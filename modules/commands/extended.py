"""Perintah OLT tambahan yang belum ada di simulator.

Modul ini murni menghasilkan teks (tanpa Qt/colorama) supaya bisa dipakai
oleh GUI (gui.dialogs.cli_terminal) maupun CLI terminal (modules.cli).

Perintah yang ditangani:
  display version
  display current-configuration [ont <f/s/p> <ont-id>]
  display ip routing-table
  display ont optical-info <port> {all | <ont-id>}
  display ont wan-info <f/s/p> <ont-id>
  ont deactivate <port> <ont-id>
  ont activate <port> <ont-id>
  ont delete <port> <ont-id>
  lldp enable [port] | lldp disable | undo lldp enable
  display lldp neighbor [port]
  display port state {all | <port>}
  display port opticstate <port>
"""

from __future__ import annotations

import hashlib
import sqlite3

from modules.database import get_connection, release_sn

VERSION_LINES = [
    "  VERSION : MA5800 V100R026C00",
    "  PATCH   : SPC100",
    "  PRODUCT : MA5800-X17",
    "  Uptime is 26 day(s), 4 hour(s), 12 minute(s), 8 second(s)",
]

DISPLAY_MODES = ("USER", "ENABLE", "CONFIG", "INTERFACE_GPON", "INTERFACE_EPON",
                 "INTERFACE_ETH", "VLANIF", "LINEPROFILE", "SRVPROFILE", "MVLAN", "BTV")


# --------------------------------------------------------------------------- #
# util
# --------------------------------------------------------------------------- #
def _query(sql, params=()):
  try:
    conn = get_connection()
    try:
      return conn.execute(sql, params).fetchall()
    finally:
      conn.close()
  except sqlite3.Error:
    return []


def _execute(sql, params=()):
  conn = get_connection()
  try:
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.rowcount
  finally:
    conn.close()


def _fmt_port(port_token, gpon_port):
  """Ubah '0' atau '0/1' atau '0/1/0' menjadi F/S/P lengkap."""
  token = (port_token or "").strip()
  if not token:
    return f"{gpon_port}/0"
  if token.count("/") == 2:
    return token
  if token.count("/") == 1:
    return f"{token}/{0}"
  base = gpon_port if gpon_port and "/" in gpon_port else "0/1"
  return f"{base}/{token}" if token.isdigit() else token


def _pseudo_power(sn, low=-21.0, high=-16.0):
  """Nilai optik deterministik dari SN (simulasi pembacaan DDM)."""
  digest = hashlib.md5((sn or "0").encode()).digest()
  ratio = int.from_bytes(digest[:2], "big") / 65535
  return round(low + (high - low) * ratio, 2)


def _ont_rows(username, fsp=None, ont_id=None):
  sql = ("SELECT port, ont_id, sn, control_flag, run_state, config_state, "
         "match_state, description, rx_power, tx_power, olt_rx_power, "
         "line_profile_id, service_profile_id FROM olt_onts WHERE username = ?")
  params = [username]
  if fsp:
    sql += " AND port = ?"
    params.append(fsp)
  if ont_id is not None:
    sql += " AND ont_id = ?"
    params.append(ont_id)
  sql += " ORDER BY port, ont_id"
  return _query(sql, tuple(params))


def _ont_by_id(username, fsp, ont_id):
  rows = _ont_rows(username, fsp, ont_id)
  return rows[0] if rows else None


# --------------------------------------------------------------------------- #
# display version
# --------------------------------------------------------------------------- #
def _version(cmd):
  return "\n".join(["", f"  Command:", f"          {cmd}", *VERSION_LINES])


