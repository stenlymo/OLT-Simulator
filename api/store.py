"""Akses data untuk REST API simulator OLT (baca + sinkronisasi autofind)."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime

from modules.database import get_connection


def _rows(sql, params=()):
  conn = get_connection()
  try:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql, params).fetchall()]
  finally:
    conn.close()


def _row(sql, params=()):
  rows = _rows(sql, params)
  return rows[0] if rows else None


def topology(username="admin"):
  """Node + link dari tabel topologi (olt_device_links)."""
  links = _rows(
      "SELECT source_node, source_port, target_node, target_port, fiber_type,"
      " sfp_module, length_km, is_up FROM olt_device_links"
      " WHERE username = ? ORDER BY source_node, source_port",
      (username,),
  )
  names = set()
  for link in links:
    names.add(link["source_node"])
    names.add(link["target_node"])

  settings = {
      row["node_name"]: row["sysname"]
      for row in _rows(
          "SELECT node_name, sysname FROM olt_node_settings WHERE username = ?",
          (username,),
      )
  }
  nodes = []
  for name in sorted(names):
    nodes.append(
        {
            "name": name,
            "sysname": settings.get(name),
            "type": "olt" if name.upper().startswith("HUAWEI") else
                    ("ont" if name.upper().startswith("ONT") else "passive"),
        }
    )
  return {"nodes": nodes, "links": links}


def nodes(username="admin"):
  return topology(username)["nodes"]


def node_ports(node_name, username="admin"):
  """Port lokal OLT yang dipakai pada topologi (mis. 0/1/0)."""
  rows = _rows(
      "SELECT source_port AS port FROM olt_device_links"
      " WHERE username = ? AND source_node = ?"
      " UNION SELECT target_port AS port FROM olt_device_links"
      " WHERE username = ? AND target_node = ?",
      (username, node_name, username, node_name),
  )
  return sorted({row["port"] for row in rows if row["port"]})


def boards(username="admin"):
  return _rows(
      "SELECT slot_id, board_name, status, subtype0, subtype1, online_offline"
      " FROM olt_boards WHERE username = ? ORDER BY slot_id",
      (username,),
  )


def vlans(username="admin"):
  return _rows(
      "SELECT vlan_id, vlan_type, vlan_attr, bound_port, bound_state, bound_tag,"
      " description, ip_address, dedicated_net_id, vlan_name FROM olt_vlan"
      " WHERE username = ? ORDER BY vlan_id",
      (username,),
  )


def vlanifs(node_name, username="admin"):
  return _rows(
      "SELECT v.vlan_id, a.address, a.mask, a.secondary, a.description"
      " FROM olt_node_vlanifs v LEFT JOIN olt_node_vlanif_addresses a"
      " ON a.username = v.username AND a.node_name = v.node_name"
      " AND a.vlan_id = v.vlan_id"
      " WHERE v.username = ? AND v.node_name = ? ORDER BY v.vlan_id",
      (username, node_name),
  )


def routes(node_name, username="admin"):
  return _rows(
      "SELECT dest, mask, gateway, preference, tag, cost, permanent,"
      " outbound_interface, description FROM olt_node_routes"
      " WHERE username = ? AND node_name = ? ORDER BY dest, mask",
      (username, node_name),
  )


def ont_rows(node_name, username="admin"):
  """Semua ONT milik user (simulator menyimpan satu inventaris per akun)."""
  sql = (
      "SELECT port, ont_id, sn, control_flag, run_state, config_state,"
      " match_state, description, rx_power, tx_power, olt_rx_power,"
      " line_profile_id, service_profile_id, ont_name, autofind_state,"
      " pppoe_user, wan_vlan, wan_ip, wan_status, wan_mode, mac_address"
      " FROM olt_onts WHERE username = ? ORDER BY port, ont_id"
  )
  return _rows(sql, (username,))


def service_ports(node_name, username="admin"):
  sql = (
      "SELECT sp_id, vlan, port_type, port, ont_id, gemport, user_vlan, state,"
      " uplink_port, tag_transform, inbound_traffic_index, outbound_traffic_index"
      " FROM olt_service_ports WHERE username = ? ORDER BY sp_id"
  )
  return _rows(sql, (username,))


def profiles(username="admin"):
  return {
      "dba": _rows(
          "SELECT profile_id, profile_name, type, assured, max_bw, fix_bw,"
          " bind_count FROM olt_dba_profiles WHERE username = ? ORDER BY profile_id",
          (username,),
      ),
      "line": _rows(
          "SELECT profile_id, profile_name, bind_count FROM olt_line_profiles"
          " WHERE username = ? ORDER BY profile_id",
          (username,),
      ),
      "service": _rows(
          "SELECT profile_id, profile_name, eth, pots, catv FROM olt_service_profiles"
          " WHERE username = ? ORDER BY profile_id",
          (username,),
      ),
      "tr069": _rows(
          "SELECT profile_id, profile_name, url, user_name FROM olt_tr069_server_profiles"
          " WHERE username = ? ORDER BY profile_id",
          (username,),
      ),
  }


def inventory(node_name, username="admin"):
  return {
      "node": node_name,
      "sysname": sysname(node_name, username),
      "ports": node_ports(node_name, username),
      "boards": boards(username),
      "vlans": vlans(username),
      "vlanifs": vlanifs(node_name, username),
      "routes": routes(node_name, username),
      "onts": ont_rows(node_name, username),
      "service_ports": service_ports(node_name, username),
      "profiles": profiles(username),
  }


def sysname(node_name, username="admin"):
  row = _row(
      "SELECT sysname FROM olt_node_settings WHERE username = ? AND node_name = ?",
      (username, node_name),
  )
  return row["sysname"] if row else None


def ont(node_name, ont_id, username="admin"):
  for row in ont_rows(node_name, username):
    if str(row["ont_id"]) == str(ont_id):
      return row
  return None


def _allocate_sn(conn, node_name, username):
  """SN stabil dari pool untuk ONT yang terhubung tetapi belum terdaftar."""
  used = {
      row[0]
      for row in conn.execute(
          "SELECT sn FROM olt_onts WHERE username = ?", (username,)
      )
  }
  free = [
      row[0]
      for row in conn.execute(
          "SELECT sn FROM ont_sn_pool WHERE is_used = 0 ORDER BY id"
      )
      if row[0] not in used
  ]
  if not free:
    free = [
        row[0]
        for row in conn.execute("SELECT sn FROM ont_sn_pool ORDER BY id")
        if row[0] not in used
    ]
  if not free:
    return None
  index = int(hashlib.md5(node_name.encode()).hexdigest()[:4], 16) % len(free)
  sn = free[index]
  conn.execute("UPDATE ont_sn_pool SET is_used = 1 WHERE sn = ?", (sn,))
  return sn


def native_vlans(node_name, username="admin"):
  return _rows(
      "SELECT board, port_id, vlan_id, dedicated_net_id"
      " FROM olt_eth_native_vlans WHERE username = ?"
      " ORDER BY board, port_id",
      (username,),
  )


def autofind(node_name, username="admin"):
  """ONT yang terhubung fisik ke OLT dan belum diprovisioning.

  Sumber kebenaran adalah topologi (``olt_device_links`` + BFS), bukan cache.
  Baris ``olt_onts``/``olt_autofind`` ikut disinkronkan agar perintah CLI
  ``display ont autofind`` di API/GUI konsisten.
  """
  conn = get_connection()
  entries = []
  try:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    known = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT ont_name FROM olt_onts"
            " WHERE username = ? AND ont_name IS NOT NULL",
            (username,),
        )
    }
    ports = [p for p in node_ports(node_name, username) if p.startswith("0/1/")]
    for port in ports:
      linked = reachable_onts(node_name, port, username)
      for node in linked:
        if not (node.upper().startswith("ONT") or node in known):
          continue
        row = conn.execute(
            "SELECT ont_id, sn, control_flag FROM olt_onts"
            " WHERE username = ? AND ont_name = ?"
            " ORDER BY ont_id DESC LIMIT 1",
            (username, node),
        ).fetchone()
        if row and (row[2] or "").lower() == "active":
          continue
        if row is None:
          sn = _allocate_sn(conn, node, username)
          if sn is None:
            continue
          next_id = conn.execute(
              "SELECT COALESCE(MAX(ont_id), -1) + 1 FROM olt_onts"
              " WHERE username = ? AND port = ?",
              (username, port),
          ).fetchone()[0]
          conn.execute(
              "INSERT INTO olt_onts (username, port, ont_id, sn, control_flag,"
              " run_state, config_state, match_state, description, ont_name,"
              " autofind_state) VALUES"
              " (?, ?, ?, ?, NULL, 'offline', 'initial', 'initial',"
              " 'auto-find', ?, 1)",
              (username, port, next_id, sn, node),
          )
          row = (next_id, sn, None)
        entries.append(
            {
                "fsp": port,
                "sn": row[1],
                "vendor": "HWTC",
                "version": "120A0000",
                "software": "V1R001C01",
                "equipment": "G-240W-F",
                "found_at": now,
                "ont_name": node,
            }
        )

    # sinkronkan cache olt_autofind untuk port yang diperiksa
    for port in ports:
      conn.execute(
          "DELETE FROM olt_autofind WHERE username = ? AND port = ?",
          (username, port),
      )
      conn.executemany(
          "INSERT INTO olt_autofind (username, port, sn, device_type, find_time)"
          " VALUES (?, ?, ?, ?, ?)",
          [
              (username, entry["fsp"], entry["sn"], entry["equipment"], now)
              for entry in entries
              if entry["fsp"] == port
          ],
      )
    conn.commit()
  finally:
    conn.close()

  unique, seen = [], set()
  for entry in entries:
    key = (entry["fsp"], entry["sn"])
    if key not in seen:
      seen.add(key)
      unique.append(entry)
  return unique


def graph(username="admin"):
  """Adjacency list untuk penelusuran konektivitas."""
  links = topology(username)["links"]
  adjacency = {}
  for link in links:
    if not link["is_up"]:
      continue
    adjacency.setdefault(link["source_node"], []).append(
        (link["target_node"], link["source_port"])
    )
    adjacency.setdefault(link["target_node"], []).append(
        (link["source_node"], link["target_port"])
    )
  return adjacency


def reachable_onts(node_name, port, username="admin"):
  """ONT yang tercapai dari port OLT tertentu (BFS lewat splitter/roset)."""
  adjacency = graph(username)
  visited, queue = set(), [neighbor for neighbor, p in adjacency.get(node_name, []) if p == port]
  while queue:
    current = queue.pop()
    if current in visited:
      continue
    visited.add(current)
    for neighbor, _port in adjacency.get(current, []):
      if neighbor not in visited and neighbor != node_name:
        queue.append(neighbor)
  return sorted(name for name in visited if name.upper().startswith("ONT"))
