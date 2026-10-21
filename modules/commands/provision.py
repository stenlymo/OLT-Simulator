"""Perintah provisioning ONT untuk CLI terminal & REST API.

Melengkapi handler lama yang belum mendukung sintaks Huawei sebenarnya:
  ont-srvprofile gpon ...
  ont-lineprofile (tcont / gem add / gem mapping / commit)
  ont add <port> <ont> sn-auth <sn> omci ont-lineprofile-id ... ont-srvprofile-id ...
  service-port [index] vlan <v> gpon <f/s/p> ont <i> gemport <g> multi-service user-vlan <v>
"""

from __future__ import annotations

import re

from modules.database import get_connection


def _rows(conn, sql, params=()):
  return conn.execute(sql, params).fetchall()


def _profile_id_by_name(conn, table, username, name):
  row = conn.execute(
      f"SELECT profile_id FROM {table} WHERE username = ? AND profile_name = ?",
      (username, name),
  ).fetchone()
  return row[0] if row else None


def _next_sp_id(conn, username):
  row = conn.execute(
      "SELECT COALESCE(MAX(sp_id), 0) + 1 FROM olt_service_ports WHERE username = ?",
      (username,),
  ).fetchone()
  return row[0] if row else 1


# --------------------------------------------------------------------------- #
# ONT service profile
# --------------------------------------------------------------------------- #
def create_srvprofile(username, cmd, gpon_port="0/1"):
  parts = cmd.split()
  profile_id = None
  profile_name = None
  for index, token in enumerate(parts):
    low = token.lower()
    if low == "profile-id" and index + 1 < len(parts):
      profile_id = int(parts[index + 1])
    elif low == "profile-name" and index + 1 < len(parts):
      profile_name = parts[index + 1].strip('"')

  conn = get_connection()
  try:
    if profile_id is None:
      row = conn.execute(
          "SELECT COALESCE(MAX(profile_id), -1) + 1 FROM olt_service_profiles"
          " WHERE username = ?",
          (username,),
      ).fetchone()
      profile_id = row[0]
    if not profile_name:
      profile_name = f"service_profile_{profile_id}"
    conn.execute(
        "INSERT OR REPLACE INTO olt_service_profiles"
        " (username, profile_id, profile_name, eth, pots, catv)"
        " VALUES (?, ?, ?, 0, 0, 0)",
        (username, profile_id, profile_name),
    )
    conn.commit()
  finally:
    conn.close()
  return profile_id, profile_name


def srvprofile_submode(username, cmd, profile_id):
  low = cmd.lower().strip()
  if low in ("quit", "exit", "return"):
    return None  # ditangani engine

  conn = get_connection()
  try:
    if low.startswith("ont-port"):
      eth = pots = catv = None
      tokens = cmd.split()
      for index, token in enumerate(tokens):
        key = token.lower()
        if key == "eth" and index + 1 < len(tokens):
          value = tokens[index + 1]
          eth = 8 if value.lower() == "adaptive" else int(value)
        elif key == "pots" and index + 1 < len(tokens):
          value = tokens[index + 1]
          pots = 32 if value.lower() == "adaptive" else int(value)
        elif key == "catv" and index + 1 < len(tokens):
          value = tokens[index + 1]
          catv = 1 if value.lower() == "adaptive" else int(value)
      current = conn.execute(
          "SELECT eth, pots, catv FROM olt_service_profiles"
          " WHERE username = ? AND profile_id = ?",
          (username, profile_id),
      ).fetchone() or (0, 0, 0)
      conn.execute(
          "UPDATE olt_service_profiles SET eth = ?, pots = ?, catv = ?"
          " WHERE username = ? AND profile_id = ?",
          (
              eth if eth is not None else current[0],
              pots if pots is not None else current[1],
              catv if catv is not None else current[2],
              username,
              profile_id,
          ),
      )
      conn.commit()
      return f"  Ont-port capability updated (eth={eth if eth is not None else current[0]}, pots={pots if pots is not None else current[1]})."

    if low.startswith("commit"):
      return f"  Service profile {profile_id} committed successfully."

    # Parameter lain (native-vlan, transparent, mac-learning, ...) diterima
    # sebagai konfigurasi tersimpan agar alur tidak terputus.
    key = re.split(r"\s+", low, maxsplit=1)[0]
    conn.execute(
        "INSERT OR REPLACE INTO olt_service_profile_settings"
        " (username, profile_id, settings_json) VALUES (?, ?, ?)",
        (username, profile_id, f'{{"{key}": "{cmd}"}}'),
    )
    conn.commit()
    return f"  {cmd}"
  finally:
    conn.close()