# --------------------------------------------------------------------------- #
# display current-configuration
# --------------------------------------------------------------------------- #
def _current_configuration(cmd, username, node_name):
  lines = ["", "  Command:", f"          {cmd}", "#"]
  sysname = _query(
      "SELECT sysname FROM olt_node_settings WHERE username = ? AND node_name = ?",
      (username, node_name),
  )
  lines.append(f"  sysname {(sysname[0][0] if sysname else node_name) or node_name}")
  if sysname:
    lines.append("  #")

  vlans = _query(
      "SELECT vlan_id, vlan_type, bound_port, bound_tag, description FROM olt_vlan"
      " WHERE username = ? ORDER BY vlan_id",
      (username,),
  )
  if vlans:
    lines.append("#")
    for vlan_id, vlan_type, bound_port, bound_tag, desc in vlans:
      suffix = f"  description {desc}" if desc else ""
      lines.append(f"  vlan {vlan_id} {vlan_type or 'standard'}{suffix}")
      if bound_port:
        lines.append(f"  port vlan {vlan_id} {bound_port} {bound_tag or 0}")

  vlanifs = _query(
      "SELECT v.vlan_id, a.address, a.mask, a.description FROM olt_node_vlanifs v"
      " LEFT JOIN olt_node_vlanif_addresses a ON a.username = v.username"
      " AND a.node_name = v.node_name AND a.vlan_id = v.vlan_id"
      " WHERE v.username = ? AND v.node_name = ? ORDER BY v.vlan_id",
      (username, node_name),
  )
  if vlanifs:
    lines.append("#")
    for vlan_id, address, mask, desc in vlanifs:
      lines.append(f"  interface vlanif{vlan_id}")
      if address:
        lines.append(f"   ip address {address} {mask or '255.255.255.0'}")
      if desc:
        lines.append(f"   description {desc}")
      lines.append("   quit")

  routes = _query(
      "SELECT dest, mask, gateway, preference FROM olt_node_routes"
      " WHERE username = ? AND node_name = ? ORDER BY dest, mask",
      (username, node_name),
  )
  if routes:
    lines.append("#")
    for dest, mask, gateway, pref in routes:
      lines.append(
          f"  ip route-static {dest} {mask or '0.0.0.0'} {gateway}"
          f" preference {pref if pref is not None else 60}"
      )

  dba = _query(
      "SELECT profile_id, profile_name FROM olt_dba_profiles WHERE username = ?"
      " ORDER BY profile_id",
      (username,),
  )
  for pid, name in dba[:20]:
    lines.append(f"  dba-profile add profile-id {pid} profile-name {name or ('dba_' + str(pid))}")

  line_profiles = _query(
      "SELECT profile_id, profile_name FROM olt_line_profiles WHERE username = ?"
      " ORDER BY profile_id",
      (username,),
  )
  for pid, name in line_profiles:
    lines.append(f"  ont-lineprofile gpon profile-id {pid} profile-name {name or ('lp_' + str(pid))}")

  srv_profiles = _query(
      "SELECT profile_id, profile_name, eth, pots FROM olt_service_profiles"
      " WHERE username = ? ORDER BY profile_id",
      (username,),
  )
  for pid, name, eth, pots in srv_profiles:
    lines.append(f"  ont-srvprofile gpon profile-id {pid} profile-name {name or ('sp_' + str(pid))}")

  service_ports = _query(
      "SELECT sp_id, vlan, port, ont_id, gemport, user_vlan FROM olt_service_ports"
      " WHERE username = ? ORDER BY sp_id",
      (username,),
  )
  if service_ports:
    lines.append("#")
    for sp_id, vlan, port, ont_id, gemport, user_vlan in service_ports:
      lines.append(
          f"  service-port {sp_id} vlan {vlan} gpon {port} ont {ont_id}"
          f" gemport {gemport} multi-service user-vlan {user_vlan or vlan}"
          " tag-transform translate"
      )

  ont_count = _query("SELECT COUNT(*) FROM olt_onts WHERE username = ?", (username,))
  lines.append("#")
  lines.append(f"  ! total ONT terdaftar: {ont_count[0][0] if ont_count else 0}")
  lines.append("  return")
  return "\n".join(lines)


