"""Stateful DHCP simulation for the Huawei Datacom lab devices."""
import hashlib
import ipaddress
import json
import re


def interface_mac(node_name, interface):
  """Return a repeatable locally administered MAC address for a simulated port."""
  digest = hashlib.md5(f"{node_name}:{interface}".encode("utf-8")).hexdigest()[:12]
  first = (int(digest[:2], 16) & 0xFC) | 0x02
  return f"{first:02x}{digest[2:4]}-{digest[4:8]}-{digest[8:12]}"


def normalize_mac(value):
  return re.sub(r"[^0-9a-f]", "", str(value or "").lower())


def _load_devices(conn, username):
  devices = {}
  for name, kind, raw in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (username,)
  ).fetchall():
    try:
      devices[name] = {"kind": kind or "", "config": json.loads(raw)}
    except (TypeError, ValueError):
      continue
  return devices


def _save_devices(conn, username, devices, *names):
  for name in set(names):
    device = devices.get(name)
    if device:
      conn.execute(
          "UPDATE olt_datacom_devices SET running_json=? WHERE username=? AND node_name=?",
          (json.dumps(device["config"]), username, name),
      )


def _network(value, mask):
  return ipaddress.IPv4Network(f"{value}/{mask}", strict=False)


def _path_exists(conn, username, source, target):
  from modules.datacom_network import _topology_path_exists
  return _topology_path_exists(conn, username, source, target)


def _same_l2_domain(conn, username, devices, source, target):
  from modules.datacom_network import datacom_topology_links, stp_blocked_link_keys
  links, _info = datacom_topology_links(conn, username)
  blocked = stp_blocked_link_keys(conn, username)
  graph = {}
  for a, b, ap, bp, up in links:
    if not up or tuple(sorted(((a, ap), (b, bp)))) in blocked:
      continue
    graph.setdefault(a, set()).add(b)
    graph.setdefault(b, set()).add(a)
  def is_router(name):
    return "router" in (devices.get(name, {}).get("kind", "")).lower()
  queue, seen = [source], {source}
  while queue:
    current = queue.pop(0)
    for peer in graph.get(current, ()):
      if peer == target:
        return True
      if peer in seen or is_router(peer):
        continue
      seen.add(peer)
      queue.append(peer)
  return False


def _same_l2_vlan(conn, username, source, target, vlan):
  """Check a DHCP broadcast domain across all switch hops, including VLAN tags."""
  if not _same_l2_domain(conn, username, _load_devices(conn, username), source, target):
    return False
  from modules.datacom_network import path_allows_vlan
  reachable, checked = path_allows_vlan(conn, username, source, target, int(vlan))
  return reachable if checked else _same_l2_domain(conn, username, _load_devices(conn, username), source, target)


def _pool_network(pool):
  try:
    return _network(pool["network"], pool["mask"])
  except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
    return None


def _allocate(pool, mac, client_name, client_port, server_interface_ip):
  network = _pool_network(pool)
  if network is None:
    return None
  bindings = pool.setdefault("bindings", {})
  previous = pool.setdefault("leases", {}).get(mac)
  binding = next((ip for bound_mac, ip in bindings.items() if normalize_mac(bound_mac) == mac), None)
  if previous and previous.get("ip") and (not binding or previous.get("ip") == binding) and ipaddress.IPv4Address(previous["ip"]) in network:
    previous.update(client=client_name, interface=client_port, state="Bound")
    return previous["ip"], network
  occupied = {str(ipaddress.IPv4Address(value)) for value in bindings.values() if _valid_ip(value)}
  occupied.update(str(info.get("ip")) for lease_mac, info in pool["leases"].items()
                  if lease_mac != mac and info.get("ip"))
  if pool.get("gateway"):
    occupied.add(str(pool["gateway"]))
  if server_interface_ip:
    occupied.add(str(server_interface_ip))
  if binding:
    try:
      address = ipaddress.IPv4Address(binding)
    except ipaddress.AddressValueError:
      return None
    if address not in network or (str(address) in occupied and not any(
        normalize_mac(k) == mac and v == str(address) for k, v in bindings.items()
    )):
      return None
  else:
    address = next((candidate for candidate in network.hosts() if str(candidate) not in occupied), None)
    if address is None:
      return None
  pool["leases"][mac] = {"ip": str(address), "client": client_name, "interface": client_port, "state": "Bound"}
  return str(address), network