# --------------------------------------------------------------------------- #
# ONT line profile
# --------------------------------------------------------------------------- #
def lineprofile_submode(username, cmd, profile_id):
  low = cmd.lower().strip()
  if low in ("quit", "exit", "return"):
    return None

  conn = get_connection()
  try:
    if low.startswith("tcont"):
      tokens = cmd.split()
      tcont_id = int(tokens[1])
      dba_id = None
      for index, token in enumerate(tokens):
        if token.lower() == "dba-profile-id" and index + 1 < len(tokens):
          dba_id = int(tokens[index + 1])
        elif token.lower() == "dba-profile-name" and index + 1 < len(tokens):
          dba_id = _profile_id_by_name(
              conn, "olt_dba_profiles", username, tokens[index + 1].strip('"')
          )
      if dba_id is None:
        return "  Failure: DBA profile tidak ditemukan."
      conn.execute(
          "INSERT OR REPLACE INTO olt_line_profile_tconts"
          " (username, profile_id, tcont_id, dba_profile_id) VALUES (?, ?, ?, ?)",
          (username, profile_id, tcont_id, dba_id),
      )
      conn.commit()
      return f"  T-CONT {tcont_id} bound to DBA profile {dba_id}."

    if low.startswith("gem add"):
      tokens = cmd.split()
      gem_id = int(tokens[2])
      service_type = tokens[3].lower() if len(tokens) > 3 else "eth"
      tcont_id = 1
      encrypt = "on"
      for index, token in enumerate(tokens):
        if token.lower() == "tcont" and index + 1 < len(tokens):
          tcont_id = int(tokens[index + 1])
        elif token.lower() == "encrypt" and index + 1 < len(tokens):
          encrypt = tokens[index + 1].lower()
      conn.execute(
          "INSERT OR REPLACE INTO olt_line_profile_gems"
          " (username, profile_id, gem_id, service_type, tcont_id, encrypt,"
          " cascade, priority_queue, downstream_priority_queue, gem_car_index)"
          " VALUES (?, ?, ?, ?, ?, ?, 'off', 0, 'adapt', '')",
          (username, profile_id, gem_id, service_type, tcont_id, encrypt),
      )
      conn.commit()
      return f"  GEM {gem_id} added (tcont {tcont_id}, encrypt {encrypt})."

    if low.startswith("gem mapping"):
      tokens = cmd.split()
      gem_id = int(tokens[2])
      mapping_id = int(tokens[3]) if len(tokens) > 3 and tokens[3].isdigit() else 0
      match_type, match_value = "vlan", ""
      for index, token in enumerate(tokens):
        key = token.lower()
        if key in ("vlan", "priority", "user-vlan", "all") and index + 1 <= len(tokens):
          match_type = "priority" if key == "priority" else "vlan"
          match_value = tokens[index + 1] if index + 1 < len(tokens) else ""
          break
      conn.execute(
          "INSERT OR REPLACE INTO olt_line_profile_mappings"
          " (username, profile_id, gem_id, mapping_id, match_type, match_value, mapping_extra)"
          " VALUES (?, ?, ?, ?, ?, ?, '')",
          (username, profile_id, gem_id, mapping_id, match_type, match_value),
      )
      conn.commit()
      return f"  GEM {gem_id} mapping {mapping_id} -> {match_type} {match_value}."

    if low.startswith("vlan-bundle"):
      tokens = cmd.split()
      bundle_id = int(tokens[1]) if len(tokens) > 1 else 0
      vlans = " ".join(tokens[2:])
      conn.execute(
          "INSERT OR REPLACE INTO olt_line_profile_vlan_bundles"
          " (username, profile_id, bundle_id, vlan_list) VALUES (?, ?, ?, ?)",
          (username, profile_id, bundle_id, vlans),
      )
      conn.commit()
      return f"  VLAN bundle {bundle_id} saved."

    if low.startswith("commit"):
      return f"  Line profile {profile_id} committed successfully."

    # omcc/tr069/qos/mapping mode dan sejenisnya disimpan sebagai settings
    tokens = cmd.split()
    key = tokens[0].lower()
    value = " ".join(tokens[1:])
    current = conn.execute(
        "SELECT omcc_encrypt, tr069_enabled, tr069_ip_index, fec_upstream,"
        " qos_mode, mapping_mode, gem_encrypt_mode FROM olt_line_profile_settings"
        " WHERE username = ? AND profile_id = ?",
        (username, profile_id),
    ).fetchone() or ("on", 0, 0, "disable", "priority-queue", "vlan", "aes-128")
    settings = dict(
        omcc_encrypt=current[0],
        tr069_enabled=current[1],
        tr069_ip_index=current[2],
        fec_upstream=current[3],
        qos_mode=current[4],
        mapping_mode=current[5],
        gem_encrypt_mode=current[6],
    )
    if key == "omcc" and value.startswith("encrypt"):
      settings["omcc_encrypt"] = "on" if "on" in value else "off"
    elif key == "tr069-management":
      settings["tr069_enabled"] = 0 if "disable" in value else 1
      match = re.search(r"ip-index\s+(\d+)", value)
      if match:
        settings["tr069_ip_index"] = int(match.group(1))
    elif key == "fec-upstream":
      settings["fec_upstream"] = value.split()[-1]
    elif key == "qos-mode":
      settings["qos_mode"] = value.split()[-1]
    elif key == "mapping-mode":
      settings["mapping_mode"] = value.split()[-1]
    elif key == "gem" and value.startswith("encrypt-mode"):
      settings["gem_encrypt_mode"] = value.split()[-1]
    conn.execute(
        "INSERT OR REPLACE INTO olt_line_profile_settings"
        " (username, profile_id, omcc_encrypt, tr069_enabled, tr069_ip_index,"
        " fec_upstream, qos_mode, mapping_mode, gem_encrypt_mode)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            username,
            profile_id,
            settings["omcc_encrypt"],
            settings["tr069_enabled"],
            settings["tr069_ip_index"],
            settings["fec_upstream"],
            settings["qos_mode"],
            settings["mapping_mode"],
            settings["gem_encrypt_mode"],
        ),
    )
    conn.commit()
    return f"  Command:\n          {cmd}"
  finally:
    conn.close()