def _current_configuration_ont(cmd, username, parts):
  # display current-configuration ont 0/1/0 0
  if len(parts) < 5:
    return "\n  Command:\n          " + cmd + "\n  Failure: Use display current-configuration ont <frame/slot/port> <ont-id>."
  fsp = _fmt_port(parts[3], "0/1")
  try:
    ont_id = int(parts[4])
  except ValueError:
    return "\n  Failure: ONT ID must be an integer."
  row = _ont_by_id(username, fsp, ont_id)
  if not row:
    return f"\n  Command:\n          {cmd}\n  Failure: The ONT does not exist."
  port, oid, sn, ctrl, run, cfg, match, desc, rx, tx, olt_rx, lp, sp = row
  lines = [
      "",
      "  Command:",
      f"          {cmd}",
      "#",
      f"  interface gpon {port.rsplit('/', 1)[0] if '/' in port else '0/1'}",
      f"   ont add {oid} sn-auth {sn} omci ont-lineprofile-id {lp or 0}"
      f" ont-srvprofile-id {sp or 0} desc {desc or '-'}",
      "   quit",
      "#",
  ]
  return "\n".join(lines)


# --------------------------------------------------------------------------- #
# display ip routing-table
# --------------------------------------------------------------------------- #
def _routing_table(cmd, username, node_name):
  vlanifs = _query(
      "SELECT a.vlan_id, a.address, a.mask FROM olt_node_vlanif_addresses a"
      " WHERE a.username = ? AND a.node_name = ? ORDER BY a.vlan_id",
      (username, node_name),
  )
  routes = _query(
      "SELECT dest, mask, gateway, preference, cost FROM olt_node_routes"
      " WHERE username = ? AND node_name = ? ORDER BY dest, mask",
      (username, node_name),
  )
  rows = []
  for vlan_id, address, mask in vlanifs:
    if address:
      rows.append((f"{address}/{mask or '255.255.255.0'}", "Direct", "0", "0", "D",
                   address, f"Vlanif{vlan_id}"))
  for dest, mask, gateway, pref, cost in routes:
    rows.append((f"{dest}/{mask or '0.0.0.0'}", "Static", str(pref if pref is not None else 60),
                 str(cost or 0), "RD", gateway, "-"))
  lines = [
      "",
      "  Command:",
      f"          {cmd}",
      "  Route Flags: R - relay, D - download to fib",
      "  ------------------------------------------------------------------",
      "  Routing Tables: Public",
      f"  Destinations : {len(rows)}        Routes : {len(rows)}",
      "",
      "  Destination/Mask    Proto    Pre  Cost   Flags  NextHop       Interface",
      "  ------------------------------------------------------------------",
  ]
  for dst, proto, pref, cost, flags, nexthop, iface in rows:
    lines.append(f"  {dst:<19} {proto:<8} {pref:<4} {cost:<6} {flags:<6} {nexthop:<13} {iface}")
  if not rows:
    lines.append("  (tidak ada route terkonfigurasi)")
  lines.append("  ------------------------------------------------------------------")
  return "\n".join(lines)


