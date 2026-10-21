"""VLAN service-profile persistence and command handling."""
import json
import re

from modules.database import get_connection


def ensure_schema():
  conn = get_connection()
  try:
    conn.execute("""CREATE TABLE IF NOT EXISTS olt_vlan_service_profiles (
      username TEXT NOT NULL, profile_id INTEGER NOT NULL,
      settings_json TEXT NOT NULL DEFAULT '{}', pending_json TEXT NOT NULL DEFAULT '{}',
      PRIMARY KEY(username,profile_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS olt_vlan_service_profile_bindings (
      username TEXT NOT NULL, vlan_id INTEGER NOT NULL, profile_id INTEGER NOT NULL,
      PRIMARY KEY(username,vlan_id))""")
    conn.commit()
  finally:
    conn.close()


ensure_schema()


def _json(value):
  try:
    return json.loads(value or "{}")
  except (ValueError, TypeError):
    return {}


def enter(username, cmd):
  match = re.fullmatch(r"vlan service-profile profile-id (\d+)", cmd.strip(), re.I)
  if not match:
    return None
  profile_id = int(match.group(1))
  if not 1 <= profile_id <= 4096:
    return False, "VLAN service-profile ID must be in range 1-4096."
  conn = get_connection()
  try:
    conn.execute("INSERT OR IGNORE INTO olt_vlan_service_profiles(username,profile_id) VALUES (?,?)", (username, profile_id))
    conn.commit()
  finally:
    conn.close()
  return profile_id, ""


def handle(username, cmd, profile_id):
  text = " ".join(cmd.strip().split())
  low = text.lower()
  conn = get_connection()
  try:
    row = conn.execute("SELECT settings_json,pending_json FROM olt_vlan_service_profiles WHERE username=? AND profile_id=?", (username, profile_id)).fetchone()
    if not row:
      return "  Failure: VLAN service profile does not exist."
    active, pending = _json(row[0]), _json(row[1])
    if low == "commit":
      for key, value in pending.items():
        if value == "__unset__":
          active.pop(key, None)
        else:
          active[key] = value
      conn.execute("UPDATE olt_vlan_service_profiles SET settings_json=?,pending_json='{}' WHERE username=? AND profile_id=?", (json.dumps(active, sort_keys=True), username, profile_id))
      pitp = conn.execute("SELECT value FROM olt_system_config WHERE username=? AND key=?", (username, f"pitp_vlan_profile_pending_{profile_id}")).fetchone()
      if pitp:
        conn.execute("INSERT OR REPLACE INTO olt_system_config(username,key,value) VALUES (?,?,?)", (username, f"pitp_vlan_profile_{profile_id}", pitp[0]))
        conn.execute("DELETE FROM olt_system_config WHERE username=? AND key=?", (username, f"pitp_vlan_profile_pending_{profile_id}"))
      conn.commit()
      return "  VLAN service profile committed successfully."
    if low.startswith("display vlan service-profile"):
      return f"\n  Command:\n          {cmd}\n  Profile-ID : {profile_id}\n  Committed settings: {json.dumps(active, sort_keys=True)}"
    pitp_match = re.fullmatch(r"pitp (enable|disable)", text, re.I)
    if pitp_match:
      value = pitp_match.group(1).lower()
      conn.execute("INSERT OR REPLACE INTO olt_system_config(username,key,value) VALUES (?,?,?)", (username, f"pitp_vlan_profile_pending_{profile_id}", value))
      conn.commit()
      return "\n  Command:\n          " + cmd + "\n  Info: Please use the commit command to make modifications take effect"
    bpdu_match = re.fullmatch(r"bpdu tunnel (enable|disable)", text, re.I)
    if bpdu_match:
      pending["bpdu_tunnel"] = bpdu_match.group(1).lower()
      conn.execute("UPDATE olt_vlan_service_profiles SET pending_json=? WHERE username=? AND profile_id=?", (json.dumps(pending, sort_keys=True), username, profile_id))
      conn.commit()
      return "\n  Command:\n          " + cmd + "\n  Info: Please use the commit command to make modifications take effect"
    if low.startswith("undo "):
      undo_command = low[5:].strip()
      key = "bpdu_tunnel" if undo_command == "bpdu tunnel" else undo_command.split()[0]
      if key in active or key in pending:
        pending[key] = "__unset__"
      else:
        pending.pop(key, None)
    else:
      pieces = text.split(None, 1)
      key = pieces[0].lower()
      value = pieces[1] if len(pieces) > 1 else "enable"
      supported = {"forwarding", "user-bridging", "packet-policy", "igmp", "dhcp", "dhcpv6", "pppoe", "vmac", "bpdu", "vtp-cdp", "rip", "l3-protocol", "ipv6", "security"}
      if key not in supported:
        return "  Failure: Unsupported VLAN service-profile command."
      pending[key] = value
    conn.execute("UPDATE olt_vlan_service_profiles SET pending_json=? WHERE username=? AND profile_id=?", (json.dumps(pending, sort_keys=True), username, profile_id))
    conn.commit()
    return "\n  Command:\n          " + cmd + "\n  Info: Please use the commit command to make modifications take effect"
  finally:
    conn.close()