# --------------------------------------------------------------------------- #
# ont add
# --------------------------------------------------------------------------- #
def ont_add(username, cmd, gpon_port="0/1", node_name=None):
  from modules.commands.ont_status import refresh_ont_states
  tokens = cmd.split()
  if len(tokens) < 4:
    return "  Failure: Use ont add <port> <ont-id> sn-auth <sn> omci ..."

  try:
    port_id = int(tokens[2])
    ont_id = int(tokens[3])
  except ValueError:
    return "  Failure: Port ID dan ONT ID harus angka."

  fsp = f"{gpon_port}/{port_id}"
  sn = None
  auth_mode = "sn-auth"
  line_profile_id = None
  srv_profile_id = None
  description = ""

  index = 4
  line_profile_name = None
  srv_profile_name = None
  while index < len(tokens):
    key = tokens[index].lower()
    if key in ("sn-auth", "password-auth") and index + 1 < len(tokens):
      auth_mode = key
      sn = tokens[index + 1].strip('"')
      index += 2
    elif key == "ont-lineprofile-id" and index + 1 < len(tokens):
      line_profile_id = int(tokens[index + 1])
      index += 2
    elif key == "ont-srvprofile-id" and index + 1 < len(tokens):
      srv_profile_id = int(tokens[index + 1])
      index += 2
    elif key == "ont-lineprofile-name" and index + 1 < len(tokens):
      line_profile_name = tokens[index + 1].strip('"')
      index += 2
    elif key == "ont-srvprofile-name" and index + 1 < len(tokens):
      srv_profile_name = tokens[index + 1].strip('"')
      index += 2
    elif key == "desc" and index + 1 < len(tokens):
      description = " ".join(tokens[index + 1:]).strip('"')
      break
    else:
      index += 1

  if not sn:
    return "  Failure: SN/password ONT wajib diisi (sn-auth <sn>)."

  conn = get_connection()
  try:
    if line_profile_id is None and line_profile_name:
      line_profile_id = _profile_id_by_name(
          conn, "olt_line_profiles", username, line_profile_name
      )
      if line_profile_id is None:
        return f"  Failure: Line profile '{line_profile_name}' tidak ada."
    if srv_profile_id is None and srv_profile_name:
      srv_profile_id = _profile_id_by_name(
          conn, "olt_service_profiles", username, srv_profile_name
      )
      if srv_profile_id is None:
        return f"  Failure: Service profile '{srv_profile_name}' tidak ada."

    if line_profile_id is not None:
      row = conn.execute(
          "SELECT 1 FROM olt_line_profiles WHERE username = ? AND profile_id = ?",
          (username, line_profile_id),
      ).fetchone()
      if not row:
        return f"  Failure: Line profile {line_profile_id} tidak ada."
    if srv_profile_id is not None:
      row = conn.execute(
          "SELECT 1 FROM olt_service_profiles WHERE username = ? AND profile_id = ?",
          (username, srv_profile_id),
      ).fetchone()
      if not row:
        return f"  Failure: Service profile {srv_profile_id} tidak ada."

    existing = conn.execute(
        "SELECT ont_id, port, control_flag FROM olt_onts"
        " WHERE username = ? AND sn = ?",
        (username, sn),
    ).fetchone()

    if existing:
      conn.execute(
          "UPDATE olt_onts SET port = ?, ont_id = ?, control_flag = 'active',"
          " run_state = 'offline', config_state = 'normal', match_state = 'match',"
          " line_profile_id = COALESCE(?, line_profile_id),"
          " service_profile_id = COALESCE(?, service_profile_id),"
          " description = COALESCE(NULLIF(?, ''), description),"
          " auth_mode = ?, auth_value = ?"
          " WHERE username = ? AND sn = ?",
          (
              fsp,
              ont_id,
              line_profile_id,
              srv_profile_id,
              description,
              auth_mode,
              sn,
              username,
              sn,
          ),
      )
    else:
      conn.execute(
          "INSERT INTO olt_onts (username, port, ont_id, sn, control_flag,"
          " run_state, config_state, match_state, description, last_down_cause,"
          " line_profile_id, service_profile_id, auth_mode, auth_value,"
          " alarm_profile_id, autofind_state)"
          " VALUES (?, ?, ?, ?, 'active', 'offline', 'normal', 'match', ?,"
          " 'power-off', ?, ?, ?, ?, 1, 0)",
          (
              username,
              fsp,
              ont_id,
              sn,
              description,
              line_profile_id or 0,
              srv_profile_id or 0,
              auth_mode,
              sn,
          ),
      )

    conn.execute(
        "DELETE FROM olt_autofind WHERE username = ? AND sn = ?", (username, sn)
    )
    # bind_count hanya ada pada tabel line profile
    if line_profile_id is not None:
      conn.execute(
          "UPDATE olt_line_profiles SET bind_count = COALESCE(bind_count, 0) + 1"
          " WHERE username = ? AND profile_id = ?",
          (username, line_profile_id),
      )
    conn.commit()
  finally:
    conn.close()

  if node_name:
    refresh_ont_states(username, node_name)

  return (
      "  Number of ONTs that can be added: 1, success: 1\n"
      f"  PortID :{port_id}, ONTID :{ont_id}, SN :{sn}"
  )