# --------------------------------------------------------------------------- #
# ONT operations
# --------------------------------------------------------------------------- #
def _ont_optical(cmd, username, parts, gpon_port):
  if len(parts) < 5:
    return "\n  Command:\n          " + cmd + "\n  Failure: Use display ont optical-info <port> {all|<ont-id>}."
  fsp = _fmt_port(parts[3], gpon_port)
  selector = parts[4].lower()
  ont_id = None if selector == "all" else int(selector) if selector.isdigit() else None
  rows = _ont_rows(username, fsp, ont_id)
  header = [
      "",
      "  Command:",
      f"          {cmd}",
      "  ----------------------------------------------------------------------------------",
      "  Port       ONT-ID  Rx power(dBm)  Tx power(dBm)  OLT Rx power(dBm)  Run state",
      "  ----------------------------------------------------------------------------------",
  ]
  if not rows:
    header.append("  Failure: The ONT does not exist or no optical information is available.")
    header.append("  ----------------------------------------------------------------------------------")
    return "\n".join(header)
  if selector != "all":
    port, oid, sn, ctrl, run, cfg, match, desc, rx, tx, olt_rx, lp, sp = rows[0]
    rx = rx if rx not in (None, 0, 0.0) else _pseudo_power(sn)
    tx = tx if tx not in (None, 0, 0.0) else 2.26
    olt_rx = olt_rx if olt_rx not in (None, 0, 0.0) else round(tx - 0.6, 2)
    return "\n".join([
        "", "  Command:", f"          {cmd}",
        "  -----------------------------------------------------------------------------",
        "  ONU NNI port ID                        : 0",
        "  Module type                            : GPON",
        "  Module sub-type                        : CLASS B+",
        "  Used type                              : ONU",
        "  Encapsulation Type                     : BOSA ON BOARD",
        "  Optical power precision(dBm)           : 3.0",
        "  Vendor name                            : HUAWEI",
        "  Tx WaveLength(nm)                      : 1310",
        "  Rx WaveLength(nm)                      : 1490",
        f"  Rx optical power(dBm)                  : {rx:.2f}",
        "  Rx power current alarm threshold(dBm)  : [-29.00,-7.00]",
        f"  Tx optical power(dBm)                  : {tx:.2f}",
        "  Tx power current alarm threshold(dBm)  : [0.00,5.00]",
        "  Laser bias current(mA)                 : 8.0",
        "  Temperature(C)                         : 45",
        "  Voltage(V)                             : 3.380",
        f"  OLT Rx ONT optical power(dBm)          : {olt_rx:.2f}",
        "  -----------------------------------------------------------------------------",
    ])
  out = list(header)
  for port, oid, sn, ctrl, run, cfg, match, desc, rx, tx, olt_rx, lp, sp in rows:
    # The DB schema predates optical budget simulation and initializes these
    # fields to 0.0; treat zero as unmeasured instead of displaying it as DDM.
    rx = rx if rx not in (None, 0, 0.0) else _pseudo_power(sn)
    tx = tx if tx not in (None, 0, 0.0) else 2.26
    olt_rx = olt_rx if olt_rx not in (None, 0, 0.0) else round(tx - 0.6, 2)
    out.append(f"  {port:<10} {oid:<7} {rx:<15} {tx:<14} {olt_rx:<19} {run or 'online'}")
  out.append("  ----------------------------------------------------------------------------------")
  return "\n".join(out)


def _ont_wan_info(cmd, username, parts):
  if len(parts) < 5:
    return "\n  Command:\n          " + cmd + "\n  Failure: Use display ont wan-info <frame/slot/port> <ont-id>."
  fsp = _fmt_port(parts[3], "0/1")
  ont_id = int(parts[4]) if parts[4].isdigit() else 0
  row = _ont_by_id(username, fsp, ont_id)
  if not row:
    return f"\n  Command:\n          {cmd}\n  Failure: The ONT does not exist."
  port, oid, sn, ctrl, run, cfg, match, desc, rx, tx, olt_rx, lp, sp = row
  sp_rows = _query(
      "SELECT sp_id, vlan, user_vlan FROM olt_service_ports WHERE username = ?"
      " AND port = ? AND ont_id = ? ORDER BY sp_id",
      (username, fsp, ont_id),
  )
  base = int(hashlib.md5((sn or "0").encode()).hexdigest()[:2], 16)
  lines = [
      "",
      "  Command:",
      f"          {cmd}",
      "  ------------------------------------------------------------------",
      f"  F/S/P                : {fsp}",
      f"  ONT-ID               : {ont_id}",
      f"  Run state            : {run or 'online'}",
      "  ------------------------------------------------------------------",
      "  WAN index  IP address      Mask            Gateway        VLAN  Status",
      "  ------------------------------------------------------------------",
  ]
  if sp_rows:
    for idx, (sp_id, vlan, user_vlan) in enumerate(sp_rows, 1):
      host = (base + idx) % 250 + 2
      lines.append(
          f"  {idx:<10} 10.20.{vlan % 250}.{host:<3} 255.255.255.0   10.20.{vlan % 250}.1"
          f"    {user_vlan or vlan:<5} up"
      )
  else:
    lines.append("  (belum ada WAN/service-port yang terhubung ke ONT ini)")
  lines.append("  ------------------------------------------------------------------")
  return "\n".join(lines)


