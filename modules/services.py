"""Layanan simulasi: DHCP server, BRAS/PPPoE, dan WAN ONT.

Alur: ONT dikonfigurasi PPPoE (user/password/VLAN) -> dilakukan connect ->
kredensial dicek ke akun BRAS -> bila cocok, ONT mendapat IP dari pool DHCP
server yang terpasang di topologi.
"""

from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
from datetime import datetime

from modules.database import get_connection

SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_dhcp_pools (
  username TEXT NOT NULL,
  server_node TEXT NOT NULL,
  pool_name TEXT NOT NULL DEFAULT 'POOL1',
  start_ip TEXT NOT NULL,
  end_ip TEXT NOT NULL,
  gateway TEXT NOT NULL DEFAULT '',
  dns TEXT NOT NULL DEFAULT '',
  vlan INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (username, server_node)
);
CREATE TABLE IF NOT EXISTS sim_bras_users (
  username TEXT NOT NULL,
  bras_node TEXT NOT NULL,
  pppoe_user TEXT NOT NULL,
  pppoe_pass TEXT NOT NULL,
  profile TEXT NOT NULL DEFAULT 'default',
  vlan INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (username, bras_node, pppoe_user)
);
CREATE TABLE IF NOT EXISTS sim_service_nodes (
  username TEXT NOT NULL,
  node_name TEXT NOT NULL,
  service_type TEXT NOT NULL DEFAULT '',
  local_ip TEXT NOT NULL DEFAULT '',
  settings_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (username, node_name)
);
CREATE TABLE IF NOT EXISTS sim_pc_wan (
  username TEXT NOT NULL,
  node_name TEXT NOT NULL,
  pppoe_user TEXT NOT NULL DEFAULT '',
  pppoe_pass TEXT NOT NULL DEFAULT '',
  ip TEXT NOT NULL DEFAULT '',
  vlan INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'disconnected',
  updated TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (username, node_name)
);
CREATE TABLE IF NOT EXISTS sim_leases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL,
  server_node TEXT NOT NULL,
  ip TEXT NOT NULL,
  ont_name TEXT NOT NULL DEFAULT '',
  mac TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'active',
  started_at TEXT NOT NULL DEFAULT '',
  circuit_id TEXT NOT NULL DEFAULT '',
  remote_id TEXT NOT NULL DEFAULT '',
  option82 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sim_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL,
  bras_node TEXT NOT NULL,
  pppoe_user TEXT NOT NULL,
  ont_name TEXT NOT NULL DEFAULT '',
  ip TEXT NOT NULL DEFAULT '',
  vlan INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'up',
  started_at TEXT NOT NULL DEFAULT ''
);
"""

ONT_COLUMNS = [
  ("pppoe_user", "TEXT"),
  ("pppoe_pass", "TEXT"),
  ("wan_vlan", "INTEGER"),
  ("wan_ip", "TEXT"),
  ("wan_status", "TEXT"),
  ("wan_updated", "TEXT"),
  ("wan_mode", "TEXT DEFAULT 'pppoe'"),
  ("mac_address", "TEXT"),
]

LEASE_COLUMNS = [
  ("circuit_id", "TEXT DEFAULT ''"),
  ("remote_id", "TEXT DEFAULT ''"),
  ("option82", "TEXT DEFAULT ''"),
]


def ensure_schema():
  conn = get_connection()
  try:
    conn.executescript(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(olt_onts)")}
    for column, ctype in ONT_COLUMNS:
      if column not in existing:
        conn.execute(f"ALTER TABLE olt_onts ADD COLUMN {column} {ctype}")
    lease_columns = {row[1] for row in conn.execute("PRAGMA table_info(sim_leases)")}
    for column, ctype in LEASE_COLUMNS:
      if column not in lease_columns:
        conn.execute(f"ALTER TABLE sim_leases ADD COLUMN {column} {ctype}")
    conn.commit()
  except sqlite3.Error:
    pass
  finally:
    conn.close()


ensure_schema()


def _rows(conn, sql, params=()):
  conn.row_factory = sqlite3.Row
  return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _topology_reachable(conn, username, olt_node, ont_name):
  """True bila ONT terhubung ke OLT lewat link yang UP (atau topologi kosong)."""
  if not ont_name:
    return True
  links = conn.execute(
      "SELECT source_node, target_node, source_port, target_port, is_up"
      " FROM olt_device_links WHERE username = ?",
      (username,),
  ).fetchall()
  if not links:
    return True
  adjacency = {}
  for source, target, source_port, target_port, is_up in links:
    if not is_up:
      continue
    adjacency.setdefault(source, []).append(target)
    adjacency.setdefault(target, []).append(source)
  if not adjacency:
    return False
  seen, queue = set(), [olt_node]
  while queue:
    node = queue.pop()
    if node in seen:
      continue
    seen.add(node)
    for neighbor in adjacency.get(node, []):
      if neighbor not in seen:
        queue.append(neighbor)
  return ont_name in seen


def _connected_to_any_olt(conn, username, ont_name):
  """True bila ONT tercapai dari salah satu node OLT pada topologi."""
  rows = conn.execute(
      "SELECT DISTINCT source_node FROM olt_device_links WHERE username = ?"
      " UNION SELECT DISTINCT target_node FROM olt_device_links WHERE username = ?",
      (username, username),
  ).fetchall()
  candidates = [
      row[0]
      for row in rows
      if row[0] and ("huawei" in row[0].lower() or "olt" in row[0].lower())
  ]
  return any(
      _topology_reachable(conn, username, candidate, ont_name)
      for candidate in candidates
  )


# --------------------------------------------------------------------------- #
# konfigurasi server
# --------------------------------------------------------------------------- #
def configure_server(username, server_node, service_type="", local_ip=""):
  conn = get_connection()
  try:
    conn.execute(
        "INSERT OR REPLACE INTO sim_service_nodes"
        " (username, node_name, service_type, local_ip, settings_json)"
        " VALUES (?, ?, ?, ?, '{}')",
        (username, server_node, service_type, local_ip),
    )
    conn.commit()
  finally:
    conn.close()
  return {"node": server_node, "service_type": service_type, "local_ip": local_ip}


def server_settings(username, server_node):
  conn = get_connection()
  try:
    row = conn.execute(
        "SELECT node_name, service_type, local_ip FROM sim_service_nodes"
        " WHERE username = ? AND node_name = ?",
        (username, server_node),
    ).fetchone()
  finally:
    conn.close()
  if not row:
    return {"node": server_node, "service_type": "", "local_ip": ""}
  return {"node": row[0], "service_type": row[1], "local_ip": row[2]}


def configure_dhcp(username, server_node, start_ip, end_ip, gateway="",
                   dns="", vlan=0, pool_name="POOL1", local_ip=""):
  try:
    first = ipaddress.ip_address(start_ip)
    last = ipaddress.ip_address(end_ip)
  except ValueError as exc:
    raise ValueError(f"IP pool tidak valid: {exc}") from exc
  if int(first) > int(last):
    raise ValueError("start_ip harus <= end_ip.")
  conn = get_connection()
  try:
    conn.execute(
        "INSERT OR REPLACE INTO sim_dhcp_pools"
        " (username, server_node, pool_name, start_ip, end_ip, gateway, dns, vlan)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (username, server_node, pool_name, start_ip, end_ip, gateway, dns, vlan),
    )
    conn.commit()
  finally:
    conn.close()
  if local_ip:
    configure_server(username, server_node, "dhcp", local_ip)
  return {
      "server_node": server_node,
      "pool": pool_name,
      "range": f"{start_ip} - {end_ip}",
      "gateway": gateway,
      "dns": dns,
      "vlan": vlan,
      "local_ip": local_ip,
  }


def add_bras_user(username, bras_node, pppoe_user, pppoe_pass,
                  profile="default", vlan=0):
  conn = get_connection()
  try:
    conn.execute(
        "INSERT OR REPLACE INTO sim_bras_users"
        " (username, bras_node, pppoe_user, pppoe_pass, profile, vlan)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (username, bras_node, pppoe_user, pppoe_pass, profile, vlan),
    )
    conn.commit()
  finally:
    conn.close()
  return {
      "bras_node": bras_node,
      "username": pppoe_user,
      "profile": profile,
      "vlan": vlan,
  }


def delete_bras_user(username, bras_node, pppoe_user):
  conn = get_connection()
  try:
    conn.execute(
        "DELETE FROM sim_bras_users"
        " WHERE username = ? AND bras_node = ? AND pppoe_user = ?",
        (username, bras_node, pppoe_user),
    )
    conn.commit()
  finally:
    conn.close()


def set_ont_pppoe(username, olt_node, port, ont_id, pppoe_user,
                  pppoe_pass, vlan=0):
  conn = get_connection()
  try:
    row = conn.execute(
        "SELECT ont_name FROM olt_onts WHERE username = ? AND port = ? AND ont_id = ?",
        (username, port, ont_id),
    ).fetchone()
    if not row:
      raise ValueError(f"ONT {ont_id} pada {port} tidak ada.")
    conn.execute(
        "UPDATE olt_onts SET pppoe_user = ?, pppoe_pass = ?, wan_vlan = ?,"
        " wan_mode = 'pppoe', wan_status = 'configured', wan_updated = ?"
        " WHERE username = ? AND port = ? AND ont_id = ?",
        (
            pppoe_user,
            pppoe_pass,
            vlan,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            username,
            port,
            ont_id,
        ),
    )
    conn.commit()
    ont_name = row[0]
  finally:
    conn.close()
  return {
      "ont": ont_name,
      "port": port,
      "ont_id": ont_id,
      "pppoe_user": pppoe_user,
      "vlan": vlan,
      "status": "configured",
  }


# --------------------------------------------------------------------------- #
# koneksi PPPoE + alokasi DHCP
# --------------------------------------------------------------------------- #
def _pick_pool(conn, username, olt_node=None):
  rows = _rows(
      conn,
      "SELECT * FROM sim_dhcp_pools WHERE username = ? ORDER BY server_node",
      (username,),
  )
  return rows[0] if rows else None


def _allocate_ip(conn, username, pool):
  network = ipaddress.ip_network(
      f"{pool['start_ip']}/{ipaddress.ip_address(pool['start_ip']).max_prefixlen}",
      strict=False,
  )
  start = int(ipaddress.ip_address(pool["start_ip"]))
  end = int(ipaddress.ip_address(pool["end_ip"]))
  used = {
      row[0]
      for row in conn.execute(
          "SELECT ip FROM sim_leases WHERE username = ? AND state = 'active'",
          (username,),
      )
  }
  gateway = pool.get("gateway") or ""
  for value in range(start, end + 1):
    ip = str(ipaddress.ip_address(value))
    if ip in used or ip == gateway:
      continue
    if value in (network.network_address, network.broadcast_address) and network.num_addresses > 2:
      continue
    return ip
  return None


def connect(username, olt_node, port, ont_id):
  conn = get_connection()
  try:
    ont = conn.execute(
        "SELECT ont_name, pppoe_user, pppoe_pass, wan_vlan, wan_status,"
        " wan_mode, mac_address"
        " FROM olt_onts WHERE username = ? AND port = ? AND ont_id = ?",
        (username, port, ont_id),
    ).fetchone()
    if not ont:
      return {"ok": False, "message": f"ONT {ont_id} pada {port} tidak ditemukan."}
    ont_name, pppoe_user, pppoe_pass, wan_vlan, wan_status, wan_mode, mac_address = ont
    if (wan_mode or "pppoe").lower() == "ipoe":
      return _connect_ipoe(
          conn, username, olt_node, port, ont_id, ont
      )
    if not pppoe_user or not pppoe_pass:
      return {
          "ok": False,
          "message": "ONT belum dikonfigurasi PPPoE (set username/password dulu).",
      }
    if olt_node:
      connected = _topology_reachable(conn, username, olt_node, ont_name)
    else:
      connected = _connected_to_any_olt(conn, username, ont_name)
    if not connected:
      return {"ok": False, "message": f"ONT {ont_name} tidak terhubung ke OLT (kabel/LOS)."}

    account = conn.execute(
        "SELECT bras_node, profile FROM sim_bras_users"
        " WHERE username = ? AND pppoe_user = ? AND pppoe_pass = ?",
        (username, pppoe_user, pppoe_pass),
    ).fetchone()
    if not account:
      conn.execute(
          "UPDATE olt_onts SET wan_status = 'auth-failed', wan_ip = NULL"
          " WHERE username = ? AND port = ? AND ont_id = ?",
          (username, port, ont_id),
      )
      conn.commit()
      return {
          "ok": False,
          "message": f"PPPoE authentication failed untuk user '{pppoe_user}'.",
      }

    bras_node, profile = account

    from modules.commands import pitp

    pitp_ok, pitp_msg = pitp.bind(
        username, pppoe_user, f"{port}:{ont_id}", olt_node or ""
    )
    if not pitp_ok:
      return {"ok": False, "message": pitp_msg}

    pool = _pick_pool(conn, username, olt_node)
    if not pool:
      return {"ok": False, "message": "Belum ada DHCP pool yang dikonfigurasi."}

    existing = conn.execute(
        "SELECT ip FROM sim_leases WHERE username = ? AND ont_name = ?"
        " AND state = 'active'",
        (username, ont_name or f"{port}/{ont_id}"),
    ).fetchone()
    ip = existing[0] if existing else _allocate_ip(conn, username, pool)
    if not ip:
      return {"ok": False, "message": "DHCP pool habis."}

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "DELETE FROM sim_leases WHERE username = ? AND ont_name = ? AND state = 'active'",
        (username, ont_name or f"{port}/{ont_id}"),
    )
    conn.execute(
        "INSERT INTO sim_leases"
        " (username, server_node, ip, ont_name, mac, state, started_at)"
        " VALUES (?, ?, ?, ?, ?, 'active', ?)",
        (username, pool["server_node"], ip, ont_name or f"{port}/{ont_id}", "", now),
    )
    conn.execute(
        "UPDATE sim_sessions SET state = 'down' WHERE username = ? AND ont_name = ?",
        (username, ont_name or f"{port}/{ont_id}"),
    )
    conn.execute(
        "INSERT INTO sim_sessions"
        " (username, bras_node, pppoe_user, ont_name, ip, vlan, state, started_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'up', ?)",
        (
            username,
            bras_node,
            pppoe_user,
            ont_name or f"{port}/{ont_id}",
            ip,
            wan_vlan or 0,
            now,
        ),
    )
    conn.execute(
        "UPDATE olt_onts SET wan_ip = ?, wan_status = 'online', wan_updated = ?"
        " WHERE username = ? AND port = ? AND ont_id = ?",
        (ip, now, username, port, ont_id),
    )
    conn.commit()
  finally:
    conn.close()

  try:
    from modules.commands.mirror import emit_pppoe_exchange
    emit_pppoe_exchange(username, olt_node, ont_name or f"{port}/{ont_id}", bras_node, pppoe_user, ip)
  except Exception:
    pass

  return {
      "ok": True,
      "message": "PPPoE authentication succeeded, IP address assigned.",
      "ip": ip,
      "gateway": pool.get("gateway") or "",
      "dns": pool.get("dns") or "",
      "pool": pool["pool_name"],
      "server_node": pool["server_node"],
      "bras_node": bras_node,
      "profile": profile,
      "pppoe_user": pppoe_user,
  }


def _option82_mode(conn, username):
  row = conn.execute(
      "SELECT value FROM olt_system_config"
      " WHERE username = ? AND key = 'dhcp_option82'",
      (username,),
  ).fetchone()
  value = (row[0] if row else "disable").lower()
  return value if value in ("enable", "forward", "rebuild") else "disable"


def _connect_ipoe(conn, username, olt_node, port, ont_id, ont):
  ont_name, _user, _pass, wan_vlan, _status, _mode, mac = ont
  mac = (mac or "").strip()
  if not mac:
    return {"ok": False, "message": "ONT IPoE belum punya MAC address."}
  if olt_node:
    connected = _topology_reachable(conn, username, olt_node, ont_name)
  else:
    connected = _connected_to_any_olt(conn, username, ont_name)
  if not connected:
    return {"ok": False, "message": f"ONT {ont_name} tidak terhubung ke OLT (kabel/LOS)."}

  pool = _pick_pool(conn, username, olt_node)
  if not pool:
    return {"ok": False, "message": "Belum ada DHCP pool yang dikonfigurasi."}

  key = ont_name or f"{port}/{ont_id}"
  existing = conn.execute(
      "SELECT ip FROM sim_leases WHERE username = ? AND ont_name = ?"
      " AND state = 'active'",
      (username, key),
  ).fetchone()
  ip = existing[0] if existing else _allocate_ip(conn, username, pool)
  if not ip:
    return {"ok": False, "message": "DHCP pool habis."}

  now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  option82 = _option82_mode(conn, username)
  circuit_id = remote_id = ""
  if option82 != "disable":
    circuit_id = f"{olt_node or 'OLT'}:{port}:{ont_id}"
    remote_id = mac

  conn.execute(
      "DELETE FROM sim_leases WHERE username = ? AND ont_name = ? AND state = 'active'",
      (username, key),
  )
  conn.execute(
      "INSERT INTO sim_leases (username, server_node, ip, ont_name, mac, state,"
      " started_at, circuit_id, remote_id, option82)"
      " VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
      (
          username,
          pool["server_node"],
          ip,
          key,
          mac,
          now,
          circuit_id,
          remote_id,
          option82,
      ),
  )
  conn.execute(
      "UPDATE olt_onts SET wan_ip = ?, wan_status = 'online', wan_updated = ?"
      " WHERE username = ? AND port = ? AND ont_id = ?",
      (ip, now, username, port, ont_id),
  )
  conn.commit()
  try:
    from modules.commands.mirror import emit_dhcp_exchange
    emit_dhcp_exchange(username, olt_node, ont_name or f"{port}/{ont_id}", pool["server_node"], ip, mac)
  except Exception:
    pass
  return {
      "ok": True,
      "message": "IPoE DHCP succeeded, IP address assigned.",
      "mode": "ipoe",
      "ip": ip,
      "gateway": pool.get("gateway") or "",
      "dns": pool.get("dns") or "",
      "pool": pool["pool_name"],
      "server_node": pool["server_node"],
      "mac": mac,
      "circuit_id": circuit_id,
      "remote_id": remote_id,
      "option82": option82,
  }


def set_ont_ipoe(username, olt_node, port, ont_id, vlan, mac):
  """Konfigurasi WAN IPoE: cukup VLAN + MAC address (tanpa PPPoE)."""
  mac = (mac or "").strip()
  if not mac:
    raise ValueError("MAC address ONT wajib diisi.")
  conn = get_connection()
  try:
    row = conn.execute(
        "SELECT ont_name FROM olt_onts WHERE username = ? AND port = ? AND ont_id = ?",
        (username, port, ont_id),
    ).fetchone()
    if not row:
      raise ValueError(f"ONT {ont_id} pada {port} tidak ada.")
    conn.execute(
        "UPDATE olt_onts SET wan_mode = 'ipoe', mac_address = ?, wan_vlan = ?,"
        " pppoe_user = NULL, pppoe_pass = NULL, wan_status = 'configured',"
        " wan_updated = ?"
        " WHERE username = ? AND port = ? AND ont_id = ?",
        (
            mac,
            vlan,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            username,
            port,
            ont_id,
        ),
    )
    conn.commit()
    ont_name = row[0]
  finally:
    conn.close()
  return {
      "ont": ont_name,
      "port": port,
      "ont_id": ont_id,
      "mode": "ipoe",
      "mac": mac,
      "vlan": vlan,
      "status": "configured",
  }


def dhcp_option82(username, cmd):
  """dhcp option82 {enable|disable|forward|rebuild} (mode global)."""
  tokens = cmd.split()
  if len(tokens) < 3 or tokens[0].lower() != "dhcp" or tokens[1].lower() != "option82":
    return None
  action = tokens[2].lower()
  if action not in ("enable", "disable", "forward", "rebuild"):
    return "  Failure: Use dhcp option82 {enable|disable|forward|rebuild}."
  key = "dhcp_option82"
  if len(tokens) >= 5 and tokens[3].lower() == "port":
    key = f"dhcp_option82_port_{tokens[4].replace('/', '_')}"
    action = tokens[5].lower() if len(tokens) > 5 else action
    if action not in ("enable", "disable", "forward", "rebuild"):
      return "  Failure: Use dhcp option82 port <f/s/p> {enable|disable}."
  conn = get_connection()
  try:
    conn.execute(
        "INSERT OR REPLACE INTO olt_system_config (username, key, value)"
        " VALUES (?, ?, ?)",
        (username, key, action),
    )
    conn.commit()
  finally:
    conn.close()
  return f"\n  Command:\n          {cmd}\n  DHCP option82 {action}."


def pc_wan_status(username, pc_node):
  conn = get_connection()
  try:
    row = conn.execute(
        "SELECT pppoe_user, pppoe_pass, ip, vlan, status, updated FROM sim_pc_wan"
        " WHERE username = ? AND node_name = ?",
        (username, pc_node),
    ).fetchone()
  finally:
    conn.close()
  if not row:
    return {
        "pppoe_user": "",
        "pppoe_pass": "",
        "ip": "",
        "vlan": None,
        "status": "",
        "updated": "",
    }
  return {
      "pppoe_user": row[0],
      "pppoe_pass": row[1],
      "ip": row[2],
      "vlan": row[3],
      "status": row[4],
      "updated": row[5],
  }


def set_pc_pppoe(username, pc_node, pppoe_user, pppoe_pass):
  conn = get_connection()
  try:
    conn.execute(
        "INSERT OR REPLACE INTO sim_pc_wan"
        " (username, node_name, pppoe_user, pppoe_pass, ip, vlan, status, updated)"
        " VALUES (?, ?, ?, ?, '', 0, 'configured', ?)",
        (
            username,
            pc_node,
            pppoe_user,
            pppoe_pass,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
  finally:
    conn.close()
  return {"node": pc_node, "pppoe_user": pppoe_user, "status": "configured"}


def _pc_neighbor_ont(conn, username, pc_node):
  for source, target, source_port, target_port in conn.execute(
      "SELECT source_node, target_node, source_port, target_port"
      " FROM olt_device_links WHERE username = ? AND is_up = 1",
      (username,),
  ).fetchall():
    if source == pc_node and target.upper().startswith("ONT"):
      return target, target_port
    if target == pc_node and source.upper().startswith("ONT"):
      return source, source_port
  return None, None


def connect_pc(username, pc_node):
  """PC dial PPPoE lewat ONT bridge -> BRAS -> IP dari DHCP."""
  conn = get_connection()
  try:
    row = conn.execute(
        "SELECT pppoe_user, pppoe_pass FROM sim_pc_wan"
        " WHERE username = ? AND node_name = ?",
        (username, pc_node),
    ).fetchone()
    if not row or not row[0] or not row[1]:
      return {"ok": False, "message": "PC belum dikonfigurasi PPPoE."}
    pppoe_user, pppoe_pass = row

    ont_name, _ont_link_port = _pc_neighbor_ont(conn, username, pc_node)
    if not ont_name:
      return {"ok": False, "message": "PC tidak terhubung ke ONT."}
    ont = conn.execute(
        "SELECT port, ont_id, run_state FROM olt_onts"
        " WHERE username = ? AND ont_name = ? ORDER BY ont_id DESC LIMIT 1",
        (username, ont_name),
    ).fetchone()
    if not ont:
      return {"ok": False, "message": f"ONT {ont_name} belum terdaftar di OLT."}
    if (ont[2] or "").lower() != "online":
      return {"ok": False, "message": f"ONT {ont_name} sedang offline."}

    from modules.commands.ont_ports import bridge_vlan_for_ont

    vlan = bridge_vlan_for_ont(username, ont_name, 1)
    if vlan is None:
      return {
          "ok": False,
          "message": "Native VLAN port ETH ONT belum dikonfigurasi"
          " (ont port native-vlan ...).",
      }

    account = conn.execute(
        "SELECT bras_node FROM sim_bras_users"
        " WHERE username = ? AND pppoe_user = ? AND pppoe_pass = ?",
        (username, pppoe_user, pppoe_pass),
    ).fetchone()
    if not account:
      return {
          "ok": False,
          "message": f"PPPoE authentication failed untuk user '{pppoe_user}'.",
      }
    bras_node = account[0]

    from modules.commands import pitp

    pitp_ok, pitp_msg = pitp.bind(
        username, pppoe_user, f"{ont[0]}:{ont[1]}", ""
    )
    if not pitp_ok:
      return {"ok": False, "message": pitp_msg}

    pool = _pick_pool(conn, username)
    if not pool:
      return {"ok": False, "message": "Belum ada DHCP pool yang dikonfigurasi."}
    existing = conn.execute(
        "SELECT ip FROM sim_leases WHERE username = ? AND ont_name = ?"
        " AND state = 'active'",
        (username, pc_node),
    ).fetchone()
    ip = existing[0] if existing else _allocate_ip(conn, username, pool)
    if not ip:
      return {"ok": False, "message": "DHCP pool habis."}

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "DELETE FROM sim_leases WHERE username = ? AND ont_name = ? AND state = 'active'",
        (username, pc_node),
    )
    conn.execute(
        "INSERT INTO sim_leases (username, server_node, ip, ont_name, mac, state,"
        " started_at, circuit_id, remote_id, option82)"
        " VALUES (?, ?, ?, ?, '', 'active', ?, '', '', 'disable')",
        (username, pool["server_node"], ip, pc_node, now),
    )
    conn.execute(
        "UPDATE sim_sessions SET state = 'down' WHERE username = ? AND ont_name = ?",
        (username, pc_node),
    )
    conn.execute(
        "INSERT INTO sim_sessions (username, bras_node, pppoe_user, ont_name, ip,"
        " vlan, state, started_at) VALUES (?, ?, ?, ?, ?, ?, 'up', ?)",
        (username, bras_node, pppoe_user, pc_node, ip, vlan, now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO sim_pc_wan"
        " (username, node_name, pppoe_user, pppoe_pass, ip, vlan, status, updated)"
        " VALUES (?, ?, ?, ?, ?, ?, 'online', ?)",
        (username, pc_node, pppoe_user, pppoe_pass, ip, vlan, now),
    )
    olt_link = conn.execute("SELECT source_node FROM olt_device_links WHERE username=? AND source_port=? AND source_node LIKE 'Huawei_%' UNION SELECT target_node FROM olt_device_links WHERE username=? AND target_port=? AND target_node LIKE 'Huawei_%' LIMIT 1", (username,ont[0],username,ont[0])).fetchone()
    olt_node = olt_link[0] if olt_link else None
    conn.commit()
  finally:
    conn.close()
  try:
    from modules.commands.mirror import emit_pppoe_exchange
    emit_pppoe_exchange(username, olt_node, pc_node, bras_node, pppoe_user, ip)
  except Exception:
    pass
  return {
      "ok": True,
      "message": "PPPoE dari PC berhasil, IP didapat dari DHCP.",
      "ip": ip,
      "gateway": pool.get("gateway") or "",
      "dns": pool.get("dns") or "",
      "vlan": vlan,
      "bras_node": bras_node,
      "pppoe_user": pppoe_user,
      "ont": ont_name,
  }


def disconnect_pc(username, pc_node):
  conn = get_connection()
  try:
    conn.execute(
        "UPDATE sim_leases SET state = 'released' WHERE username = ?"
        " AND ont_name = ? AND state = 'active'",
        (username, pc_node),
    )
    conn.execute(
        "UPDATE sim_sessions SET state = 'down' WHERE username = ?"
        " AND ont_name = ? AND state = 'up'",
        (username, pc_node),
    )
    conn.execute(
        "UPDATE sim_pc_wan SET ip = '', status = 'disconnected', updated = ?"
        " WHERE username = ? AND node_name = ?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username, pc_node),
    )
    conn.commit()
  finally:
    conn.close()
  return {"ok": True, "message": "PPPoE PC diputus, IP dilepas."}


def disconnect(username, olt_node, port, ont_id):
  conn = get_connection()
  try:
    ont = conn.execute(
        "SELECT ont_name FROM olt_onts WHERE username = ? AND port = ? AND ont_id = ?",
        (username, port, ont_id),
    ).fetchone()
    if not ont:
      return {"ok": False, "message": "ONT tidak ditemukan."}
    key = ont[0] or f"{port}/{ont_id}"
    conn.execute(
        "UPDATE sim_leases SET state = 'released' WHERE username = ? AND ont_name = ?"
        " AND state = 'active'",
        (username, key),
    )
    conn.execute(
        "UPDATE sim_sessions SET state = 'down' WHERE username = ? AND ont_name = ?"
        " AND state = 'up'",
        (username, key),
    )
    conn.execute(
        "UPDATE olt_onts SET wan_ip = NULL, wan_status = 'disconnected'"
        " WHERE username = ? AND port = ? AND ont_id = ?",
        (username, port, ont_id),
    )
    conn.commit()
  finally:
    conn.close()
  return {"ok": True, "message": f"PPPoE session {key} disconnected, IP dilepas."}


def wan_status(username, port, ont_id):
  conn = get_connection()
  try:
    row = conn.execute(
        "SELECT pppoe_user, pppoe_pass, wan_vlan, wan_ip, wan_status, wan_updated,"
        " wan_mode, mac_address"
        " FROM olt_onts WHERE username = ? AND port = ? AND ont_id = ?",
        (username, port, ont_id),
    ).fetchone()
    lease = conn.execute(
        "SELECT circuit_id, remote_id, option82 FROM sim_leases"
        " WHERE username = ? AND state = 'active'"
        " AND ont_name = (SELECT ont_name FROM olt_onts"
        "  WHERE username = ? AND port = ? AND ont_id = ?)"
        " ORDER BY id DESC LIMIT 1",
        (username, username, port, ont_id),
    ).fetchone()
  finally:
    conn.close()
  if not row:
    return None
  return {
      "pppoe_user": row[0],
      "pppoe_pass": row[1],
      "vlan": row[2],
      "ip": row[3],
      "status": row[4],
      "updated": row[5],
      "mode": (row[6] or "pppoe").lower(),
      "mac": row[7] or "",
      "circuit_id": lease[0] if lease else "",
      "remote_id": lease[1] if lease else "",
      "option82": (lease[2] if lease else "") or "",
  }


def ont_wan_mode(username, port, ont_id):
  conn = get_connection()
  try:
    row = conn.execute(
        "SELECT COALESCE(wan_mode, 'pppoe') FROM olt_onts"
        " WHERE username = ? AND port = ? AND ont_id = ?",
        (username, port, ont_id),
    ).fetchone()
  finally:
    conn.close()
  return (row[0] if row else "pppoe").lower()


def leases(username, server_node=None):
  conn = get_connection()
  try:
    sql = "SELECT * FROM sim_leases WHERE username = ?"
    params = [username]
    if server_node:
      sql += " AND server_node = ?"
      params.append(server_node)
    sql += " ORDER BY started_at DESC, id DESC"
    return _rows(conn, sql, tuple(params))
  finally:
    conn.close()


def active_sessions(username, bras_node=None):
  """Gabungan sesi PPPoE (sim_sessions) dan lease IPoE aktif, untuk BRAS."""
  conn = get_connection()
  try:
    result = []
    sql = (
        "SELECT pppoe_user, ont_name, ip, vlan, started_at, bras_node"
        " FROM sim_sessions WHERE username = ? AND state = 'up'"
    )
    params = [username]
    if bras_node:
      sql += " AND bras_node = ?"
      params.append(bras_node)
    for row in conn.execute(sql, tuple(params)).fetchall():
      result.append(
          {
              "type": "PPPoE",
              "user": row[0],
              "ont": row[1],
              "ip": row[2],
              "vlan": row[3],
              "since": row[4],
              "bras_node": row[5],
              "circuit_id": "",
              "remote_id": "",
          }
      )

    for row in conn.execute(
        "SELECT ont_name, ip, started_at, circuit_id, remote_id, mac"
        " FROM sim_leases WHERE username = ? AND state = 'active'",
        (username,),
    ).fetchall():
      ont = conn.execute(
          "SELECT wan_mode, wan_vlan FROM olt_onts WHERE username = ?"
          " AND ont_name = ? ORDER BY ont_id DESC LIMIT 1",
          (username, row[0]),
      ).fetchone()
      if not ont or (ont[0] or "pppoe").lower() != "ipoe":
        continue
      result.append(
          {
              "type": "IPoE",
              "user": row[5] or "-",
              "ont": row[0],
              "ip": row[1],
              "vlan": ont[1],
              "since": row[2],
              "bras_node": "",
              "circuit_id": row[3],
              "remote_id": row[4],
          }
      )
    result.sort(key=lambda item: item["since"] or "", reverse=True)
    return result
  finally:
    conn.close()


def sessions(username, bras_node=None):
  conn = get_connection()
  try:
    sql = "SELECT * FROM sim_sessions WHERE username = ?"
    params = [username]
    if bras_node:
      sql += " AND bras_node = ?"
      params.append(bras_node)
    sql += " ORDER BY started_at DESC, id DESC"
    return _rows(conn, sql, tuple(params))
  finally:
    conn.close()


def dhcp_pool(username, server_node):
  conn = get_connection()
  try:
    rows = _rows(
        conn,
        "SELECT * FROM sim_dhcp_pools WHERE username = ? AND server_node = ?",
        (username, server_node),
    )
    return rows[0] if rows else None
  finally:
    conn.close()


def bras_users(username, bras_node):
  conn = get_connection()
  try:
    return _rows(
        conn,
        "SELECT * FROM sim_bras_users WHERE username = ? AND bras_node = ?"
        " ORDER BY pppoe_user",
        (username, bras_node),
    )
  finally:
    conn.close()


# --------------------------------------------------------------------------- #
# perintah CLI
# --------------------------------------------------------------------------- #
def _normalize_port(token, gpon_port):
  """'0/1/3' -> apa adanya, '0/1' -> 0/1/0, '3' -> <gpon>/3."""
  token = (token or "").strip()
  if token.count("/") == 2:
    return token
  if token.count("/") == 1:
    return f"{token}/0"
  return f"{gpon_port}/{token}"


def handle_command(username, node_name, cmd, cli_state="ENABLE", gpon_port="0/1"):
  """Tangani perintah terkait layanan; None bila bukan milik modul ini."""
  text = " ".join(cmd.strip().split())
  low = text.lower()
  if not text:
    return None

  if low.startswith("dhcp option82"):
    return dhcp_option82(username, text)

  match = re.match(
      r"^ont ipoe-config (\S+) (\d+) vlan (\d+) mac (\S+)$",
      text,
      re.IGNORECASE,
  )
  if match:
    port_token, ont_id, vlan, mac = match.groups()
    port = _normalize_port(port_token, gpon_port)
    try:
      result = set_ont_ipoe(
          username, node_name, port, int(ont_id), int(vlan), mac
      )
    except ValueError as exc:
      return f"\n  Failure: {exc}"
    return (
        f"\n  Command:\n          {cmd}\n"
        f"  IPoE client configured on {port} ont {ont_id}:"
        f" vlan {result['vlan']}, mac {result['mac']}."
    )

  match = re.match(r"^ont ipoe-connect (\S+) (\d+)$", text, re.IGNORECASE)
  if match:
    port_token, ont_id = match.groups()
    port = _normalize_port(port_token, gpon_port)
    result = connect(username, node_name, port, int(ont_id))
    if not result["ok"]:
      return f"\n  Command:\n          {cmd}\n  Failure: {result['message']}"
    lines = [
        f"\n  Command:\n          {cmd}",
        f"  {result['message']}",
        f"  Mode       : IPoE (DHCP)",
        f"  MAC        : {result.get('mac', '-')}",
        f"  IP address : {result['ip']}",
        f"  Gateway    : {result['gateway']}",
        f"  DNS        : {result['dns']}",
        f"  Pool       : {result['pool']} @ {result['server_node']}",
    ]
    if result.get("circuit_id"):
      lines.append(f"  Option82   : circuit-id {result['circuit_id']}")
      lines.append(f"               remote-id  {result['remote_id']}")
    return "\n".join(lines)

  match = re.match(r"^ont ipoe-disconnect (\S+) (\d+)$", text, re.IGNORECASE)
  if match:
    port_token, ont_id = match.groups()
    port = _normalize_port(port_token, gpon_port)
    result = disconnect(username, node_name, port, int(ont_id))
    prefix = "" if result["ok"] else "  Failure: "
    return f"\n  Command:\n          {cmd}\n  {prefix}{result['message']}"

  match = re.match(
      r"^ont pppoe-config (\S+) (\d+) user (\S+) password (\S+)(?: vlan (\d+))?$",
      text,
      re.IGNORECASE,
  )
  if match:
    port_token, ont_id, user, password, vlan = match.groups()
    port = _normalize_port(port_token, gpon_port)
    try:
      result = set_ont_pppoe(
          username, node_name, port, int(ont_id), user, password, int(vlan or 0)
      )
    except ValueError as exc:
      return f"\n  Failure: {exc}"
    return (
        f"\n  Command:\n          {cmd}\n"
        f"  PPPoE client configured on {port} ont {ont_id}: user {result['pppoe_user']}"
        f" (vlan {result['vlan']})."
    )

  match = re.match(r"^ont pppoe-connect (\S+) (\d+)$", text, re.IGNORECASE)
  if match:
    port_token, ont_id = match.groups()
    port = _normalize_port(port_token, gpon_port)
    if ont_wan_mode(username, port, int(ont_id)) == "ipoe":
      return (
          f"\n  Command:\n          {cmd}\n  Failure: ONT memakai IPoE;"
          " gunakan 'ont ipoe-connect'."
      )
    result = connect(username, node_name, port, int(ont_id))
    if not result["ok"]:
      return f"\n  Command:\n          {cmd}\n  Failure: {result['message']}"
    return (
        f"\n  Command:\n          {cmd}\n"
        f"  {result['message']}\n"
        f"  IP address : {result['ip']}\n"
        f"  Gateway    : {result['gateway']}\n"
        f"  DNS        : {result['dns']}\n"
        f"  Pool       : {result['pool']} @ {result['server_node']}\n"
        f"  BRAS       : {result['bras_node']} ({result['pppoe_user']})"
    )

  match = re.match(r"^ont pppoe-disconnect (\S+) (\d+)$", text, re.IGNORECASE)
  if match:
    port_token, ont_id = match.groups()
    port = _normalize_port(port_token, gpon_port)
    result = disconnect(username, node_name, port, int(ont_id))
    prefix = "" if result["ok"] else "  Failure: "
    return f"\n  Command:\n          {cmd}\n  {prefix}{result['message']}"

  match = re.match(
      r"^display ont wan-info (\S+) (\d+)(?: (\d+))?$", text, re.IGNORECASE
  )
  if match:
    port_token, ont_id = match.group(1), int(match.group(2))
    port = _normalize_port(port_token, gpon_port)
    status = wan_status(username, port, ont_id)
    if not status:
      return f"\n  Command:\n          {cmd}\n  Failure: ONT tidak ditemukan."
    mode_text = "IPoE" if status["mode"] == "ipoe" else "PPPoE"
    lines = [
        f"\n  Command:\n          {cmd}",
        "  ------------------------------------------------------------------",
        f"  F/S/P                : {port}",
        f"  ONT-ID               : {ont_id}",
        f"  WAN mode             : {mode_text}",
        f"  PPPoE user           : {status['pppoe_user'] or '-'}",
        f"  MAC address          : {status['mac'] or '-'}",
        f"  VLAN                 : {status['vlan'] if status['vlan'] is not None else '-'}",
        f"  WAN IP               : {status['ip'] or '-'}",
        f"  WAN status           : {status['status'] or '-'}",
    ]
    if status["circuit_id"]:
      lines.append(f"  Option82 circuit-id  : {status['circuit_id']}")
      lines.append(f"  Option82 remote-id   : {status['remote_id']}")
      lines.append(f"  Option82 mode        : {status['option82']}")
    lines.extend([
        f"  Updated              : {status['updated'] or '-'}",
        "  ------------------------------------------------------------------",
    ])
    return "\n".join(lines)

  if low.startswith("display dhcp lease"):
    rows = leases(username)
    lines = [
        f"\n  Command:\n          {cmd}",
        "  ------------------------------------------------------------------",
        "  IP              ONT           Server        State     Since",
        "  ------------------------------------------------------------------",
    ]
    for row in rows:
      if row["state"] != "active":
        continue
      lines.append(
          f"  {row['ip']:<15} {row['ont_name']:<13} {row['server_node']:<13}"
          f" {row['state']:<9} {row['started_at']}"
      )
    lines.append("  ------------------------------------------------------------------")
    return "\n".join(lines)

  if low.startswith("display pppoe session"):
    rows = sessions(username)
    lines = [
        f"\n  Command:\n          {cmd}",
        "  ------------------------------------------------------------------",
        "  User            ONT           IP              BRAS          State",
        "  ------------------------------------------------------------------",
    ]
    for row in rows:
      if row["state"] != "up":
        continue
      lines.append(
          f"  {row['pppoe_user']:<15} {row['ont_name']:<13} {row['ip']:<15}"
          f" {row['bras_node']:<13} {row['state']}"
      )
    lines.append("  ------------------------------------------------------------------")
    return "\n".join(lines)

  return None