def _valid_ip(value):
  try:
    ipaddress.IPv4Address(value)
    return True
  except (ipaddress.AddressValueError, ValueError):
    return False


def _client_server_candidate(conn, username, devices, client_node, client_port, client_iface):
  """Resolve a local DHCP server or a configured relay/server path for a client."""
  client_mac = normalize_mac(interface_mac(client_node, client_port))
  client_address = client_iface.get("dhcp_requested_address")
  relays = []
  for relay_name, relay in devices.items():
    rcfg = relay["config"]
    for relay_port, relay_iface in (rcfg.get("interfaces") or {}).items():
      if relay_iface.get("dhcp_select") != "relay" or not relay_iface.get("dhcp_relay_server_ip"):
        continue
      if relay_name == client_node or not _same_l2_domain(conn, username, devices, client_node, relay_name):
        continue
      try:
        relay_network = _network(relay_iface["ip"], relay_iface.get("mask", "255.255.255.0"))
      except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
        continue
      for server_name, server in devices.items():
        scfg = server["config"]
        if not scfg.get("dhcp_enabled") or relay_iface["dhcp_relay_server_ip"] not in {
            iface.get("ip") for iface in (scfg.get("interfaces") or {}).values()
        }:
          continue
        from modules.datacom_network import routed_ping_reachable
        # The relay server must be reachable, and the server must have a
        # return route to the relay interface (the DHCP giaddr subnet).
        if not routed_ping_reachable(
            conn, username, relay_name, server_name,
            relay_iface["dhcp_relay_server_ip"], source_address=relay_iface.get("ip"),
        ):
          continue
        server_port, server_iface = next(
            (port, iface) for port, iface in (scfg.get("interfaces") or {}).items()
            if iface.get("ip") == relay_iface["dhcp_relay_server_ip"]
        )
        for pool_name, pool in (scfg.get("dhcp_pools") or {}).items():
          if server_iface.get("dhcp_select") != "global" or not scfg.get("dhcp_enabled"):
            continue
          if _pool_network(pool) == relay_network:
            relays.append((server_name, server_port, server_iface, pool_name, pool, relay_name, relay_port))

  if relays:
    return relays[0], client_mac

  # Without a relay, the DHCP client and DHCP server interface must share a broadcast domain.
  for server_name, server in devices.items():
    scfg = server["config"]
    if not scfg.get("dhcp_enabled") or server_name == client_node:
      continue
    if not _same_l2_domain(conn, username, devices, client_node, server_name):
      continue
    for server_port, server_iface in (scfg.get("interfaces") or {}).items():
      if server_iface.get("dhcp_select") != "global" or not server_iface.get("ip"):
        continue
      for pool_name, pool in (scfg.get("dhcp_pools") or {}).items():
        network = _pool_network(pool)
        if network and ipaddress.IPv4Address(server_iface["ip"]) in network:
          return (server_name, server_port, server_iface, pool_name, pool, None, None), client_mac

  # Huawei interface address pools are bound directly to a routed VLANIF.
  # Their DHCP clients must be on the same VLAN broadcast domain.
  client_vlan = client_iface.get("vlan_id") if client_iface.get("is_vlanif") else None
  if client_vlan is None:
    client_vlan = client_iface.get("pvid")
  if client_vlan is None:
    links, _ = __import__("modules.datacom_network", fromlist=["datacom_topology_links"]).datacom_topology_links(conn, username)
    client_vlan = next((int((devices.get(peer, {}).get("config", {}).get("interfaces") or {}).get(peer_port, {}).get("pvid", 1))
                        for a, b, ap, bp, up in links if up
                        for peer, local_port, peer_port in ((b, ap, bp), (a, bp, ap))
                        if peer == client_node and local_port == client_port), 1)
  for server_name, server in devices.items():
    scfg = server["config"]
    if not scfg.get("dhcp_enabled") or server_name == client_node:
      continue
    for server_port, server_iface in (scfg.get("interfaces") or {}).items():
      if server_iface.get("dhcp_select") != "interface" or not server_iface.get("ip"):
        continue
      server_vlan = server_iface.get("vlan_id")
      if not server_iface.get("is_vlanif") or int(server_vlan or 0) != int(client_vlan or 0):
        continue
      if not _same_l2_vlan(conn, username, client_node, server_name, int(server_vlan)):
        continue
      try:
        network = _network(server_iface["ip"], server_iface.get("mask", "255.255.255.0"))
      except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
        continue
      lease_map = server_iface.setdefault("dhcp_leases", {})
      binding_map = server_iface.setdefault("dhcp_bindings", {})
      pool = {"network": str(network.network_address), "mask": str(network.netmask),
              "gateway": server_iface.get("dhcp_server_gateway") or server_iface.get("ip"),
              "dns": server_iface.get("dhcp_server_dns", ""), "leases": lease_map,
              "bindings": binding_map}
      return (server_name, server_port, server_iface, server_port, pool, None, None), client_mac
  return None, client_mac


