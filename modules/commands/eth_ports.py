"""Perintah native-vlan (access port) untuk CLI terminal & REST API."""

from __future__ import annotations

import re

from modules.database import get_connection


def _members(conn, username, node_name, board, vlan_id):
  rows = conn.execute(
      "SELECT port FROM olt_node_vlan_ports"
      " WHERE username = ? AND node_name = ? AND vlan_id = ? AND port LIKE ?",
      (username, node_name, vlan_id, f"{board}/%"),
  ).fetchall()
  return {int(row[0].rsplit("/", 1)[1]) for row in rows if row[0]}


def native_vlan(username, node_name, board, cmd):
  """native-vlan {port|all} vlan <id> [dedicated-net-id <1-8>]"""
  text = " ".join(cmd.split())
  removing = text.lower().startswith("undo ")
  body = text[5:] if removing else text
  tokens = body.split()
  if len(tokens) < 2 or tokens[0].lower() != "native-vlan":
    return "  Failure: Use native-vlan {<port>|all} vlan <VLAN-ID>."

  selector = tokens[1].lower()
  lower = [token.lower() for token in tokens]
  vlan_id = None
  if "vlan" in lower:
    try:
      vlan_id = int(tokens[lower.index("vlan") + 1])
    except (ValueError, IndexError):
      return "  Failure: Use native-vlan {<port>|all} vlan <VLAN-ID>."
  if not removing and vlan_id is None:
    return "  Failure: Use native-vlan {<port>|all} vlan <VLAN-ID>."

  dedicated = None
  if "dedicated-net-id" in lower:
    try:
      dedicated = int(tokens[lower.index("dedicated-net-id") + 1])
    except (ValueError, IndexError):
      return "  Failure: dedicated-net-id harus 1-8."

  conn = get_connection()
  try:
    if removing:
      if selector == "all":
        conn.execute(
            "DELETE FROM olt_eth_native_vlans WHERE username = ?"
            " AND node_name = ? AND board = ?",
            (username, node_name, board),
        )
        conn.commit()
        return f"  Native VLAN removed from all ports on {board}."
      port_id = int(selector)
      conn.execute(
          "DELETE FROM olt_eth_native_vlans WHERE username = ? AND node_name = ?"
          " AND board = ? AND port_id = ?",
          (username, node_name, board, port_id),
      )
      conn.commit()
      return f"  Native VLAN removed from port {board}/{port_id}."

    if not 1 <= vlan_id <= 4093:
      return "  Failure: VLAN ID harus 1-4093."
    row = conn.execute(
        "SELECT dedicated_net_id FROM olt_vlan WHERE username = ? AND vlan_id = ?",
        (username, vlan_id),
    ).fetchone()
    if not row:
      return f"  Failure: VLAN {vlan_id} belum dibuat."

    members = _members(conn, username, node_name, board, vlan_id)
    # VLAN 1 adalah VLAN default: semua port otomatis menjadi anggotanya.
    default_vlan = vlan_id == 1
    if selector == "all":
      if not members and not default_vlan:
        return f"  Failure: Tidak ada port {board} yang menjadi anggota VLAN {vlan_id}."
      if members:
        port_ids = sorted(members)
      else:
        limit = 3 if board.endswith("/2") else 27
        port_ids = list(range(0, limit + 1))
    else:
      port_id = int(selector)
      if port_id not in members and not default_vlan:
        return (
            f"  Failure: Port {board}/{port_id} harus dimasukkan ke VLAN"
            f" {vlan_id} dulu (port vlan {vlan_id} {board}/{port_id})."
        )
      port_ids = [port_id]

    for port_id in port_ids:
      conn.execute(
          "DELETE FROM olt_eth_native_vlans WHERE username = ? AND node_name = ?"
          " AND board = ? AND port_id = ?",
          (username, node_name, board, port_id),
      )
      conn.execute(
          "INSERT INTO olt_eth_native_vlans"
          " (username, node_name, board, port_id, vlan_id, dedicated_net_id)"
          " VALUES (?, ?, ?, ?, ?, ?)",
          (username, node_name, board, port_id, vlan_id, dedicated),
      )
    conn.commit()
  except (ValueError, IndexError):
    return "  Failure: Parameter native-vlan tidak valid."
  finally:
    conn.close()
  return f"  Native VLAN {vlan_id} set on {board}/{port_ids[0] if len(port_ids) == 1 else 'all'}."


def display_native_vlans(username, cmd, node_name=None):
  conn = get_connection()
  try:
    rows = conn.execute(
        "SELECT board, port_id, vlan_id, dedicated_net_id"
        " FROM olt_eth_native_vlans WHERE username = ?"
        " ORDER BY board, port_id",
        (username,),
    ).fetchall()
  finally:
    conn.close()
  lines = [
      "",
      "  Command:",
      f"          {cmd}",
      "  ------------------------------------------------------------------",
      "  Port          Native VLAN   Dedicated-net",
      "  ------------------------------------------------------------------",
  ]
  for board, port_id, vlan_id, dedicated in rows:
    lines.append(f"  {board}/{port_id:<11} {vlan_id:<13} {dedicated if dedicated else '-'}")
  if not rows:
    lines.append("  (belum ada native VLAN; port memakai VLAN 1)")
  lines.append("  ------------------------------------------------------------------")
  return "\n".join(lines)


def configure_access_port(username, node_name, port, vlan_id):
  """Jadikan port ETH access: masukkan ke VLAN + set native-vlan."""
  board, _, port_text = port.rpartition("/")
  if not board or not port_text.isdigit():
    return False
  port_id = int(port_text)
  conn = get_connection()
  try:
    if not conn.execute(
        "SELECT 1 FROM olt_vlan WHERE username = ? AND vlan_id = ?",
        (username, vlan_id),
    ).fetchone():
      return False
    conn.execute(
        "DELETE FROM olt_node_vlan_ports WHERE username = ? AND node_name = ?"
        " AND vlan_id = ? AND port = ?",
        (username, node_name, vlan_id, port),
    )
    conn.execute(
        "INSERT INTO olt_node_vlan_ports (username, node_name, vlan_id, port)"
        " VALUES (?, ?, ?, ?)",
        (username, node_name, vlan_id, port),
    )
    conn.execute(
        "DELETE FROM olt_eth_native_vlans WHERE username = ? AND node_name = ?"
        " AND board = ? AND port_id = ?",
        (username, node_name, board, port_id),
    )
    conn.execute(
        "INSERT INTO olt_eth_native_vlans"
        " (username, node_name, board, port_id, vlan_id, dedicated_net_id)"
        " VALUES (?, ?, ?, ?, ?, NULL)",
        (username, node_name, board, port_id, vlan_id),
    )
    conn.commit()
  finally:
    conn.close()
  return True