def _ont_change_state(cmd, username, parts, gpon_port, action):
  if len(parts) < 4:
    return f"\n  Command:\n          {cmd}\n  Failure: Use ont {action} <port> <ont-id>."
  fsp = _fmt_port(parts[2], gpon_port)
  try:
    ont_id = int(parts[3])
  except ValueError:
    return "\n  Failure: ONT ID must be an integer."

  row = _ont_by_id(username, fsp, ont_id)
  if not row:
    return (f"\n  Command:\n          {cmd}\n  Failure: The ONT does not exist on"
            f" {fsp}.")

  if action == "deactivate":
    _execute(
        "UPDATE olt_onts SET control_flag='deactive', run_state='offline'"
        " WHERE username=? AND port=? AND ont_id=?",
        (username, fsp, ont_id),
    )
    return (f"\n  Command:\n          {cmd}\n  ONT {fsp} {ont_id} has been"
            " deactivated.")

  if action == "activate":
    _execute(
        "UPDATE olt_onts SET control_flag='active', run_state='online',"
        " config_state='normal', match_state='match'"
        " WHERE username=? AND port=? AND ont_id=?",
        (username, fsp, ont_id),
    )
    return (f"\n  Command:\n          {cmd}\n  ONT {fsp} {ont_id} has been"
            " activated.")

  # delete
  sn = row[2]
  _execute("DELETE FROM olt_onts WHERE username=? AND port=? AND ont_id=?",
           (username, fsp, ont_id))
  if sn:
    try:
      release_sn(sn)
    except sqlite3.Error:
      pass
  return f"\n  Command:\n          {cmd}\n  The ONT is deleted successfully."


# --------------------------------------------------------------------------- #
# LLDP
# --------------------------------------------------------------------------- #
def _lldp(cmd, username, parts, lower):
  key = "lldp_enabled"
  mode = parts[0].lower() if parts else ""
  action = parts[1].lower() if len(parts) > 1 else ""

  if mode == "lldp" and action in ("enable", "disable"):
    enabled = "1" if action == "enable" else "0"
    _execute(
        "INSERT OR REPLACE INTO olt_system_config (username, key, value) VALUES (?,?,?)",
        (username, key, enabled),
    )
    if len(parts) >= 3:
      _execute(
          "INSERT OR REPLACE INTO olt_system_config (username, key, value) VALUES (?,?,?)",
          (username, f"lldp_port_{parts[2].replace('/', '_')}", enabled),
      )
    state = "enabled" if enabled == "1" else "disabled"
    return f"\n  Command:\n          {cmd}\n  LLDP is {state} globally."

  if mode == "undo" and action == "lldp" and len(parts) > 2 and parts[2].lower() == "enable":
    _execute("DELETE FROM olt_system_config WHERE username=? AND key=?", (username, key))
    return f"\n  Command:\n          {cmd}\n  LLDP is disabled globally."
  return None


def _lldp_neighbor(cmd, username, node_name, parts):
  rows = _query(
      "SELECT source_port, target_node, target_port, fiber_type FROM olt_device_links"
      " WHERE username=? AND source_node=? ORDER BY source_port",
      (username, node_name),
  )
  lines = [
      "",
      "  Command:",
      f"          {cmd}",
      "  ------------------------------------------------------------------",
      "  Local Port   Neighbor Device      Neighbor Port   Type",
      "  ------------------------------------------------------------------",
  ]
  if not rows:
    lines.append("  (tidak ada tetangga LLDP; hubungkan kabel di topologi)")
  for local, dev, remote, ftype in rows:
    lines.append(f"  {local:<12} {dev:<21} {remote:<15} {ftype or 'SMF'}")
  lines.append("  ------------------------------------------------------------------")
  return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Port state / optic
