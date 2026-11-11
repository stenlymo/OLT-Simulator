"""WAC/Fit AP and wireless station simulation for Datacom lab topologies."""

import hashlib
import ipaddress
import json
import re
from datetime import datetime

from modules.database import get_connection


def ensure_schema(conn=None):
  own = conn is None
  conn = conn or get_connection()
  try:
    conn.execute("""CREATE TABLE IF NOT EXISTS olt_wlan_leases (
      username TEXT NOT NULL, wac_node TEXT NOT NULL, vlanif TEXT NOT NULL,
      client_node TEXT NOT NULL, client_mac TEXT NOT NULL, ip_address TEXT NOT NULL,
      client_kind TEXT NOT NULL, ap_node TEXT NOT NULL DEFAULT '', ssid TEXT NOT NULL DEFAULT '',
      connected_at TEXT NOT NULL, dhcp_node TEXT NOT NULL DEFAULT '',
      PRIMARY KEY(username,wac_node,vlanif,client_mac))""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(olt_wlan_leases)")}
    if "dhcp_node" not in columns:
      conn.execute("ALTER TABLE olt_wlan_leases ADD COLUMN dhcp_node TEXT NOT NULL DEFAULT ''")
      conn.execute("UPDATE olt_wlan_leases SET dhcp_node=wac_node WHERE dhcp_node=''")
    if own:
      conn.commit()
  finally:
    if own:
      conn.close()


def normalize_mac(value):
  return re.sub(r"[^0-9a-f]", "", str(value or "").lower())


def mac_for_node(node_name, prefix="c0a9"):
  digest = hashlib.sha256(str(node_name).encode("utf-8")).hexdigest()[:8]
  raw = (prefix + digest).lower()[:12]
  return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}"


def _load_config(conn, username, node_name):
  row = conn.execute("SELECT device_type,running_json FROM olt_datacom_devices WHERE username=? AND node_name=?",
                     (username, node_name)).fetchone()
  if not row:
    return None, None
  try:
    return row[0], json.loads(row[1] or "{}")
  except (TypeError, ValueError):
    return row[0], {}


def _vlanif(config, vlan):
  name = f"Vlanif{int(vlan)}"
  iface = (config.get("interfaces") or {}).get(name)
  if not iface or not iface.get("ip") or not iface.get("mask"):
    return name, None
  try:
    network = ipaddress.IPv4Network(f"{iface['ip']}/{iface['mask']}", strict=False)
  except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
    return name, None
  return name, (iface, network)


def _allocate_interface_lease(conn, username, wac_node, dhcp_node, vlanif, client_node, client_mac, kind, ap_node="", ssid=""):
  ensure_schema(conn)
  _kind, config = _load_config(conn, username, dhcp_node)
  if config is None:
    return None
  iface = (config.get("interfaces") or {}).get(vlanif, {})
  if not config.get("dhcp_enabled") or iface.get("dhcp_select") != "interface":
    return None
  try:
    gateway = ipaddress.IPv4Address(iface.get("dhcp_server_gateway") or iface["ip"])
    network = ipaddress.IPv4Network(f"{gateway}/{iface.get('mask') or '255.255.255.0'}", strict=False)
  except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
    return None
  client_mac = normalize_mac(client_mac)
  existing = conn.execute("SELECT ip_address FROM olt_wlan_leases WHERE username=? AND dhcp_node=? AND vlanif=? AND client_mac=?",
                           (username, dhcp_node, vlanif, client_mac)).fetchone()
  if existing and ipaddress.IPv4Address(existing[0]) in network:
    address = existing[0]
  else:
    occupied = {str(gateway)}
    occupied.update(row[0] for row in conn.execute(
        "SELECT ip_address FROM olt_wlan_leases WHERE username=? AND dhcp_node=? AND vlanif=?",
        (username, dhcp_node, vlanif)).fetchall())
    server_iface = (config.get("interfaces") or {}).get(vlanif, {})
    occupied.update(str(info.get("ip")) for info in server_iface.get("dhcp_leases", {}).values() if isinstance(info, dict) and info.get("ip"))
    occupied.update(str(value) for value in server_iface.get("dhcp_bindings", {}).values())
    address = next((str(candidate) for candidate in network.hosts() if str(candidate) not in occupied), None)
    if address is None:
      return None
  conn.execute("INSERT OR REPLACE INTO olt_wlan_leases "
               "(username,wac_node,vlanif,client_node,client_mac,ip_address,client_kind,ap_node,ssid,connected_at,dhcp_node) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (username, wac_node, vlanif, client_node, client_mac, address, kind, ap_node, ssid,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), dhcp_node))
  return {"ip": address, "mask": str(network.netmask), "gateway": str(gateway), "network": str(network), "vlanif": vlanif, "dhcp_node": dhcp_node}


def _ap_registration(config, mac):
  wlan = config.get("wlan") or {}
  key = normalize_mac(mac)
  for ap_id, ap in (wlan.get("aps") or {}).items():
    if normalize_mac(ap.get("mac_address")) == key:
      return str(ap_id), ap
  return None, None


def _ap_vlan_path(conn, username, ap_name, server_node, vlan):
  from modules.datacom_network import _topology_path_exists, path_allows_vlan
  if not _topology_path_exists(conn, username, ap_name, server_node):
    return False
  reachable, checked = path_allows_vlan(conn, username, ap_name, server_node, int(vlan))
  return reachable if checked else True


def _ap_management_vlan(conn, username, ap_node, fallback):
  row = conn.execute("SELECT source_node,target_node,source_port,target_port FROM olt_device_links WHERE username=? AND (source_node=? OR target_node=?) LIMIT 1",
                     (username, ap_node, ap_node)).fetchone()
  if not row:
    return fallback
  peer = row[1] if row[0] == ap_node else row[0]
  port = row[3] if row[0] == ap_node else row[2]
  _kind, config = _load_config(conn, username, peer)
  iface = (config or {}).get("interfaces", {}).get(port, {})
  if iface.get("link_type") == "access":
    return int(iface.get("pvid", 1))
  if iface.get("link_type") == "trunk":
    return int(iface.get("pvid", 1))
  if iface.get("link_type") == "hybrid":
    return int(iface.get("pvid", 1))
  return fallback


def _wac_source(config):
  wlan = config.get("wlan") or {}
  source = re.sub(r"\s+", "", wlan.get("capwap_source_interface", "")).lower()
  iface = next((value for name, value in (config.get("interfaces") or {}).items()
                if name.lower().replace(" ", "") == source), None)
  return iface


def _find_dhcp_scope(conn, username, source_node, vlan, wac_node=None, wac_ip=None):
  """Locate an interface-pool DHCP server reachable on the requested VLAN."""
  rows = conn.execute("SELECT node_name,running_json FROM olt_datacom_devices WHERE username=?", (username,)).fetchall()
  for server_node, raw in rows:
    try:
      config = json.loads(raw or "{}")
    except (TypeError, ValueError):
      continue
    if not config.get("dhcp_enabled"):
      continue
    for ifname, iface in (config.get("interfaces") or {}).items():
      if not iface.get("is_vlanif") or int(iface.get("vlan_id") or 0) != int(vlan) or iface.get("dhcp_select") != "interface":
        continue
      if not _ap_vlan_path(conn, username, source_node, server_node, vlan):
        continue
      if wac_node and wac_ip:
        option43 = iface.get("dhcp_option_43", "")
        if option43 and option43 != wac_ip:
          continue
        try:
          network = ipaddress.IPv4Network(f"{iface['ip']}/{iface.get('mask', '255.255.255.0')}", strict=False)
        except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
          continue
        if not option43 and ipaddress.IPv4Address(wac_ip) not in network:
          continue
        from modules.datacom_network import routed_ping_reachable
        if wac_node != server_node and not routed_ping_reachable(
            conn, username, server_node, wac_node, wac_ip, source_address=iface.get("ip")):
          continue
      return server_node, ifname, iface
  return None


def access_point_status(conn, username, ap_node, wac_node=None):
  """Return controller registration/online state for one Fit AP topology node."""
  ap_config = getattr(ap_node, "ap_config", {}) or {}
  mac = ap_config.get("mac_address") or mac_for_node(ap_node.device_name)
  rows = conn.execute("SELECT node_name,running_json FROM olt_datacom_devices WHERE username=?",
                      (username,)).fetchall()
  candidates = []
  for name, raw in rows:
    try:
      config = json.loads(raw or "{}")
    except (TypeError, ValueError):
      continue
    wlan = config.get("wlan") or {}
    ap_id, registration = _ap_registration(config, mac)
    if registration and (wac_node is None or name == wac_node):
      candidates.append((name, config, ap_id, registration))
  if not candidates:
    return {"state": "unauth", "reason": "AP MAC is not registered on a WAC", "mac": mac, "ip": "", "wac": "", "ap_id": "-", "group": "-"}
  for name, config, ap_id, registration in candidates:
    wlan = config.get("wlan") or {}
    capwap = wlan.get("capwap_source_interface", "")
    source_iface = _wac_source(config)
    if not source_iface or not source_iface.get("ip"):
      continue
    wac_vlan = source_iface.get("vlan_id") or re.sub(r"\D", "", wlan.get("capwap_source_interface", ""))
    if not wac_vlan:
      continue
    mgmt_vlan = _ap_management_vlan(conn, username, ap_node.device_name, int(wac_vlan))
    scope = _find_dhcp_scope(conn, username, ap_node.device_name, mgmt_vlan, name, source_iface["ip"])
    if not scope:
      continue
    dhcp_node, dhcp_vlanif, _dhcp_iface = scope
    lease = _allocate_interface_lease(conn, username, name, dhcp_node, dhcp_vlanif,
                                      ap_node.device_name, mac, "ap", ap_node.device_name)
    if not lease:
      continue
    group = registration.get("ap_group", "default")
    group_cfg = (wlan.get("ap_groups") or {}).get(group)
    if not group_cfg:
      continue
    ap_config.update({"mac_address": mac, "management_ip": lease["ip"], "online_state": "nor",
                      "wac_name": name, "ap_id": ap_id, "ap_group": group})
    ap_node.update()
    conn.commit()
    return {"state": "nor", "reason": "", "mac": mac, "ip": lease["ip"], "wac": name, "ap_id": ap_id, "group": group,
            "config": config, "registration": registration}
  return {"state": "fault", "reason": "CAPWAP source, management VLAN path, or interface DHCP is unavailable",
          "mac": mac, "ip": "", "wac": candidates[0][0], "ap_id": candidates[0][2],
          "group": candidates[0][3].get("ap_group", "default"), "config": candidates[0][1], "registration": candidates[0][3]}


def _published_networks(conn, username, ap_node, config, ap_registration):
  wlan = config.get("wlan") or {}
  group = (wlan.get("ap_groups") or {}).get(ap_registration.get("ap_group", "default"), {})
  results = []
  for binding in group.get("vap_bindings", []):
    vap_name = binding.get("vap_profile")
    vap = (wlan.get("vap_profiles") or {}).get(vap_name, {})
    ssid_name = vap.get("ssid_profile")
    security_name = vap.get("security_profile")
    ssid_profile = (wlan.get("ssid_profiles") or {}).get(ssid_name, {})
    security = (wlan.get("security_profiles") or {}).get(security_name, {})
    ssid = ssid_profile.get("ssid", "")
    service_vlan = int(vap.get("service_vlan", 0) or 0)
    if not ssid or not security.get("passphrase") or not service_vlan:
      continue
    scope = _find_dhcp_scope(conn, username, ap_node, service_vlan)
    if not scope:
      continue
    results.append({"ssid": ssid, "security": security, "service_vlan": service_vlan,
                    "wac": ap_registration.get("wac_name", ""), "vap_profile": vap_name,
                    "security_profile": security_name, "ssid_profile": ssid_name,
                    "forward_mode": vap.get("forward_mode", "direct-forward"),
                    "dhcp_node": scope[0], "dhcp_vlanif": scope[1]})
  return results


def scan_networks(conn, username, scene):
  networks = []
  if scene is None:
    return networks
  for item in scene.items():
    if "Fit AP" not in getattr(item, "device_type", ""):
      continue
    status = access_point_status(conn, username, item)
    if status.get("state") != "nor":
      continue
    for network in _published_networks(conn, username, item.device_name, status["config"], {**status["registration"], "wac_name": status["wac"]}):
      networks.append({**network, "ap_node": item.device_name, "ap_name": status["registration"].get("ap_name") or item.display_name,
                       "ap_id": status["ap_id"], "bssid": status["mac"]})
  conn.commit()
  return networks


def connect_station(conn, username, pc_node, pc_mac, ap_node, ssid, passphrase, scene):
  networks = scan_networks(conn, username, scene)
  selected = next((row for row in networks if row["ap_node"] == ap_node and row["ssid"] == ssid), None)
  if not selected:
    return {"ok": False, "message": "SSID tidak ditemukan pada AP online atau DHCP VLAN layanan belum aktif."}
  required_password = selected["security"].get("passphrase", "")
  if required_password and passphrase != required_password:
    return {"ok": False, "message": "Password WLAN salah."}
  lease = _allocate_interface_lease(conn, username, selected["wac"], selected["dhcp_node"], selected["dhcp_vlanif"],
                                    pc_node, pc_mac, "station", ap_node, ssid)
  if not lease:
    return {"ok": False, "message": "DHCP interface pool VLAN layanan tidak tersedia atau kehabisan alamat."}
  if not _ap_vlan_path(conn, username, ap_node, selected["dhcp_node"], selected["service_vlan"]):
    conn.execute("DELETE FROM olt_wlan_leases WHERE username=? AND wac_node=? AND client_mac=? AND client_kind='station'",
                 (username, selected["wac"], normalize_mac(pc_mac)))
    conn.commit()
    return {"ok": False, "message": f"VLAN layanan {selected['service_vlan']} belum melewati trunk dari AP ke WAC."}
  conn.commit()
  return {"ok": True, **lease, "ssid": ssid, "ap_node": ap_node, "ap_name": selected["ap_name"],
          "wac": selected["wac"], "service_vlan": selected["service_vlan"], "mac": normalize_mac(pc_mac)}


def station_info(conn, username, pc_node):
  ensure_schema(conn)
  row = conn.execute("SELECT wac_node,vlanif,client_mac,ip_address,ap_node,ssid,connected_at,dhcp_node FROM olt_wlan_leases "
                     "WHERE username=? AND client_node=? AND client_kind='station' LIMIT 1",
                     (username, pc_node)).fetchone()
  if not row:
    return None
  return {"wac": row[0], "vlanif": row[1], "mac": row[2], "ip": row[3], "ap_node": row[4], "ssid": row[5], "connected_at": row[6], "dhcp_node": row[7] or row[0]}


def disconnect_station(conn, username, pc_node):
  ensure_schema(conn)
  conn.execute("DELETE FROM olt_wlan_leases WHERE username=? AND client_node=? AND client_kind='station'",
               (username, pc_node))
  conn.commit()


def station_rows(conn, username, wac_node):
  ensure_schema(conn)
  return conn.execute("SELECT client_node,client_mac,ip_address,ap_node,ssid,vlanif,connected_at FROM olt_wlan_leases "
                       "WHERE username=? AND wac_node=? AND client_kind='station' ORDER BY client_node",
                       (username, wac_node)).fetchall()


def station_ping(conn, username, pc_node, destination):
  station = station_info(conn, username, pc_node)
  if not station:
    return None
  try:
    target = ipaddress.IPv4Address(destination)
  except ipaddress.AddressValueError:
    return False
  _kind, config = _load_config(conn, username, station["dhcp_node"])
  interface = (config.get("interfaces") or {}).get(station["vlanif"], {}) if config else {}
  gateway = interface.get("dhcp_server_gateway") or interface.get("ip")
  if gateway == str(target):
    return True
  peers = conn.execute("SELECT ip_address FROM olt_wlan_leases WHERE username=? AND dhcp_node=? AND vlanif=? AND client_kind='station'",
                       (username, station["dhcp_node"], station["vlanif"])).fetchall()
  if str(target) in {row[0] for row in peers}:
    return True
  rows = conn.execute("SELECT node_name,running_json FROM olt_datacom_devices WHERE username=?", (username,)).fetchall()
  owner = None
  for name, raw in rows:
    try:
      cfg = json.loads(raw or "{}")
    except (TypeError, ValueError):
      continue
    if any(iface.get("ip") == str(target) for iface in (cfg.get("interfaces") or {}).values()):
      owner = name
      break
  if owner is None:
    return False
  from modules.datacom_network import routed_ping_reachable
  return routed_ping_reachable(conn, username, station["dhcp_node"], owner, str(target), source_address=station["ip"])


def acquire_wired_pc(conn, username, pc_node, pc_mac, scene):
  """Acquire an interface-pool DHCP lease for a PC connected to a switch access VLAN."""
  ensure_schema(conn)
  vlan = None
  for link in getattr(pc_node, "links", []):
    if not link.is_up:
      continue
    peer = link.target_node if link.source_node is pc_node else link.source_node
    peer_port = link.target_port if link.target_node is peer else link.source_port
    _kind, config = _load_config(conn, username, peer.device_name)
    iface = (config or {}).get("interfaces", {}).get(peer_port, {})
    if iface.get("link_type") in ("access", "trunk", "hybrid"):
      vlan = int(iface.get("pvid", 1))
      break
  if vlan is None:
    return {"ok": False, "message": "PC harus terhubung ke switch port access/trunk yang aktif."}
  scope = _find_dhcp_scope(conn, username, pc_node.device_name, vlan)
  if not scope:
    return {"ok": False, "message": f"Tidak ada interface DHCP pool yang dapat dicapai pada VLAN {vlan}."}
  server_node, server_vlanif, _iface = scope
  lease = _allocate_interface_lease(conn, username, "", server_node, server_vlanif,
                                    pc_node.device_name, pc_mac, "wired")
  if not lease:
    return {"ok": False, "message": "Pool DHCP VLAN kabel belum aktif atau kehabisan alamat."}
  conn.commit()
  return {"ok": True, **lease, "vlan": vlan}


def wired_pc_info(conn, username, pc_node):
  ensure_schema(conn)
  row = conn.execute("SELECT dhcp_node,vlanif,client_mac,ip_address FROM olt_wlan_leases WHERE username=? AND client_node=? AND client_kind='wired' LIMIT 1",
                     (username, pc_node)).fetchone()
  if not row:
    return None
  return {"dhcp_node": row[0], "vlanif": row[1], "mac": row[2], "ip": row[3]}


def release_pc(conn, username, pc_node):
  ensure_schema(conn)
  conn.execute("DELETE FROM olt_wlan_leases WHERE username=? AND client_node=? AND client_kind='wired'", (username, pc_node))
  conn.commit()
