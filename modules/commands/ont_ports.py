"""Perintah `ont port native-vlan` (bridge port LAN ONT)."""

from __future__ import annotations

import re

from modules.database import get_connection


def _schema(conn):
  conn.execute(
      """
      CREATE TABLE IF NOT EXISTS ont_port_native_vlans (
        username TEXT NOT NULL,
        olt_node TEXT NOT NULL DEFAULT '',
        port TEXT NOT NULL,
        ont_id INTEGER NOT NULL,
        port_type TEXT NOT NULL DEFAULT 'eth',
        ont_port_id INTEGER NOT NULL,
        vlan_id INTEGER NOT NULL DEFAULT 1,
        priority INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (username, port, ont_id, port_type, ont_port_id)
      )
      """
  )


def ensure_schema():
  conn = get_connection()
  try:
    _schema(conn)
    conn.commit()
  finally:
    conn.close()


ensure_schema()


def handle_command(username, cmd, gpon_port, node_name=""):
  text = " ".join(cmd.strip().split())
  low = text.lower()

  match = re.match(
      r"^ont port native-vlan (\S+) (\d+) (eth|wifi|vdsl|moca) (\d+)"
      r" vlan (\d+)(?: priority (\d+))?$",
      text,
      re.IGNORECASE,
  )
  if match:
    port_token, ont_id, port_type, ont_port, vlan, priority = match.groups()
    port = (
        port_token
        if port_token.count("/") == 2
        else f"{gpon_port}/{port_token}"
    )
    vlan_id = int(vlan)
    if not 0 <= vlan_id <= 4095:
      return "  Failure: VLAN ID harus 0-4095."
    ont = get_connection()
    try:
      _schema(ont)
      row = ont.execute(
          "SELECT 1 FROM olt_onts WHERE username = ? AND port = ? AND ont_id = ?",
          (username, port, int(ont_id)),
      ).fetchone()
      if not row:
        return f"  Failure: ONT {ont_id} pada {port} tidak ditemukan."
      ont.execute(
          "INSERT OR REPLACE INTO ont_port_native_vlans"
          " (username, olt_node, port, ont_id, port_type, ont_port_id, vlan_id, priority)"
          " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
          (
              username,
              node_name,
              port,
              int(ont_id),
              port_type.lower(),
              int(ont_port),
              vlan_id,
              int(priority or 0),
          ),
      )
      ont.commit()
    finally:
      ont.close()
    return (
        f"\n  Command:\n          {cmd}\n"
        f"  ONT {port} ont {ont_id}: {port_type.lower()} port {ont_port}"
        f" native VLAN {vlan_id} (bridge/untagged)."
    )

  if low.startswith("display ont port native-vlan"):
    return display(username, cmd)
  return None


def display(username, cmd):
  conn = get_connection()
  try:
    _schema(conn)
    rows = conn.execute(
        "SELECT port, ont_id, port_type, ont_port_id, vlan_id, priority"
        " FROM ont_port_native_vlans WHERE username = ?"
        " ORDER BY port, ont_id, port_type, ont_port_id",
        (username,),
    ).fetchall()
  finally:
    conn.close()
  lines = [
      "",
      "  Command:",
      f"          {cmd}",
      "  ------------------------------------------------------------------",
      "  F/S/P       ONT  Port Type  Port-ID  Native VLAN  Priority",
      "  ------------------------------------------------------------------",
  ]
  for port, ont_id, port_type, ont_port, vlan, priority in rows:
    lines.append(
        f"  {port:<11} {ont_id:<4} {port_type:<9} {ont_port:<8}"
        f" {vlan:<12} {priority}"
    )
  if not rows:
    lines.append("  (belum ada konfigurasi native VLAN port ONT)")
  lines.append("  ------------------------------------------------------------------")
  return "\n".join(lines)


def bridge_vlan_for_ont(username, ont_name, ont_port=1):
  """Native VLAN port ETH ONT (untuk validasi PPPoE dari PC)."""
  conn = get_connection()
  try:
    _schema(conn)
    ont = conn.execute(
        "SELECT port, ont_id FROM olt_onts WHERE username = ? AND ont_name = ?"
        " ORDER BY ont_id DESC LIMIT 1",
        (username, ont_name),
    ).fetchone()
    if not ont:
      return None
    row = conn.execute(
        "SELECT vlan_id FROM ont_port_native_vlans"
        " WHERE username = ? AND port = ? AND ont_id = ? AND port_type = 'eth'"
        " AND ont_port_id = ?",
        (username, ont[0], ont[1], ont_port),
    ).fetchone()
    return row[0] if row else None
  finally:
    conn.close()
