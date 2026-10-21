"""Format perintah display vlan / display port vlan (mengikuti dokumen Huawei)."""

from __future__ import annotations

from modules.database import get_connection


def _parse_vlan_ids(token):
  ids = set()
  for part in token.split(","):
    part = part.strip()
    if not part:
      continue
    if "-" in part:
      start, end = part.split("-", 1)
      if start.isdigit() and end.isdigit():
        ids.update(range(int(start), int(end) + 1))
    elif part.isdigit():
      ids.add(int(part))
  return sorted(ids)


def _ports_for_vlan(conn, username, node_name, vlan_id):
  ports = []
  seen = set()
  for row in conn.execute(
      "SELECT port FROM olt_node_vlan_ports"
      " WHERE username = ? AND node_name = ? AND vlan_id = ? ORDER BY port",
      (username, node_name, vlan_id),
  ).fetchall():
    if row[0] and row[0] not in seen:
      seen.add(row[0])
      ports.append(row[0])
  bound = conn.execute(
      "SELECT bound_port FROM olt_vlan WHERE username = ? AND vlan_id = ?",
      (username, vlan_id),
  ).fetchone()
  if bound and bound[0] and bound[0] not in seen:
    ports.append(bound[0])
  return ports


def _detail(conn, username, node_name, row):
  vlan_id, vlan_type, vlan_attr, desc, vlan_name = row
  ports = _ports_for_vlan(conn, username, node_name, vlan_id)
  lines = [
      f"  VLAN ID: {vlan_id}",
      f"  VLAN name: {vlan_name or f'VLAN_{vlan_id:04d}'}",
      f"  VLAN type: {vlan_type or 'standard'}",
      f"  VLAN attribute: {vlan_attr or 'common'}",
      f"  VLAN description: {desc or '-'}",
      "  VLAN forwarding mode in control board: VLAN-MAC",
      "  VLAN forwarding mode: VLAN-MAC",
      "  VLAN broadcast packet forwarding policy: forward",
      "  VLAN unknown multicast packet forwarding policy: forward",
      "  VLAN unknown unicast packet forwarding policy: forward",
      "  VLAN bind service profile ID: -",
      "  VLAN bind RAIO profile index: -",
      "  VLAN priority: -",
      f"  Standard port number: {len(ports)}",
      "  ---------------------------------------------------------------------",
      "  INDEX  TYPE  STATE F /S /P  VPI  VCI   FLOWTYPE FLOWPARA   CVLAN",
      "  ---------------------------------------------------------------------",
  ]
  if ports:
    for index, port in enumerate(ports, 1):
      kind = "gpon" if port.startswith("0/1/") else "eth"
      frame, slot, port_id = (port.split("/") + ["0", "0", "0"])[:3]
      lines.append(
          f"  {index:<6} {kind:<5} up    {frame} /{slot} /{port_id}  -    -"
          "     -        -          -"
      )
  else:
    lines.append("  (tidak ada port yang terdaftar pada VLAN ini)")
  lines.append("  ---------------------------------------------------------------------")
  return lines


