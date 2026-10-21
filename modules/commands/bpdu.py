"""BPDU tunnel configuration for VLANs and VLAN service profiles."""
import re

from modules.database import get_connection


def configure(username, cmd):
  match = re.fullmatch(
      r"bpdu tunnel vlan (\d+)(?: to (\d+))?(?: dedicated-net-id (\d+))? (enable|disable)",
      " ".join(cmd.strip().split()), re.I)
  if not match:
    return "  Failure: Use bpdu tunnel vlan <ID> [to <END-ID>] [dedicated-net-id <1-8>] {enable|disable}."
  start, end, dnet, action = match.groups()
  start = int(start); end = int(end) if end else start
  dnet = int(dnet) if dnet else None
  action = action.lower()
  if not 1 <= start <= 4093 or not start <= end <= 4093:
    return "  Failure: VLAN IDs must be in range 1-4093 and end VLAN must not be less than start VLAN."
  if dnet is not None and not 1 <= dnet <= 8:
    return "  Failure: dedicated-net-id must be in range 1-8."
  conn = get_connection()
  try:
    if dnet is None:
      rows = conn.execute("SELECT vlan_id FROM olt_vlan WHERE username=? AND vlan_id BETWEEN ? AND ? ORDER BY vlan_id", (username, start, end)).fetchall()
    else:
      rows = conn.execute("SELECT vlan_id FROM olt_vlan WHERE username=? AND vlan_id BETWEEN ? AND ? AND dedicated_net_id=? ORDER BY vlan_id", (username, start, end, dnet)).fetchall()
    ids = [row[0] for row in rows]
    if not ids:
      return "  Failure: No existing VLAN matches the requested ID range and dedicated network."
    conn.executemany(
        "INSERT OR REPLACE INTO olt_system_config(username,key,value) VALUES (?,?,?)",
        [(username, f"bpdu_tunnel_vlan_{vlan_id}", action) for vlan_id in ids],
    )
    conn.commit()
  finally:
    conn.close()
  return (f"\n  Command:\n          {cmd}\n"
          f"  It will take several minutes to set BPDU tunnel, please wait...\n"
          f"  The total of {action} BPDU tunnel succeed VLAN: {len(ids)}\n"
          "  The total of failed VLAN: 0")


def is_enabled(username, vlan_id):
  """Resolve a VLAN profile override first, then its global BPDU setting."""
  conn = get_connection()
  try:
    binding = conn.execute(
        "SELECT profile_id FROM olt_vlan_service_profile_bindings WHERE username=? AND vlan_id=?",
        (username, vlan_id),
    ).fetchone()
    if binding:
      row = conn.execute(
          "SELECT settings_json FROM olt_vlan_service_profiles WHERE username=? AND profile_id=?",
          (username, binding[0]),
      ).fetchone()
      if row:
        import json
        configured = json.loads(row[0] or "{}")
        if "bpdu_tunnel" in configured:
          return configured["bpdu_tunnel"] == "enable"
    row = conn.execute(
        "SELECT value FROM olt_system_config WHERE username=? AND key=?",
        (username, f"bpdu_tunnel_vlan_{vlan_id}"),
    ).fetchone()
    return bool(row and row[0].lower() == "enable")
  finally:
    conn.close()


def display_config(username, cmd="display bpdu tunnel config"):
  conn = get_connection()
  try:
    rows = conn.execute(
        "SELECT vlan_id, COALESCE(dedicated_net_id,0) FROM olt_vlan "
        "WHERE username=? ORDER BY dedicated_net_id,vlan_id", (username,),
    ).fetchall()
  finally:
    conn.close()
  groups = {}
  for vlan_id, dedicated_net_id in rows:
    if is_enabled(username, vlan_id):
      groups.setdefault(dedicated_net_id or 0, []).append(vlan_id)
  lines = [f"\n  Command:\n          {cmd}"]
  if groups.get(0):
    vlans = groups[0]
    lines.extend([
        "  The VLAN info of enable bpdu tunnel:",
        "  --------------------------------------------------------------------",
        "     " + "  ".join(map(str, vlans)),
        "  --------------------------------------------------------------------",
        f"  The VLAN number of enable BPDU tunnel: {len(vlans)}",
    ])
  else:
    lines.extend([
        "  The VLAN info of enable bpdu tunnel:",
        "  --------------------------------------------------------------------",
        "     -",
        "  --------------------------------------------------------------------",
        "  The VLAN number of enable BPDU tunnel: 0",
    ])
  for dedicated_net_id in sorted(key for key in groups if key):
    vlans = groups[dedicated_net_id]
    lines.extend([
        f"  The VLAN info of enable BPDU tunnel in dedicated-net-id {dedicated_net_id}:",
        "  --------------------------------------------------------------------",
        "     " + "  ".join(map(str, vlans)),
        "  --------------------------------------------------------------------",
        f"  The VLAN number of enable BPDU tunnel: {len(vlans)}",
    ])
  return "\n".join(lines)


def display_vlan(username, cmd):
  match = re.fullmatch(r"display bpdu tunnel vlan (\d+)(?: dedicated-net-id (\d+))?", " ".join(cmd.strip().split()), re.I)
  if not match:
    return "  Failure: Use display bpdu tunnel vlan <VLAN-ID> [dedicated-net-id <1-8>]."
  vlan_id = int(match.group(1))
  dnet = int(match.group(2)) if match.group(2) else None
  if not 1 <= vlan_id <= 4093:
    return "  Failure: VLAN ID must be in range 1-4093."
  if dnet is not None and not 1 <= dnet <= 8:
    return "  Failure: dedicated-net-id must be in range 1-8."
  conn = get_connection()
  try:
    row = conn.execute("SELECT COALESCE(dedicated_net_id,0) FROM olt_vlan WHERE username=? AND vlan_id=?", (username, vlan_id)).fetchone()
  finally:
    conn.close()
  if not row:
    return f"  Failure: VLAN {vlan_id} does not exist."
  if dnet is not None and row[0] != dnet:
    return f"  Failure: VLAN {vlan_id} does not belong to dedicated-net-id {dnet}."
  status = "enabled" if is_enabled(username, vlan_id) else "disabled"
  return f"\n  Command:\n          {cmd}\n  The VLAN is {status} with BPDU tunnel"