# --------------------------------------------------------------------------- #
# service-port
# --------------------------------------------------------------------------- #
def service_port(username, cmd):
  tokens = cmd.split()
  if len(tokens) < 8:
    return (
        "  Failure: Use service-port [index] vlan <vlan> gpon <f/s/p> ont <id>"
        " gemport <gem> multi-service user-vlan <vlan>"
    )

  index = None
  vlan = port = ont_id = gemport = user_vlan = None
  tag_transform = "translate"

  i = 1
  if tokens[i].isdigit():
    index = int(tokens[i])
    i += 1

  def value_of(key):
    if i < len(tokens) and tokens[i].lower() == key:
      return tokens[i + 1] if i + 1 < len(tokens) else None
    return None

  while i < len(tokens):
    key = tokens[i].lower()
    if key == "vlan" and i + 1 < len(tokens):
      vlan = int(tokens[i + 1]); i += 2
    elif key == "gpon" and i + 1 < len(tokens):
      port = tokens[i + 1]; i += 2
    elif key == "ont" and i + 1 < len(tokens):
      ont_id = int(tokens[i + 1]); i += 2
    elif key == "gemport" and i + 1 < len(tokens):
      gemport = int(tokens[i + 1]); i += 2
    elif key == "user-vlan" and i + 1 < len(tokens):
      token = tokens[i + 1]
      user_vlan = int(token) if token.isdigit() else token
      i += 2
    elif key == "tag-transform" and i + 1 < len(tokens):
      tag_transform = tokens[i + 1]; i += 2
    else:
      i += 1

  if None in (vlan, port, ont_id, gemport):
    return "  Failure: Parameter service-port tidak lengkap."
  if user_vlan is None:
    user_vlan = vlan

  conn = get_connection()
  try:
    if not conn.execute(
        "SELECT 1 FROM olt_vlan WHERE username = ? AND vlan_id = ?",
        (username, vlan),
    ).fetchone():
      return f"  Failure: VLAN {vlan} belum dibuat. Jalankan 'vlan {vlan} smart' dulu."
    ont = conn.execute(
        "SELECT ont_id FROM olt_onts WHERE username = ? AND port = ? AND ont_id = ?",
        (username, port, ont_id),
    ).fetchone()
    if not ont:
      return f"  Failure: ONT {ont_id} pada {port} belum ada (ont add dulu)."
    if index is None:
      index = _next_sp_id(conn, username)
    conn.execute(
        "INSERT OR REPLACE INTO olt_service_ports"
        " (username, sp_id, vlan, port_type, port, ont_id, gemport, user_vlan,"
        " state, tag_transform, options_json)"
        " VALUES (?, ?, ?, 'gpon', ?, ?, ?, ?, 'up', ?, '{}')",
        (username, index, vlan, port, ont_id, gemport, user_vlan, tag_transform),
    )
    conn.commit()
  finally:
    conn.close()
  return (
      f"  Service virtual port {index} created successfully"
      f" (vlan {vlan}, {port} ont {ont_id} gem {gemport})."
  )


