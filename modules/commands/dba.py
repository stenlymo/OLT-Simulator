"""DBA profile command parsing and persistence for the OLT simulator."""
import shlex

from modules.database import get_connection

BW_MIN = 128
BW_MAX = 50_000_000
FREQUENCIES = {
    "calculate-period", "single-frame", "1/2", "1/4", "1/8", "1/16", "1/32"
}
MULTIBURST_FREQUENCIES = {"1/2", "1/4", "1/8", "1/16", "1/32"}


def needs_multiburst_confirmation(command):
  tokens = _tokens(command)
  return (_option(tokens, "assignment-frequency", "single-frame").lower()
          in MULTIBURST_FREQUENCIES)


class DBACommandError(ValueError):
  pass


def _tokens(command):
  try:
    return [token.lower() if i < 2 else token for i, token in enumerate(shlex.split(command))]
  except ValueError as exc:
    raise DBACommandError("Invalid quoting in command.") from exc


def _option(tokens, key, default=None):
  for index, token in enumerate(tokens[:-1]):
    if token.lower() == key.lower():
      return tokens[index + 1]
  return default


def _number(value, label, low, high):
  try:
    value = int(value)
  except (TypeError, ValueError) as exc:
    raise DBACommandError(f"{label} must be a number from {low} to {high}.") from exc
  if not low <= value <= high:
    raise DBACommandError(f"{label} must be in range {low}-{high}.")
  # Huawei rounds configured bandwidth down to a 64-kbit/s boundary.
  return (value // 64) * 64


def _parse_profile(tokens, existing=None):
  ptype = _option(tokens, "type")
  if ptype is None:
    ptype = _option(tokens, "type1") and "type1"
  if ptype is None:
    for candidate in tokens:
      if candidate.lower() in {f"type{i}" for i in range(1, 6)}:
        ptype = candidate.lower()
        break
  if ptype is None and existing:
    ptype = existing.get("type")
  if ptype is None:
    raise DBACommandError("Specify one of type1, type2, type3, type4, or type5.")
  ptype = ptype.lower()
  if ptype not in {f"type{i}" for i in range(1, 6)}:
    raise DBACommandError("DBA type must be type1 through type5.")

  def bw(key, old=0):
    raw = _option(tokens, key)
    return _number(raw, key, BW_MIN, BW_MAX) if raw is not None else old

  old = existing or {}
  profile = {
      "type": ptype,
      "fix_bw": bw("fix", old.get("fix_bw", 0)),
      "assured": bw("assure", old.get("assured", 0)),
      "max_bw": bw("max", old.get("max_bw", 0)),
      "bandwidth_compensate": (_option(tokens, "bandwidth_compensate", old.get("bandwidth_compensate", "no"))).lower(),
      "fix_delay": (_option(tokens, "fix-delay", old.get("fix_delay", "no"))).lower(),
      "additional_bandwidth": (_option(tokens, "additional-bandwidth", old.get("additional_bandwidth", "best-effort"))).lower(),
      "priority": _option(tokens, "priority", old.get("priority", 0)),
      "weight": _option(tokens, "weight", old.get("weight", 10)),
      "assignment_frequency": (_option(tokens, "assignment-frequency", old.get("assignment_frequency", "single-frame"))).lower(),
  }
  if ptype in ("type1", "type5") and not profile["fix_bw"]:
    raise DBACommandError(f"{ptype} requires fix <bandwidth>.")
  if ptype in ("type2", "type3", "type5") and not profile["assured"]:
    raise DBACommandError(f"{ptype} requires assure <bandwidth>.")
  if ptype in ("type3", "type4", "type5") and not profile["max_bw"]:
    raise DBACommandError(f"{ptype} requires max <bandwidth>.")
  if ptype == "type3" and profile["max_bw"] < profile["assured"]:
    raise DBACommandError("For type3, maximum bandwidth must be at least assured bandwidth.")
  if ptype == "type5" and profile["max_bw"] < profile["fix_bw"] + profile["assured"]:
    raise DBACommandError("For type5, maximum bandwidth must be at least fixed plus assured bandwidth.")
  if profile["bandwidth_compensate"] not in ("yes", "no") or profile["fix_delay"] not in ("yes", "no"):
    raise DBACommandError("bandwidth_compensate and fix-delay accept yes or no.")
  if profile["additional_bandwidth"] not in ("non-assure", "best-effort", "both"):
    raise DBACommandError("additional-bandwidth must be non-assure, best-effort, or both.")
  if profile["assignment_frequency"] not in FREQUENCIES:
    raise DBACommandError("Invalid assignment-frequency value.")
  try:
    profile["priority"] = int(profile["priority"])
    profile["weight"] = int(profile["weight"])
  except (TypeError, ValueError) as exc:
    raise DBACommandError("priority and weight must be numeric.") from exc
  if not 0 <= profile["priority"] <= 7 or not 1 <= profile["weight"] <= 10000:
    raise DBACommandError("priority range is 0-7 and weight range is 1-10000.")
  if ptype == "type1" and (profile["assured"] or profile["max_bw"]):
    profile["assured"] = profile["max_bw"] = 0
  elif ptype == "type2":
    profile["fix_bw"] = profile["max_bw"] = 0
  elif ptype == "type3":
    profile["fix_bw"] = 0
  elif ptype == "type4":
    profile["fix_bw"] = profile["assured"] = 0
  if ptype != "type1":
    profile["bandwidth_compensate"] = profile["fix_delay"] = "no"
  if ptype not in ("type4", "type5") or (ptype == "type5" and profile["additional_bandwidth"] == "non-assure"):
    profile["priority"] = 0
    profile["weight"] = 10
  if ptype != "type5":
    profile["additional_bandwidth"] = "best-effort"
  return profile


def _resolve_profile(cursor, username, tokens):
  profile_id = _option(tokens, "profile-id")
  name = _option(tokens, "profile-name")
  if profile_id is None and name is None:
    raise DBACommandError("Specify profile-id or profile-name.")
  if profile_id is not None:
    try:
      profile_id = int(profile_id)
    except ValueError as exc:
      raise DBACommandError("Profile ID must be numeric.") from exc
    cursor.execute("SELECT * FROM olt_dba_profiles WHERE username=? AND profile_id=?", (username, profile_id))
  else:
    cursor.execute("SELECT * FROM olt_dba_profiles WHERE username=? AND profile_name=?", (username, name))
  row = cursor.fetchone()
  if row is None:
    raise DBACommandError("DBA profile was not found.")
  columns = [row[1] for row in cursor.execute("PRAGMA table_info(olt_dba_profiles)")]
  return dict(zip(columns, row))


def get_dba_profile(username, selector_command):
  """Return one DBA profile for the terminal's modify wizard."""
  tokens = _tokens(selector_command)
  conn = get_connection()
  try:
    return _resolve_profile(conn.cursor(), username, tokens)
  finally:
    conn.close()


def execute_dba_command(username, command, mode="config"):
  """Execute documented DBA add/delete/display commands and return CLI output."""
  tokens = _tokens(command)
  lowered = [token.lower() for token in tokens]
  if not tokens:
    raise DBACommandError("Empty command.")
  is_display = lowered[0] == "display"
  if is_display:
    if mode not in ("privileged", "config", "interface", "lineprofile"):
      raise DBACommandError("Run display dba-profile in privilege, global config, GPON, or line-profile mode.")
  elif mode != "config":
    raise DBACommandError("Run DBA profile configuration commands in global config mode.")

  conn = get_connection()
  try:
    cursor = conn.cursor()
    if is_display:
      if len(tokens) < 3 or lowered[1] != "dba-profile":
        raise DBACommandError("Use display dba-profile all|profile-id <id>|profile-name <name>.")
      if lowered[2] == "all":
        cursor.execute("SELECT * FROM olt_dba_profiles WHERE username=? ORDER BY profile_id", (username,))
        rows = cursor.fetchall()
        cols = [r[1] for r in cursor.execute("PRAGMA table_info(olt_dba_profiles)")]
        rows = [dict(zip(cols, row)) for row in rows]
        if "system" in lowered:
          lines = ["  Profile-ID  Profile-name                         System-info  Global profile-ID", "  -----------------------------------------------------------------------------"]
          for row in rows:
            system_info = "default" if row["profile_id"] < 10 else "current"
            suffix = "[Default&Reserve]" if row["profile_id"] == 0 else ""
            lines.append(f"  {row['profile_id']:<11} {row['profile_name'] + suffix:<37} {system_info:<12} {row['profile_id']}")
          lines.append("  -----------------------------------------------------------------------------")
          return f"\n  Command:\n          {command}\n" + "\n".join(lines)
        if "detail" in lowered or len(lowered) > 3 and lowered[3] == "detail":
          body = []
          for row in rows:
            body.extend(_format_detail(cursor, username, row))
            body.append("  -----------------------------------------------------------------------------")
          return f"\n  Command:\n          {command}\n" + "\n".join(body)
        lines = ["  Profile-ID   type   Bandwidth compensation   Fix(kbps)   Assure(kbps)   Max(kbps)   Bind times", "  ------------------------------------------------------------------------------------------"]
        for row in rows:
          bind = _bind_count(cursor, username, row["profile_id"], row["bind_count"])
          lines.append(f"  {row['profile_id']:>9}   {row['type']:<5}  {row['bandwidth_compensate'].title():>8} {row['fix_bw']:>12} {row['assured']:>14} {row['max_bw']:>11} {bind:>12}")
        lines.append("  ------------------------------------------------------------------------------------------")
        lines.append(f"  Total number of DBA profiles is {len(rows)}")
        return f"\n  Command:\n          {command}\n" + "\n".join(lines)
      row = _resolve_profile(cursor, username, tokens)
      return f"\n  Command:\n          {command}\n" + "\n".join(_format_detail(cursor, username, row))

    if len(tokens) < 2 or lowered[0] != "dba-profile":
      raise DBACommandError("Unknown DBA profile command.")
    operation = lowered[1]
    if operation == "add":
      raw_id = _option(tokens, "profile-id")
      if raw_id is None:
        cursor.execute("SELECT profile_id FROM olt_dba_profiles WHERE username=? AND profile_id BETWEEN 10 AND 515 ORDER BY profile_id", (username,))
        used = {row[0] for row in cursor.fetchall()}
        raw_id = next((str(i) for i in range(10, 516) if i not in used), None)
      if raw_id is None:
        raise DBACommandError("No available DBA profile IDs remain (10-515).")
      p_id = int(raw_id)
      if not 10 <= p_id <= 515:
        raise DBACommandError("New DBA profile ID must be in range 10-515.")
      if cursor.execute("SELECT 1 FROM olt_dba_profiles WHERE username=? AND profile_id=?", (username, p_id)).fetchone():
        raise DBACommandError(f"DBA profile ID {p_id} already exists.")
      name = _option(tokens, "profile-name", f"dba-profile_{p_id}")
      if not 1 <= len(name) <= 33 or "?" in name:
        raise DBACommandError("Profile name must contain 1-33 visible characters and cannot contain '?'.")
      if cursor.execute("SELECT 1 FROM olt_dba_profiles WHERE username=? AND profile_name=?", (username, name)).fetchone():
        raise DBACommandError(f"DBA profile name '{name}' already exists.")
      profile = _parse_profile(tokens)
      cols = ["username", "profile_id", "profile_name", *profile.keys(), "bind_count"]
      vals = [username, p_id, name, *profile.values(), 0]
      cursor.execute(f"INSERT INTO olt_dba_profiles ({','.join(cols)}) VALUES ({','.join('?' for _ in vals)})", vals)
      conn.commit()
      warning = "\n  The profile is configured with single-frame multiburst. Confirm ONT capability before applying." if profile["assignment_frequency"] in ("1/2", "1/4", "1/8", "1/16", "1/32") else ""
      return f"\n  Command:\n          {command}{warning}\n  Adding a DBA profile succeeded\n  Profile ID  : {p_id}\n  Profile name: {name}"
    if operation == "delete":
      profile = _resolve_profile(cursor, username, tokens)
      if profile["profile_id"] < 10:
        raise DBACommandError("Default DBA profiles (ID 0-9) cannot be deleted.")
      bind = _bind_count(cursor, username, profile["profile_id"], profile["bind_count"])
      if bind:
        raise DBACommandError("DBA profile is bound to a line profile; unbind it before deleting.")
      cursor.execute("DELETE FROM olt_dba_profiles WHERE username=? AND profile_id=?", (username, profile["profile_id"]))
      conn.commit()
      return f"\n  Command:\n          {command}\n  DBA profile {profile['profile_id']} deleted."
    if operation == "modify":
      profile = _resolve_profile(cursor, username, tokens)
      update_tokens = tokens[2:]
      if len(update_tokens) <= 2:
        raise DBACommandError("This simulator requires modification values in the command, for example: dba-profile modify profile-id 20 max 30016.")
      updated = _parse_profile(update_tokens, profile)
      cursor.execute("UPDATE olt_dba_profiles SET type=?, fix_bw=?, assured=?, max_bw=?, bandwidth_compensate=?, fix_delay=?, additional_bandwidth=?, priority=?, weight=?, assignment_frequency=? WHERE username=? AND profile_id=?", (*updated.values(), username, profile["profile_id"]))
      conn.commit()
      return f"\n  Command:\n          {command}\n  DBA profile {profile['profile_id']} modified successfully."
    raise DBACommandError("Supported operations: add, delete, modify, and display.")
  finally:
    conn.close()


def _bind_count(cursor, username, profile_id, fallback=0):
  count = 0
  try:
    count += cursor.execute("SELECT COUNT(*) FROM olt_line_profile_tconts WHERE username=? AND dba_profile_id=?", (username, profile_id)).fetchone()[0]
  except Exception:
    pass
  try:
    count = max(count, cursor.execute("SELECT COUNT(*) FROM olt_line_profile_details WHERE username=? AND dba_profile_id=?", (username, profile_id)).fetchone()[0])
  except Exception:
    pass
  return count or (fallback or 0)


def _format_detail(cursor, username, row):
  bind = _bind_count(cursor, username, row["profile_id"], row.get("bind_count", 0))
  freq = row.get("assignment_frequency") or "single-frame"
  return [
      "  -----------------------------------------------------------------------------",
      f"  Profile-name :            {row['profile_name']}",
      f"  Profile-ID:               {row['profile_id']}",
      f"  Assignment-frequency:     {freq}",
      f"  type:                     {row['type']}",
      f"  Bandwidth compensation:   {row.get('bandwidth_compensate', 'no').title()}",
      f"  Fix-delay:                {row.get('fix_delay', 'no').title()}",
      f"  Fix(kbps):                {row.get('fix_bw', 0)}",
      f"  Assure(kbps):             {row['assured']}",
      f"  Max(kbps):                {row['max_bw']}",
      f"  Additional-bandwidth:     {row.get('additional_bandwidth', 'best-effort')}",
      f"  Best-effort-priority:     {row.get('priority', 0)}",
      f"  Best-effort-weight:       {row.get('weight', 10)}",
      f"  bind-times:               {bind}",
  ]


def handle_dba_profile_add(username, cmd, current_mode):
  mode = {"config": "config", "interface": "interface"}.get(current_mode, current_mode)
  try:
    print(execute_dba_command(username, cmd, mode))
  except DBACommandError as exc:
    print(f"Failure: {exc}")


def handle_display_dba_profile(username, cmd, current_mode="privileged"):
  mode = {"privileged": "privileged", "interface": "interface", "lineprofile": "lineprofile"}.get(current_mode, current_mode)
  try:
    print(execute_dba_command(username, cmd, mode))
  except DBACommandError as exc:
    print(f"Failure: {exc}")