# --------------------------------------------------------------------------- #
def _port_state(cmd, username, node_name, parts):
  rows = _query(
      "SELECT source_port, target_node, target_port, fiber_type, is_up"
      " FROM olt_device_links WHERE username=? AND source_node=? ORDER BY source_port",
      (username, node_name),
  )
  lines = [
      "",
      "  Command:",
      f"          {cmd}",
      "  ------------------------------------------------------------------",
      "  Port          Status   Duplex   Speed     Type      Neighbor",
      "  ------------------------------------------------------------------",
  ]
  if rows:
    for port, dev, remote, ftype, up in rows:
      lines.append(
          f"  {port:<13} {'up' if up else 'down':<8} {'full':<8} {'10G':<9}"
          f" {(ftype or 'SMF'):<9} {dev}:{remote}"
      )
  else:
    lines.append("  (belum ada port uplink terhubung)")
  lines.append("  ------------------------------------------------------------------")
  return "\n".join(lines)


def _port_optic(cmd, username, parts):
  if len(parts) < 4:
    return "\n  Command:\n          " + cmd + "\n  Failure: Use display port opticstate <port>."
  port = parts[3]
  rows = _query(
      "SELECT sfp_module, fiber_type, length_km, is_up FROM olt_device_links"
      " WHERE source_port=? OR target_port=? LIMIT 1",
      (port, port),
  )
  if not rows:
    return (f"\n  Command:\n          {cmd}\n  Failure: No optical module is"
            f" present on port {port}.")
  module, ftype, length, up = rows[0]
  lines = [
      "",
      "  Command:",
      f"          {cmd}",
      "  ------------------------------------------------------------------",
      f"  Port                        : {port}",
      f"  Module type                 : {module or 'SFP+ -10G-1310nm-SMF-10km'}",
      f"  Fiber type                  : {ftype or 'SMF'}",
      f"  Transmission distance(km)   : {length if length is not None else 10}",
      f"  Port status                 : {'up' if up else 'down'}",
      "  Temperature(C)              : 41",
      "  Voltage(V)                  : 3.29",
      "  Bias current(mA)            : 6.12",
      "  TX power(dBm)               : -2.15",
      "  RX power(dBm)               : -3.42",
      "  ------------------------------------------------------------------",
  ]
  return "\n".join(lines)


# --------------------------------------------------------------------------- #
# dispatcher
# --------------------------------------------------------------------------- #
def dispatch(username, node_name, command, cli_state="ENABLE", gpon_port="0/1"):
  """Kembalikan teks hasil perintah, atau None bila bukan perintah modul ini."""
  if not command or not command.strip():
    return None
  cmd = " ".join(command.strip().split())
  lower = cmd.lower()
  parts = cmd.split()

  if lower == "display version":
    return _version(cmd)

  if lower.startswith("display current-configuration ont"):
    return _current_configuration_ont(cmd, username, parts)
  if lower == "display current-configuration":
    return _current_configuration(cmd, username, node_name)

  if lower in ("display ip routing-table", "display ip routing-table all"):
    return _routing_table(cmd, username, node_name)

  if lower.startswith("display ont optical-info"):
    return _ont_optical(cmd, username, parts, gpon_port)
  if lower.startswith("display ont wan-info"):
    return _ont_wan_info(cmd, username, parts)

  if lower.startswith("ont deactivate"):
    return _ont_change_state(cmd, username, parts, gpon_port, "deactivate")
  if lower.startswith("ont activate"):
    return _ont_change_state(cmd, username, parts, gpon_port, "activate")
  if lower.startswith("ont delete"):
    return _ont_change_state(cmd, username, parts, gpon_port, "delete")

  if lower.startswith("lldp ") or lower.startswith("undo lldp"):
    result = _lldp(cmd, username, parts, lower)
    if result is not None:
      return result
  if lower.startswith("display lldp neighbor"):
    return _lldp_neighbor(cmd, username, node_name, parts)

  if lower.startswith("display port state"):
    return _port_state(cmd, username, node_name, parts)
  if lower.startswith("display port opticstate"):
    return _port_optic(cmd, username, parts)

  return None