def bind_vlan(username, cmd):
  match = re.fullmatch(r"vlan bind service-profile (\d+) profile-id (\d+)", cmd.strip(), re.I)
  if not match:
    return None
  vlan_id, profile_id = map(int, match.groups())
  conn = get_connection()
  try:
    if not conn.execute("SELECT 1 FROM olt_vlan WHERE username=? AND vlan_id=?", (username, vlan_id)).fetchone():
      return "  Failure: VLAN does not exist."
    if not conn.execute("SELECT 1 FROM olt_vlan_service_profiles WHERE username=? AND profile_id=?", (username, profile_id)).fetchone():
      return "  Failure: VLAN service profile does not exist."
    conn.execute("INSERT OR REPLACE INTO olt_vlan_service_profile_bindings(username,vlan_id,profile_id) VALUES (?,?,?)", (username, vlan_id, profile_id))
    conn.commit()
    return f"\n  Command:\n          {cmd}\n  VLAN {vlan_id} bound to service profile {profile_id}."
  finally:
    conn.close()


def display(username, cmd):
  match = re.fullmatch(r"display vlan service-profile(?: profile-id (\d+))?", cmd.strip(), re.I)
  if not match:
    return "  Failure: Use display vlan service-profile [profile-id <ID>]."
  profile_id = int(match.group(1)) if match.group(1) else None
  conn = get_connection()
  try:
    query = "SELECT profile_id,settings_json,pending_json FROM olt_vlan_service_profiles WHERE username=?"
    args = [username]
    if profile_id is not None:
      query += " AND profile_id=?"
      args.append(profile_id)
    query += " ORDER BY profile_id"
    rows = conn.execute(query, args).fetchall()
    bindings = conn.execute("SELECT vlan_id,profile_id FROM olt_vlan_service_profile_bindings WHERE username=? ORDER BY vlan_id", (username,)).fetchall()
  finally:
    conn.close()
  lines = [f"\n  Command:\n          {cmd}", "  Profile-ID  VLAN bindings  Committed settings", "  ------------------------------------------------------------"]
  if profile_id is not None and not rows:
    return "  Failure: VLAN service profile does not exist."
  for pid, settings, pending in rows:
    vlans = ",".join(str(vlan) for vlan, bound_pid in bindings if bound_pid == pid) or "-"
    lines.append(f"  {pid:<11} {vlans:<14} {_json(settings)}")
    if _json(pending):
      lines.append(f"  Pending changes: {_json(pending)}")
  lines.extend(["  ------------------------------------------------------------", f"  Total: {len(rows)}"])
  return "\n".join(lines)