def display_vlan(username, cmd, node_name="Huawei_1"):
  text = " ".join(cmd.split())
  tokens = text.split()[2:]
  lower = [token.lower() for token in tokens]

  conn = get_connection()
  try:
    rows = conn.execute(
        "SELECT vlan_id, vlan_type, vlan_attr, description, vlan_name"
        " FROM olt_vlan WHERE username = ? ORDER BY vlan_id",
        (username,),
    ).fetchall()
    by_id = {row[0]: row for row in rows}

    lines = ["", "  Command:", f"          {text}"]

    # display vlan number -> statistik
    if lower and lower[0] == "number":
      counts = {}
      for row in rows:
        counts[row[1] or "standard"] = counts.get(row[1] or "standard", 0) + 1
      lines.append(f"  TOTAL: {len(rows)}")
      for kind in sorted(counts):
        lines.append(f"  VLAN type {kind}: {counts[kind]}")
      return "\n".join(lines)

    # display vlan name <name>
    if lower and lower[0] == "name" and len(tokens) > 1:
      name = tokens[1]
      match = next((row for row in rows if (row[4] or "").lower() == name.lower()), None)
      if not match:
        lines.append(f"  Failure: VLAN name {name} does not exist.")
        return "\n".join(lines)
      lines.extend(_detail(conn, username, node_name, match))
      return "\n".join(lines)

    # satu VLAN -> detail
    if len(tokens) == 1 and tokens[0].isdigit():
      vlan_id = int(tokens[0])
      row = by_id.get(vlan_id)
      if not row:
        lines.append(f"  Failure: VLAN {vlan_id} does not exist.")
        return "\n".join(lines)
      lines.extend(_detail(conn, username, node_name, row))
      return "\n".join(lines)

    # ringkasan: all / daftar / rentang
    selected = []
    if not tokens or (len(tokens) == 1 and lower[0] in ("all", "number")):
      selected = rows
    else:
      ids = set()
      index = 0
      while index < len(tokens):
        token = tokens[index].lower()
        if token == "to" and index + 1 < len(tokens) and tokens[index + 1].isdigit():
          start = int(tokens[index - 1]) if index else 1
          ids.update(range(start, int(tokens[index + 1]) + 1))
          index += 2
          continue
        if token.isdigit():
          ids.add(int(token))
        elif "," in token or "-" in token:
          ids.update(_parse_vlan_ids(token))
        index += 1
      selected = [by_id[vlan_id] for vlan_id in sorted(ids) if vlan_id in by_id]

    lines.extend([
        "  ------------------------------------------------------------------",
        "  VLAN  Type      Attribute  Ports                    Description",
        "  ------------------------------------------------------------------",
    ])
    for vlan_id, vlan_type, vlan_attr, desc, _name in selected:
      ports = _ports_for_vlan(conn, username, node_name, vlan_id)
      port_text = ",".join(ports) if ports else "-"
      lines.append(
          f"  {vlan_id:<5} {str(vlan_type or 'standard'):<9}"
          f" {str(vlan_attr or 'common'):<10} {port_text:<24} {desc or '-'}"
      )
    lines.append("  ------------------------------------------------------------------")
    lines.append(f"  Total: {len(selected)}")
    return "\n".join(lines)
  finally:
    conn.close()


def display_port_vlan(username, node_name, cmd, default_board="0/2"):
  text = " ".join(cmd.split())
  tokens = text.split()
  if len(tokens) < 4:
    return "\n  Failure: Use display port vlan <frame/slot/port>."
  fsp = tokens[3]
  if fsp.count("/") != 2:
    return "\n  Failure: Port harus dalam format frame/slot/port, mis. 0/2/0."
  board, _, port_text = fsp.rpartition("/")
  port_id = int(port_text)

  conn = get_connection()
  try:
    # VLAN 1 selalu ada di setiap port (VLAN default).
    vlans = [1]
    for row in conn.execute(
        "SELECT vlan_id FROM olt_node_vlan_ports"
        " WHERE username = ? AND node_name = ? AND port = ? ORDER BY vlan_id",
        (username, node_name, fsp),
    ).fetchall():
      if row[0] not in vlans:
        vlans.append(row[0])
    bound = conn.execute(
        "SELECT vlan_id FROM olt_vlan WHERE username = ? AND bound_port = ?",
        (username, fsp),
    ).fetchone()
    if bound and bound[0] not in vlans:
      vlans.append(bound[0])
    native = conn.execute(
        "SELECT vlan_id FROM olt_eth_native_vlans"
        " WHERE username = ? AND node_name = ? AND board = ? AND port_id = ?",
        (username, node_name, board, port_id),
    ).fetchone()
    native_vlan = native[0] if native else None
    if native_vlan and native_vlan not in vlans:
      vlans.append(native_vlan)
    vlans.sort()
  finally:
    conn.close()

  lines = [
      "",
      "  Command:",
      f"          {text}",
      "  ---------------------------------------",
  ]
  if vlans:
    lines.append("   " + "   ".join(f"{vlan:>4}" for vlan in vlans))
  else:
    lines.append("   (tidak ada VLAN pada port ini)")
  lines.extend([
      "  ---------------------------------------",
      f"  Total: {len(vlans)}",
      f"  Native VLAN: {native_vlan if native_vlan else 1}",
      "  -------------------------------------------------------",
      "  Outer VLAN  Inner VLAN  Src F/ S/ P  Dst TYPE  Dst PARA",
      "  -------------------------------------------------------",
      "  -------------------------------------------------------",
      "  Total VLAN-connect number on this port: 0",
  ])
  return "\n".join(lines)