def acquire_dhcp_client(conn, username, client_node, client_port):
  """Attempt a DHCP allocation and persist the client and server lease states."""
  devices = _load_devices(conn, username)
  client = devices.get(client_node)
  if not client:
    return False, "Error: Datacom device configuration is unavailable."
  cfg = client["config"]
  iface = (cfg.get("interfaces") or {}).get(client_port)
  if iface is None:
    return False, "Error: The client interface does not exist."
  iface["dhcp_mode"] = "client"
  iface["ip"] = ""
  iface["mask"] = ""
  iface.pop("dhcp_lease", None)
  candidate, mac = _client_server_candidate(conn, username, devices, client_node, client_port, iface)
  if candidate is None:
    _save_devices(conn, username, devices, client_node)
    conn.commit()
    return False, "Info: DHCP client is configured; no reachable server or relay is available yet."
  server_name, server_port, server_iface, pool_name, pool, relay_name, relay_port = candidate
  scfg = devices[server_name]["config"]
  allocation = _allocate(pool, mac, client_node, client_port, server_iface.get("ip"))
  if allocation is None:
    _save_devices(conn, username, devices, client_node, server_name)
    conn.commit()
    return False, f"Failure: No available address in DHCP pool {pool_name}."
  address, network = allocation
  lease = {"ip": address, "mask": str(network.netmask), "gateway": pool.get("gateway", ""),
           "dns": pool.get("dns", ""), "server": server_iface.get("ip", ""),
           "pool": pool_name, "mac": mac, "state": "Bound", "relay": relay_name or ""}
  iface["ip"], iface["mask"] = address, str(network.netmask)
  iface["dhcp_gateway"] = lease["gateway"]
  iface["dhcp_dns"] = lease["dns"]
  iface["dhcp_lease"] = lease
  _save_devices(conn, username, devices, client_node, server_name)
  conn.commit()
  return True, f"Info: DHCP address {address}/{network.prefixlen} was allocated to {client_port} from {server_name}:{pool_name}."


def renew_clients(conn, username):
  """Retry DHCP clients after server/relay configuration changes."""
  devices = _load_devices(conn, username)
  clients = [(name, port) for name, item in devices.items()
             for port, iface in (item["config"].get("interfaces") or {}).items()
             if iface.get("dhcp_mode") == "client"]
  results = []
  for node_name, port in clients:
    success, message = acquire_dhcp_client(conn, username, node_name, port)
    results.append((node_name, port, success, message))
  return results