# --------------------------------------------------------------------------- #
# display ont info {all|<port>} [<ont-id>|all]
# --------------------------------------------------------------------------- #
def display_ont_info(username, cmd, gpon_port="0/1", node_name=None):
  from modules.commands.ont_status import refresh_ont_states

  if node_name:
    refresh_ont_states(username, node_name)
  tokens = cmd.split()
  args = tokens[3:]
  full_port = None
  ont_filter = None

  if not args:
    return "  Failure: Use display ont info {all|<port>} [<ont-id>|all]."
  if args[0].lower() == "all":
    full_port = None
  elif args[0].count("/") == 2:
    full_port = args[0]
  else:
    full_port = f"{gpon_port}/{args[0]}"
  if len(args) > 1 and args[1].lower() != "all":
    if not args[1].isdigit():
      return "  Failure: ONT ID harus angka atau 'all'."
    ont_filter = int(args[1])

  conn = get_connection()
  try:
    query = (
        "SELECT port, ont_id, sn, control_flag, run_state, config_state, match_state"
        " FROM olt_onts WHERE username = ? AND control_flag IS NOT NULL"
    )
    params = [username]
    if full_port:
      query += " AND port = ?"
      params.append(full_port)
    if ont_filter is not None:
      query += " AND ont_id = ?"
      params.append(ont_filter)
    query += " ORDER BY port, ont_id"
    rows = conn.execute(query, params).fetchall()
  finally:
    conn.close()

  lines = [
      "",
      "  Command:",
      f"          {cmd}",
      "  ---------------------------------------------------------------------",
      "  F/S/P       ONT ID   SN                Control flag  Run state  Config state  Match state",
      "  ---------------------------------------------------------------------",
  ]
  for port, ont_id, sn, ctrl, run_state, cfg_state, match_state in rows:
    lines.append(
        f"  {port:<11} {str(ont_id if ont_id is not None else '-'):<8}"
        f" {(sn or '-'):<17} {(ctrl or '-'):<13} {(run_state or '-'):<10}"
        f" {(cfg_state or '-'):<13} {match_state or '-'}"
    )
  lines.append("  ---------------------------------------------------------------------")
  online = sum(1 for row in rows if row[4] == "online")
  if full_port:
    lines.append(
        f"  In port {full_port}, the total of ONTs are: {len(rows)}, online: {online}"
    )
  else:
    lines.append(f"  Total ONTs: {len(rows)}, online: {online}")
  return "\n".join(lines)


# --------------------------------------------------------------------------- #
# display ont-srvprofile gpon (ringkas)
# --------------------------------------------------------------------------- #
def display_srvprofile(username, cmd):
  conn = get_connection()
  try:
    rows = conn.execute(
        "SELECT profile_id, profile_name, eth, pots, catv FROM olt_service_profiles"
        " WHERE username = ? ORDER BY profile_id",
        (username,),
    ).fetchall()
  finally:
    conn.close()
  lines = [
      "",
      "  Command:",
      f"          {cmd}",
      "  ------------------------------------------------------------------",
      "  Profile-ID  Profile-name        Eth  Pots  Catv",
      "  ------------------------------------------------------------------",
  ]
  for profile_id, name, eth, pots, catv in rows:
    lines.append(f"  {profile_id:<11} {name or '-':<19} {eth:<4} {pots:<5} {catv}")
  lines.append("  ------------------------------------------------------------------")
  return "\n".join(lines)
