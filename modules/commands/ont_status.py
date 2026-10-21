"""Sinkronisasi run_state ONT dengan konektivitas fisik di topologi."""

from __future__ import annotations

from modules.database import get_connection


def _mark_all_offline(conn, username):
  rows = conn.execute(
      "SELECT port, ont_id, run_state FROM olt_onts WHERE username = ?",
      (username,),
  ).fetchall()
  changed = 0
  for port, ont_id, run_state in rows:
    if (run_state or "").lower() != "offline":
      conn.execute(
          "UPDATE olt_onts SET run_state = 'offline'"
          " WHERE username = ? AND port = ? AND ont_id = ?",
          (username, port, ont_id),
      )
      changed += 1
  conn.commit()
  return changed


def refresh_ont_states(username, olt_node):
  """Tandai ONT online hanya bila node ONT-nya tercapai dari port OLT.

  Mengembalikan jumlah baris yang berubah. Tanpa topologi (tidak ada link
  sama sekali, atau OLT tidak punya link), semua ONT dianggap offline.
  """
  if not olt_node:
    return 0
  conn = get_connection()
  try:
    links = conn.execute(
        "SELECT source_node, source_port, target_node, target_port"
        " FROM olt_device_links WHERE username = ? AND is_up = 1",
        (username,),
    ).fetchall()
    if not links:
      return _mark_all_offline(conn, username)

    adjacency = {}
    for source, source_port, target, target_port in links:
      adjacency.setdefault(source, []).append((target, source_port))
      adjacency.setdefault(target, []).append((source, target_port))

    if olt_node not in adjacency:
      return _mark_all_offline(conn, username)

    reachable_by_port = {}
    for neighbor, port in adjacency[olt_node]:
      seen, stack = set(), [neighbor]
      while stack:
        node = stack.pop()
        if node in seen:
          continue
        seen.add(node)
        for other, _port in adjacency.get(node, []):
          if other not in seen and other != olt_node:
            stack.append(other)
      reachable_by_port.setdefault(port, set()).update(seen)

    changed = 0
    rows = conn.execute(
        "SELECT port, ont_id, ont_name, run_state FROM olt_onts WHERE username = ?",
        (username,),
    ).fetchall()
    for port, ont_id, ont_name, run_state in rows:
      expected = (
          "online"
          if ont_name and ont_name in reachable_by_port.get(port, set())
          else "offline"
      )
      if (run_state or "").lower() != expected:
        conn.execute(
            "UPDATE olt_onts SET run_state = ?"
            " WHERE username = ? AND port = ? AND ont_id = ?",
            (expected, username, port, ont_id),
        )
        changed += 1
    conn.commit()
    return changed
  finally:
    conn.close()
