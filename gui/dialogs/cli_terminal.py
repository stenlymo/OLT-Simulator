import hashlib
import html
import ipaddress
import io
import json
import re
import sqlite3
import shlex
from datetime import datetime
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QInputDialog, QTextEdit, QVBoxLayout, QWidget
from modules.database import get_connection, init_user_data, reset_user_data, save_user_configuration
from modules.commands.extended import dispatch as dispatch_extended
from modules.commands import bpdu, eth_ports, mirror, ont_ports, ont_status, pitp, vlan_info, vlan_service_profile
from modules import services
from modules.commands.dba import (
    DBACommandError,
    execute_dba_command,
    get_dba_profile,
    needs_multiburst_confirmation,
)


def build_ont_srvprofile_display(username, cmd):
  """Format the documented display ont-srvprofile gpon query results."""
  parts = cmd.split()
  lower_parts = [part.lower() for part in parts]
  conn = get_connection()
  cursor = conn.cursor()

  if "all" in lower_parts:
    cursor.execute(
        "SELECT profile_id, profile_name, eth, pots, catv "
        "FROM olt_service_profiles WHERE username = ? ORDER BY profile_id",
        (username,),
    )
    rows = cursor.fetchall()
    system_info = "system" in lower_parts
    lines = [
        "{ <cr>||<K> }:",
        "",
        "  Command:",
        f"          {cmd}",
        "  -----------------------------------------------------------------------------",
    ]
    if system_info:
      lines.append(
          "  Profile-ID  Profile-name                                System-info  Global profile-ID"
      )
    else:
      lines.append("  Profile-ID  Profile-name                                Binding times")
    lines.append("  -----------------------------------------------------------------------------")
    for profile_id, profile_name, _eth, _pots, _catv in rows:
      cursor.execute(
          "SELECT COUNT(*) FROM olt_onts WHERE username = ? AND service_profile_id = ?",
          (username, profile_id),
      )
      bind_count = cursor.fetchone()[0]
      if system_info:
        lines.append(
            f"  {profile_id:<11} {profile_name:<44} current      {profile_id}"
        )
      else:
        lines.append(
            f"  {profile_id:<11} {profile_name:<44} {bind_count}"
        )
    lines.extend([
        "  -----------------------------------------------------------------------------",
        f"  Total: {len(rows)}",
    ])
    conn.close()
    return "\n".join(lines)

  profile_id = None
  profile_name = None
  if "profile-id" in lower_parts:
    try:
      profile_id = int(parts[lower_parts.index("profile-id") + 1])
    except (ValueError, IndexError):
      conn.close()
      return "  Failure: profile-id must be a number from 0 to 8192."
  elif "profile-name" in lower_parts:
    try:
      profile_name = parts[lower_parts.index("profile-name") + 1].strip('"')
    except IndexError:
      conn.close()
      return "  Failure: profile-name is required."
  else:
    conn.close()
    return "  Failure: Use profile-id <ID>, profile-name <name>, or all [system]."

  if profile_id is not None:
    cursor.execute(
        "SELECT profile_id, profile_name, eth, pots, catv "
        "FROM olt_service_profiles WHERE username = ? AND profile_id = ?",
        (username, profile_id),
    )
  else:
    cursor.execute(
        "SELECT profile_id, profile_name, eth, pots, catv "
        "FROM olt_service_profiles WHERE username = ? AND profile_name = ? COLLATE NOCASE",
        (username, profile_name),
    )
  row = cursor.fetchone()
  if not row:
    conn.close()
    target = f"ID {profile_id}" if profile_id is not None else f"name {profile_name}"
    return f"  Failure: GPON ONT service profile with {target} was not found."

  profile_id, profile_name, eth, pots, catv = row
  cursor.execute(
      "SELECT port_id, vlan_mode, vlan_id, user_vlan "
      "FROM olt_service_profile_port_vlans "
      "WHERE username = ? AND profile_id = ? ORDER BY port_id",
      (username, profile_id),
  )
  vlan_rules = cursor.fetchall()
  cursor.execute(
      "SELECT forward_mode, translation_vlan FROM olt_service_profile_multicast "
      "WHERE username = ? AND profile_id = ?",
      (username, profile_id),
  )
  multicast = cursor.fetchone()
  cursor.execute(
      "SELECT COUNT(*) FROM olt_onts WHERE username = ? AND service_profile_id = ?",
      (username, profile_id),
  )
  bind_count = cursor.fetchone()[0]
  cursor.execute(
      "SELECT settings_json FROM olt_service_profile_settings "
      "WHERE username = ? AND profile_id = ?", (username, profile_id),
  )
  settings_row = cursor.fetchone()
  conn.close()

  settings = json.loads(settings_row[0]) if settings_row else {}
  ports = settings.get("ports", {})

  def port_capability(value, adaptive_max):
    if isinstance(value, str) and value.lower().startswith("adaptive"):
      maximum = value.partition(":")[2] or str(adaptive_max)
      return "adaptive", maximum
    if value is None:
      return "0", "-"
    return str(value), "-"

  def port_value(kind, legacy, default=0):
    value = ports.get(kind, legacy if legacy is not None else default)
    if isinstance(value, dict):
      if value.get("mode") == "adaptive":
        return "adaptive", str(value.get("max", "-"))
      return str(value.get("count", 0)), "-"
    return port_capability(value, {"eth": 28, "pots": 32, "vdsl": 8, "catv": 8}.get(kind, 8))

  eth_number, eth_max = port_value("eth", eth)
  pots_number, pots_max = port_value("pots", pots)
  vdsl_number, vdsl_max = port_value("vdsl", None)
  tdm_number, _ = port_value("tdm", None)
  moca_number, _ = port_value("moca", None)
  wifi_number, _ = port_value("wifi", None)
  catv_number, catv_max = port_value("catv", catv)
  qinq = settings.get("qinq", {})
  lines = [
      "{ <cr>||<K> }:",
      "",
      "  Command:",
      f"          {cmd}",
      "  -----------------------------------------------------------------------------",
      f"  Profile-ID  : {profile_id}",
      f"  Profile-name: {profile_name}",
      "  Access-type : GPON",
      "  -----------------------------------------------------------------------------",
      "  Port-type     Port-number     Max-adaptive-number",
      "  -----------------------------------------------------------------------------",
      f"  POTS          {pots_number:<15} {pots_max}",
      f"  ETH           {eth_number:<15} {eth_max}",
      f"  VDSL          {vdsl_number:<15} {vdsl_max}",
      f"  TDM           {tdm_number:<15} -",
      f"  MOCA          {moca_number:<15} -",
      f"  WiFi          {wifi_number:<15} -",
      f"  CATV          {catv_number:<15} {catv_max}",
      "  -----------------------------------------------------------------------------",
      f"  TDM port type                     : {settings.get('tdm_type', 'E1')}",
      f"  TDM service type                  : {settings.get('tdm_srvtype', 'TDMoGEM')}",
      f"  MAC learning function switch      : {settings.get('mac_learning', 'enable').title()}",
      f"  ONT transparent function switch   : {settings.get('transparent', 'disable').title()}",
      "  Ring check switch                 : Disable",
      "  Ring port auto-shutdown           : Enable",
      "  Ring detect frequency             : 8 (pps)",
      "  Ring resume interval              : 300 (s)",
      "  Ring detect period                : 0 (s)",
      f"  Multicast forward mode            : {multicast[0] if multicast else 'Unconcern'}",
      f"  Multicast forward VLAN            : {multicast[1] if multicast and multicast[1] is not None else '-'}",
      "  Multicast mode                    : Unconcern",
      "  Upstream IGMP packet forward mode : Unconcern",
      "  Upstream IGMP packet forward VLAN : -",
      "  Upstream IGMP packet priority     : -",
      f"  Native VLAN option                : {settings.get('native_vlan', 'concern').title()}",
      "  Upstream PQ color policy          : -",
      "  Downstream PQ color policy        : -",
      "  Monitor link                      : Enable",
      "  MTU(byte)                         : Unconcern",
      "  L2-isolate                        : Unconcern",
      "  Storm control                     : Enable",
      "  BPDU protection                   : Enable",
      "  Access control policy             : Centralized",
      "  -----------------------------------------------------------------------------",
      "  TPID-Index        TPID-Value",
      "  -----------------------------------------------------------------------------",
      "  0                 0x8100",
      "  1                 0x8818",
      "  -----------------------------------------------------------------------------",
      "  Port-type Port-ID QinQmode  PriorityPolicy Inbound     Outbound",
      "  -----------------------------------------------------------------------------",
  ]
  eth_count = int(eth_number) if str(eth_number).isdigit() else int(eth_max) if str(eth_max).isdigit() else 8
  for port_id in range(1, min(eth_count, 8) + 1):
    lines.append(
        f"  ETH       {port_id:<7} {qinq.get(f'eth:{port_id}', 'unconcern'):<10} unconcern      unconcern   unconcern"
    )
  lines.extend([
      "  -----------------------------------------------------------------------------",
      "  Port-type Port-ID  DownstreamMode  MismatchPolicy",
      "  -----------------------------------------------------------------------------",
  ])
  for port_id in range(1, min(eth_count, 8) + 1):
    lines.append(f"  ETH       {port_id:<15} operation       discard")
  lines.extend([
      "  -----------------------------------------------------------------------------",
      "  Port   Port   Service-type Index S-VLAN S-PRI C-VLAN C-PRI ENCAP      S-PRI POLICY",
      "  type   ID",
      "  -----------------------------------------------------------------------------",
  ])
  for port_id, vlan_mode, vlan_id, user_vlan in vlan_rules:
    lines.append(
        f"  ETH    {port_id:<6} {('Translation' if vlan_mode == 'translation' else 'Translation'):<13} "
        f"1     {vlan_id:<6} -     {user_vlan if user_vlan is not None else '-':<6} -     -          -"
    )
  lines.extend([
      "  -----------------------------------------------------------------------------",
      f"  Binding times : {bind_count}",
      "  -----------------------------------------------------------------------------",
  ])
  return "\n".join(lines)


def build_current_srvprofile_display(profile_id, profile_name, profile_data, settings):
  ports = settings.get("ports", {})
  lines = [
      "\n  Command:\n          display ont-srvprofile current",
      f"  Profile-ID  : {profile_id}", f"  Profile-name: {profile_name}",
      "  Port capability:",
  ]
  for kind in ("pots", "eth", "vdsl", "tdm", "moca", "catv", "wifi"):
    legacy = profile_data.get(kind, 0)
    value = ports.get(kind, legacy)
    if isinstance(value, dict):
      shown = f"adaptive {value.get('max')}" if value.get("mode") == "adaptive" else str(value.get("count", 0))
    else:
      shown = str(value)
    lines.append(f"  {kind.upper():<8}: {shown}")
  lines.extend([
      f"  TDM type       : {settings.get('tdm_type', 'E1')}",
      f"  TDM service    : {settings.get('tdm_srvtype', 'TDMoGEM')}",
      f"  MAC learning   : {settings.get('mac_learning', 'enable')}",
      f"  Transparent    : {settings.get('transparent', 'disable')}",
      f"  Native VLAN    : {settings.get('native_vlan', 'concern')}",
  ])
  for port, mode in sorted(settings.get("qinq", {}).items()):
    lines.append(f"  QinQ {port:<10}: {mode}")
  return "\n".join(lines)


def build_tr069_profile_display(username, cmd):
  try:
    parts = shlex.split(cmd)
  except ValueError:
    return "  Failure: Invalid quoted profile name."
  lowered = [part.lower() for part in parts]
  conn = get_connection()
  cursor = conn.cursor()
  if "bound-info" in lowered:
    key = "profile-id" if "profile-id" in lowered else "profile-name" if "profile-name" in lowered else None
    if key is None:
      conn.close()
      return "  Failure: Specify profile-id or profile-name."
    value = parts[lowered.index(key) + 1] if lowered.index(key) + 1 < len(parts) else ""
    where = "profile_id = ?" if key == "profile-id" else "profile_name = ? COLLATE NOCASE"
    cursor.execute(f"SELECT profile_id FROM olt_tr069_server_profiles WHERE username = ? AND {where}", (username, int(value) if key == "profile-id" and value.isdigit() else value))
    found = cursor.fetchone()
    if not found:
      conn.close()
      return "  Failure: TR069 server profile was not found."
    cursor.execute("SELECT port, ont_id FROM olt_tr069_bindings WHERE username = ? AND profile_id = ? ORDER BY port, ont_id", (username, found[0]))
    bindings = cursor.fetchall()
    conn.close()
    grouped = {}
    for port, ont_id in bindings:
      grouped.setdefault(port, []).append(ont_id)
    lines = [f"\n  Command:\n          {cmd}", "  ---------------------------------------------------------------------------", "  F/S/P       ONT List", "  ---------------------------------------------------------------------------"]
    lines.extend(f"  {port:<11} {','.join(map(str, ids))}" for port, ids in grouped.items())
    lines.append("  ---------------------------------------------------------------------------")
    return "\n".join(lines)

  if "all" in lowered:
    cursor.execute("SELECT profile_id, profile_name FROM olt_tr069_server_profiles WHERE username = ? ORDER BY profile_id", (username,))
    rows = cursor.fetchall()
    lines = [f"\n  Command:\n          {cmd}", " --------------------------------------------------------------", " Profile ID     Profile Name                     Binding Times", " --------------------------------------------------------------"]
    for profile_id, name in rows:
      cursor.execute("SELECT COUNT(*) FROM olt_tr069_bindings WHERE username = ? AND profile_id = ?", (username, profile_id))
      lines.append(f" {profile_id:<14} {name:<32} {cursor.fetchone()[0]}")
    lines.extend([" --------------------------------------------------------------", f" Total: {len(rows)}"])
    conn.close()
    return "\n".join(lines)

  key = "profile-id" if "profile-id" in lowered else "profile-name" if "profile-name" in lowered else None
  if key is None:
    conn.close()
    return "  Failure: Use all, profile-id <ID>, or profile-name <name>."
  value = parts[lowered.index(key) + 1] if lowered.index(key) + 1 < len(parts) else ""
  if key == "profile-id" and value.isdigit():
    where, target = "profile_id = ?", int(value)
  else:
    where, target = "profile_name = ? COLLATE NOCASE", value
  cursor.execute(f"SELECT profile_id, profile_name, url, user_name, auth_realm FROM olt_tr069_server_profiles WHERE username = ? AND {where}", (username, target))
  row = cursor.fetchone()
  if not row:
    conn.close()
    return "  Failure: TR069 server profile was not found."
  cursor.execute("SELECT COUNT(*) FROM olt_tr069_bindings WHERE username = ? AND profile_id = ?", (username, row[0]))
  bindings = cursor.fetchone()[0]
  conn.close()
  return "\n".join([
      f"\n  Command:\n          {cmd}", " -----------------------------------------------------------------------",
      f" Profile-id     :   {row[0]}", f" Profile-name   :   {row[1]}",
      f" URL            :   {row[2]}", f" User Name      :   {row[3] or '-'}",
      f" Realm          :   {row[4] or '-'}", " -----------------------------------------------------------------------",
      f" Binding times  :   {bindings}", " -----------------------------------------------------------------------",
  ])


def parse_vlan_ids(value, minimum=1, maximum=4093):
  ids = set()
  for item in value.replace(" ", "").split(","):
    if not item:
      raise ValueError("VLAN list contains an empty item")
    if "-" in item:
      bounds = item.split("-")
      if len(bounds) != 2:
        raise ValueError("Invalid VLAN range")
      start, end = map(int, bounds)
      if start > end:
        raise ValueError("VLAN range start must not exceed end")
      current = range(start, end + 1)
    else:
      current = (int(item),)
    if any(vlan < minimum or vlan > maximum for vlan in current):
      raise ValueError(f"VLAN IDs must be in the range {minimum}-{maximum}")
    ids.update(current)
  if not ids:
    raise ValueError("VLAN list cannot be empty")
  return sorted(ids)


def build_vlan_display(username, cmd):
  conn = get_connection()
  cursor = conn.cursor()
  parts = cmd.split()
  if len(parts) >= 3 and parts[2].lower() == "number":
    cursor.execute("SELECT vlan_type, vlan_attr, COUNT(*) FROM olt_vlan WHERE username = ? GROUP BY vlan_type, vlan_attr", (username,))
    counts = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) FROM olt_vlan WHERE username = ?", (username,))
    total = cursor.fetchone()[0]
    types = {name: 0 for name in ("mux", "smart", "standard", "super")}
    attrs = {name: 0 for name in ("common", "q-in-q", "stacking")}
    for vlan_type, attr, count in counts:
      types[vlan_type.lower()] = types.get(vlan_type.lower(), 0) + count
      attrs[attr.lower()] = attrs.get(attr.lower(), 0) + count
    conn.close()
    return "\n".join([
        f"\n  Command:\n          {cmd}", f"  The total of VLAN(s) in system : {total}",
        "  Count by the type of VLAN(s)   :",
        f"  The total of MUX VLAN(s)       : {types['mux']}",
        f"  The total of smart VLAN(s)     : {types['smart']}",
        f"  The total of standard VLAN(s)  : {types['standard']}",
        f"  The total of super VLAN(s)     : {types['super']}",
        "  Count by the attribute of VLAN(s):",
        f"  The total of common VLAN(s)    : {attrs['common']}",
        f"  The total of QinQ VLAN(s)      : {attrs['q-in-q']}",
        f"  The total of stacking VLAN(s)  : {attrs['stacking']}",
    ])
  requested_id = None
  if len(parts) > 2 and parts[2].isdigit():
    requested_id = int(parts[2])
  if requested_id is None:
    cursor.execute("SELECT vlan_id, vlan_type, vlan_attr, description, bound_port, dedicated_net_id, vlan_name FROM olt_vlan WHERE username = ? ORDER BY vlan_id", (username,))
    rows = cursor.fetchall()
    conn.close()
    lines = [f"\n  Command:\n          {cmd}", "  VLAN  Type      Attribute  Description                     Dedicated-net  Ports", "  -------------------------------------------------------------------------------"]
    lines.extend(f"  {vid:<5} {vtype:<9} {attr:<10} {(desc or name or '-'):<30} {dnet:<14} {ports or '-'}" for vid, vtype, attr, desc, ports, dnet, name in rows)
    lines.extend(["  -------------------------------------------------------------------------------", f"  Total: {len(rows)}"])
    return "\n".join(lines)
  cursor.execute("SELECT vlan_type, vlan_attr, description, bound_port, bound_state, bound_tag, ip_address, dedicated_net_id, vlan_name FROM olt_vlan WHERE username = ? AND vlan_id = ?", (username, requested_id))
  row = cursor.fetchone()
  conn.close()
  if not row:
    return f"  Failure: VLAN {requested_id} was not found."
  fields = ("VLAN type", "VLAN attribute", "VLAN description", "Standard port number", "Port state", "Port tag", "IP address", "Dedicated-net ID", "VLAN name")
  lines = [f"\n  Command:\n          {cmd}", f"  VLAN ID : {requested_id}"]
  lines.extend(f"  {label:<28}: {value if value not in (None, '') else '-'}" for label, value in zip(fields, row))
  return "\n".join(lines)


def build_ont_lineprofile_display(username, cmd):
  """Format committed ONT line profiles using the Huawei display layout."""
  parts = cmd.split()
  lower_parts = [part.lower() for part in parts]
  conn = get_connection()
  cursor = conn.cursor()

  if "all" in lower_parts:
    cursor.execute(
        "SELECT profile_id, profile_name FROM olt_line_profiles "
        "WHERE username = ? ORDER BY profile_id",
        (username,),
    )
    profiles = cursor.fetchall()
    system_info = "system" in lower_parts
    lines = [
        "{ <cr>||<K> }:", "", "  Command:", f"          {cmd}",
        "  -----------------------------------------------------------------------------",
        ("  Profile-ID  Profile-name                       System-info  Global profile-ID"
         if system_info else
         "  Profile-ID  Profile-name                                Binding times"),
        "  -----------------------------------------------------------------------------",
    ]
    for profile_id, profile_name in profiles:
      cursor.execute(
          "SELECT COUNT(*) FROM olt_onts WHERE username = ? AND line_profile_id = ?",
          (username, profile_id),
      )
      ont_bindings = cursor.fetchone()[0]
      cursor.execute(
          "SELECT bind_count FROM olt_line_profiles WHERE username = ? AND profile_id = ?",
          (username, profile_id),
      )
      profile_row = cursor.fetchone()
      bindings = max(ont_bindings, profile_row[0] if profile_row else 0)
      if system_info:
        system_name = f"{profile_name}[Default]" if profile_id == 0 else profile_name
        lines.append(
            f"  {profile_id:<11} {system_name:<35} {'default' if profile_id == 0 else 'current':<12} {profile_id}"
        )
      else:
        lines.append(
            f"  {profile_id:<11} {profile_name:<44} {bindings}"
        )
    lines.extend([
        "  -----------------------------------------------------------------------------",
        f"  Total: {len(profiles)}",
    ])
    conn.close()
    return "\n".join(lines)

  profile_id = None
  profile_name = None
  if "profile-id" in lower_parts:
    try:
      profile_id = int(parts[lower_parts.index("profile-id") + 1])
    except (ValueError, IndexError):
      conn.close()
      return "  Failure: profile-id must be a number from 0 to 8195."
  elif "profile-name" in lower_parts:
    try:
      profile_name = parts[lower_parts.index("profile-name") + 1].strip('"')
    except IndexError:
      conn.close()
      return "  Failure: profile-name is required."
  else:
    conn.close()
    return "  Failure: Use profile-id <ID>, profile-name <name>, or all [system]."

  if profile_id is not None:
    cursor.execute(
        "SELECT profile_id, profile_name FROM olt_line_profiles "
        "WHERE username = ? AND profile_id = ?",
        (username, profile_id),
    )
  else:
    cursor.execute(
        "SELECT profile_id, profile_name FROM olt_line_profiles "
        "WHERE username = ? AND profile_name = ? COLLATE NOCASE",
        (username, profile_name),
    )
  profile = cursor.fetchone()
  if not profile:
    conn.close()
    target = f"ID {profile_id}" if profile_id is not None else f"name {profile_name}"
    return f"  Failure: GPON ONT line profile with {target} was not found."
  profile_id, profile_name = profile

  cursor.execute(
      "SELECT omcc_encrypt, tr069_enabled, tr069_ip_index, fec_upstream, "
      "qos_mode, mapping_mode, gem_encrypt_mode "
      "FROM olt_line_profile_settings WHERE username = ? AND profile_id = ?",
      (username, profile_id),
  )
  settings = cursor.fetchone() or ("off", 0, 0, "disable", "priority-queue", "vlan", "aes-128")
  cursor.execute(
      "SELECT tcont_id, dba_profile_id FROM olt_line_profile_tconts "
      "WHERE username = ? AND profile_id = ? ORDER BY tcont_id",
      (username, profile_id),
  )
  tconts = cursor.fetchall()
  cursor.execute(
      "SELECT gem_id, service_type, tcont_id, encrypt, cascade, priority_queue, "
      "downstream_priority_queue, gem_car_index "
      "FROM olt_line_profile_gems WHERE username = ? AND profile_id = ? "
      "ORDER BY gem_id",
      (username, profile_id),
  )
  gems = cursor.fetchall()
  cursor.execute(
      "SELECT gem_id, mapping_id, match_type, match_value "
      "FROM olt_line_profile_mappings WHERE username = ? AND profile_id = ? "
      "ORDER BY gem_id, mapping_id",
      (username, profile_id),
  )
  mappings = cursor.fetchall()
  cursor.execute(
      "SELECT COUNT(*) FROM olt_onts WHERE username = ? AND line_profile_id = ?",
      (username, profile_id),
  )
  ont_bindings = cursor.fetchone()[0]
  cursor.execute(
      "SELECT bind_count FROM olt_line_profiles WHERE username = ? AND profile_id = ?",
      (username, profile_id),
  )
  profile_row = cursor.fetchone()
  bindings = max(ont_bindings, profile_row[0] if profile_row else 0)
  conn.close()

  omcc_value = "On" if str(settings[0]).lower() == "on" else "Off"
  tr069_value = "Enable" if settings[1] else "Disable"
  qos_label = {"priority-queue": "PQ", "gem-car": "GEM-CAR", "flow-car": "FLOW-CAR"}.get(settings[4], settings[4])
  mapping_label = {
      "vlan": "VLAN", "priority": "PRI", "vlan-priority": "VLAN+PRI",
      "port": "PORT", "port-vlan": "PORT+VLAN",
      "port-priority": "PORT+PRI", "port-vlan-priority": "PORT+VLAN+PRI",
      "iptos": "IPTOS", "vlan-iptos": "VLAN+IPTOS",
      "vlan-bundle-priority": "VLAN BUNDLE+PRI",
      "port-vlan-bundle-priority": "PORT+VLAN BUNDLE+PRI",
  }.get(settings[5], settings[5])
  lines = [
      "{ <cr>||<K> }:", "", "  Command:", f"          {cmd}",
      "  ------------------------------------------------------------------------------",
      f"  Profile-ID          :{profile_id}",
      f"  Profile-name        :{profile_name}",
      "  Access-type         :GPON",
      "  ------------------------------------------------------------------------------",
      f"  FEC upstream switch :{str(settings[3]).title()}",
      f"  OMCC encrypt switch :{omcc_value}",
      f"  Qos mode            :{qos_label}",
      f"  Mapping mode        :{mapping_label}",
      f"  TR069 management    :{tr069_value}",
      f"  TR069 IP index      :{settings[2]}",
      f"  GEM encrypt mode    :{settings[6].upper()}",
      "  ------------------------------------------------------------------------------",
  ]
  for tcont_id, dba_profile_id in tconts:
    lines.append(
        f"  <T-CONT {tcont_id:>3}>          DBA Profile-ID:{dba_profile_id}"
    )

  mappings_by_gem = {}
  for gem_id, mapping_id, match_type, match_value in mappings:
    mappings_by_gem.setdefault(gem_id, []).append(
        (mapping_id, match_type, match_value)
    )
  for gem_id, service_type, tcont_id, encrypt, cascade, upstream_queue, downstream_queue, gem_car in gems:
    priority_queue = next(
        (value for _idx, kind, value in mappings_by_gem.get(gem_id, [])
         if kind == "priority"),
        upstream_queue,
    )
    lines.extend([
        f"   <Gem Index {gem_id}>",
        "   --------------------------------------------------------------------",
        f"   |Serv-Type:{service_type.upper():<3} |Encrypt:{encrypt:<3} |Cascade:{cascade:<3} |GEM-CAR:{gem_car if gem_car is not None else '-':<14} |",
        f"   |Upstream-priority-queue:{priority_queue:<2} |Downstream-priority-queue:{downstream_queue:<8} |",
        "   --------------------------------------------------------------------",
        "    Mapping VLAN  Priority Port    Port  Bundle  Flow  Transparent",
        "    index                  type    ID    ID      CAR",
        "   --------------------------------------------------------------------",
    ])
    gem_mappings = mappings_by_gem.get(gem_id, []) or [(0, None, None)]
    for mapping_id, match_type, match_value in gem_mappings:
      vlan = match_value if match_type == "vlan" else "-"
      priority = match_value if match_type == "priority" else "-"
      lines.append(
          f"    {mapping_id:<7} {str(vlan):<5} {str(priority):<8} -       -     -       -     -"
      )
    lines.append("   --------------------------------------------------------------------")

  lines.extend([
      "  ------------------------------------------------------------------------------",
      "  Notes: Run the display traffic table ip command to query",
      "         traffic table configuration",
      "  ------------------------------------------------------------------------------",
      f"  Binding times       :{bindings}",
      "  ------------------------------------------------------------------------------",
  ])
  return "\n".join(lines)


def parse_id_list(value, minimum, maximum, label):
  """Parse Huawei-style comma/range lists such as 1,3-5."""
  result = set()
  try:
    for token in value.split(","):
      bounds = token.split("-")
      if len(bounds) == 1:
        start = end = int(bounds[0])
      elif len(bounds) == 2:
        start, end = map(int, bounds)
      else:
        raise ValueError
      if start > end or start < minimum or end > maximum:
        raise ValueError
      result.update(range(start, end + 1))
  except ValueError as exc:
    raise ValueError(f"{label} must be in range {minimum}-{maximum}.") from exc
  if not result:
    raise ValueError(f"{label} is required.")
  return sorted(result)


class AuthenticCLITerminal(QTextEdit):
  """Kustom QTextEdit untuk memberikan pengalaman Terminal Telnet/SSH yang asli."""

  def __init__(self, parent_dialog):
    super().__init__()
    self.dlg = parent_dialog
    self.setStyleSheet(
        "background-color: #000000; color: #00ff00; font-family: 'Consolas';"
        " font-size: 12pt; border: none;"
    )
    self.command_history = []
    self.history_index = 0

  def wheelEvent(self, event):
    if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
      if event.angleDelta().y() > 0:
        self.zoomIn(1)
      else:
        self.zoomOut(1)
      event.accept()
    else:
      super().wheelEvent(event)

  def keyPressEvent(self, event):
    cursor = self.textCursor()
    if (
        event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Left)
        and cursor.position() <= self.dlg.prompt_position
    ):
      return

    if event.key() == Qt.Key.Key_Up:
      if self.command_history and self.history_index > 0:
        self.history_index -= 1
        self.replace_current_line_input(
            self.command_history[self.history_index]
        )
      return
    elif event.key() == Qt.Key.Key_Down:
      if self.command_history and self.history_index < len(
          self.command_history
      ) - 1:
        self.history_index += 1
        self.replace_current_line_input(
            self.command_history[self.history_index]
        )
      elif self.history_index == len(self.command_history) - 1:
        self.history_index += 1
        self.replace_current_line_input("")
      return
    elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
      full_text = self.toPlainText()
      input_cmd = full_text[self.dlg.prompt_position :].strip()
      if input_cmd:
        self.command_history.append(input_cmd)
        self.history_index = len(self.command_history)
      self.dlg.append_plain_text("")
      # Jaring pengaman: kesalahan internal tidak boleh mematikan terminal.
      try:
        self.dlg.handle_user_input(input_cmd)
      except Exception as exc:  # noqa: BLE001
        self.dlg.append_plain_text(
            f"\n  Failure: Internal error ({type(exc).__name__}: {exc})."
            " Perintah dibatalkan."
        )
        self.dlg.print_prompt()
      return

    super().keyPressEvent(event)

  def replace_current_line_input(self, text):
    cursor = self.textCursor()
    cursor.setPosition(self.dlg.prompt_position)
    cursor.movePosition(
        QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor
    )
    cursor.removeSelectedText()
    cursor.insertText(text)


class CLITerminalDialog(QWidget):
  """Jendela Terminal CLI Otentik Huawei SmartAX MA5801 / MA5608T OLT."""

  def __init__(self, node_name, username="admin", parent=None):
    super().__init__(parent)
    self.node_name = node_name if node_name else "HW.MA5801-0LT-01"
    self.username = username
    self.sysname = self.node_name
    self.cli_state = "USER"
    self.current_gpon_port = "0/1"
    self.current_eth_board = "0/3"
    self.current_vlanif_id = None
    self.current_mcast_vlan_id = None
    self.current_profile_id = 10
    self.current_vlan_service_profile_id = None
    self.current_line_profile_name = None
    self.current_service_profile_name = None
    self.autofind_enabled_ports = set()
    self.pending_confirmation = None
    self.pending_command_buffer = ""

    # Buffer sementara saat konfigurasi Line Profile
    self.pending_lineprofile_data = {
        "tcont_id": 1,
        "dba_id": 6,
        "gem_id": 1,
        "vlan_id": 10,
    }
    self.pending_lineprofile_settings = {
        "omcc_encrypt": "on", "tr069_enabled": 0, "tr069_ip_index": 0,
        "fec_upstream": "disable", "qos_mode": "priority-queue",
        "mapping_mode": "vlan", "gem_encrypt_mode": "aes-128",
    }
    self.pending_lineprofile_tconts = []
    self.pending_lineprofile_gems = []
    self.pending_lineprofile_mappings = []
    self.pending_lineprofile_vlan_bundles = []
    self.pending_service_profile_data = {"eth": 0, "pots": 0, "catv": 0}
    self.pending_service_vlan_rules = []
    self.pending_multicast_forward = None
    self.pending_service_settings = {
        "ports": {}, "tdm_type": "E1", "tdm_srvtype": "TDMoGEM",
        "mac_learning": "enable", "transparent": "disable",
        "native_vlan": "concern", "qinq": {},
    }

    init_user_data(self.username)
    conn = get_connection()
    row = conn.execute(
        "SELECT sysname FROM olt_node_settings WHERE username=? AND node_name=?",
        (self.username, self.node_name),
    ).fetchone()
    conn.close()
    if row and row[0]:
      self.sysname = row[0]
    self.init_ui()
    self.print_welcome_banner()

  def init_ui(self):
    layout = QVBoxLayout(self)
    layout.setContentsMargins(0, 0, 0, 0)
    self.terminal = AuthenticCLITerminal(self)
    layout.addWidget(self.terminal)

  def append_plain_text(self, text):
    """Mencetak teks mentah secara aman tanpa interpretasi tag HTML."""
    cursor = self.terminal.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(text + "\n")
    self.terminal.setTextCursor(cursor)

  def print_welcome_banner(self):
    banner = (
        "\n  Please check whether system data has been changed, and save data in"
        " time\n\n  Configuration console time out, please retry to log"
        " on\n\n\n  Huawei Integrated Access Software (MA5801).\n "
        " Copyright(C) Huawei Technologies Co., Ltd. 2002-2022. All rights"
        " reserved.\n\n"
    )
    self.append_plain_text(banner)
    self.print_prompt()

  def get_current_prompt_text(self):
    if self.cli_state == "USER":
      return f"{self.sysname}>"
    elif self.cli_state == "ENABLE":
      return f"{self.sysname}#"
    elif self.cli_state == "CONFIG":
      return f"{self.sysname}(config)#"
    elif self.cli_state == "INTERFACE_GPON":
      return f"{self.sysname}(config-if-gpon-{self.current_gpon_port})#"
    elif self.cli_state == "INTERFACE_EPON":
      return f"{self.sysname}(config-if-epon-{self.current_gpon_port})#"
    elif self.cli_state == "INTERFACE_ETH":
      return f"{self.sysname}(config-if-eth-{self.current_eth_board})#"
    elif self.cli_state == "VLANIF":
      return f"{self.sysname}(config-if-vlanif{self.current_vlanif_id})#"
    elif self.cli_state == "MVLAN":
      return f"{self.sysname}(config-mvlan{self.current_mcast_vlan_id})#"
    elif self.cli_state == "BTV":
      return f"{self.sysname}(config-btv)#"
    elif self.cli_state == "LINEPROFILE":
      return (
          f"{self.sysname}(config-gpon-lineprofile-{self.current_profile_id})#"
      )
    elif self.cli_state == "SRVPROFILE":
      return (
          f"{self.sysname}(config-gpon-srvprofile-{self.current_profile_id})#"
      )
    elif self.cli_state == "VLAN_SRVPROFILE":
      return f"{self.sysname}(config-vlan-srvprof-{self.current_vlan_service_profile_id})#"
    return f"{self.sysname}>"

  def print_prompt(self):
    prompt_str = self.get_current_prompt_text()
    cursor = self.terminal.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(prompt_str)
    self.terminal.setTextCursor(cursor)
    self.prompt_position = len(self.terminal.toPlainText())

  def _run_dba_modify_wizard(self, original_command, mode):
    """Collect the documented DBA modify values and apply them to the profile."""
    try:
      profile = get_dba_profile(self.username, original_command)
    except DBACommandError as exc:
      self.append_plain_text(f"\n  Failure: {exc}")
      return
    ptype, ok = QInputDialog.getItem(
        self, "Modify DBA Profile", "DBA profile type (1-5):",
        [f"type{i}" for i in range(1, 6)],
        max(0, int(str(profile["type"]).replace("type", "")) - 1), False,
    )
    if not ok:
      self.append_plain_text("\n  Operation cancelled.")
      return
    type_number = int(ptype[-1])
    tokens = ["dba-profile", "modify", "profile-id", str(profile["profile_id"]), ptype]

    def ask_bandwidth(label, key):
      value, accepted = QInputDialog.getInt(
          self, "Modify DBA Profile", f"{label} (128-50000000 kbit/s):",
          int(profile.get(key) or 128), 128, 50_000_000, 64,
      )
      return value, accepted

    fields = []
    if type_number in (1, 5):
      fields.append(("Fixed bandwidth", "fix", "fix_bw"))
    if type_number in (2, 3, 5):
      fields.append(("Assured bandwidth", "assure", "assured"))
    if type_number in (3, 4, 5):
      fields.append(("Maximum bandwidth", "max", "max_bw"))
    for label, keyword, key in fields:
      value, accepted = ask_bandwidth(label, key)
      if not accepted:
        self.append_plain_text("\n  Operation cancelled.")
        return
      tokens.extend((keyword, str(value)))
    if type_number == 1:
      for key, keyword, label in (("bandwidth_compensate", "bandwidth_compensate", "Bandwidth compensation"), ("fix_delay", "fix-delay", "Fixed delay")):
        value, accepted = QInputDialog.getItem(self, "Modify DBA Profile", f"{label}:", ["no", "yes"], 1 if profile.get(key) == "yes" else 0, False)
        if not accepted:
          self.append_plain_text("\n  Operation cancelled.")
          return
        tokens.extend((keyword, value))
    if type_number == 5:
      value, accepted = QInputDialog.getItem(self, "Modify DBA Profile", "Additional bandwidth:", ["non-assure", "best-effort"], 1 if profile.get("additional_bandwidth") != "non-assure" else 0, False)
      if not accepted:
        self.append_plain_text("\n  Operation cancelled.")
        return
      tokens.extend(("additional-bandwidth", value))
    if type_number == 4 or (type_number == 5 and profile.get("additional_bandwidth") != "non-assure"):
      priority, accepted = QInputDialog.getInt(self, "Modify DBA Profile", "Best-effort priority (0-7):", int(profile.get("priority") or 0), 0, 7)
      if not accepted:
        self.append_plain_text("\n  Operation cancelled.")
        return
      weight, accepted = QInputDialog.getInt(self, "Modify DBA Profile", "Best-effort weight (1-10000):", int(profile.get("weight") or 10), 1, 10000)
      if not accepted:
        self.append_plain_text("\n  Operation cancelled.")
        return
      tokens.extend(("priority", str(priority), "weight", str(weight)))
    frequencies = ["calculate-period", "single-frame", "1/2", "1/4", "1/8", "1/16", "1/32"]
    current_frequency = profile.get("assignment_frequency") or "single-frame"
    frequency, accepted = QInputDialog.getItem(self, "Modify DBA Profile", "Assignment frequency:", frequencies, max(0, frequencies.index(current_frequency)) if current_frequency in frequencies else 1, False)
    if not accepted:
      self.append_plain_text("\n  Operation cancelled.")
      return
    tokens.extend(("assignment-frequency", frequency))
    try:
      if needs_multiburst_confirmation(" ".join(tokens)):
        answer, accepted = QInputDialog.getItem(
            self, "Confirm DBA Profile",
            "Single-frame multiburst may prevent lower-capability ONTs from going online. Continue?",
            ["no", "yes"], 0, False,
        )
        if not accepted or answer != "yes":
          self.append_plain_text("\n  Operation cancelled.")
          return
      self.append_plain_text(execute_dba_command(self.username, " ".join(tokens), mode))
    except DBACommandError as exc:
      self.append_plain_text(f"\n  Failure: {exc}")

  def _format_current_lineprofile(self, command):
    settings = self.pending_lineprofile_settings
    lines = [
        "\n  Command:", f"          {command}",
        "  -----------------------------------------------------------------------------",
        f"  Profile-ID          :{self.current_profile_id}",
        f"  Profile-name        :{self.current_line_profile_name}",
        "  Access-type         :GPON",
        "  -----------------------------------------------------------------------------",
        f"  FEC upstream switch :{settings['fec_upstream'].title()}",
        f"  OMCC encrypt switch :{settings['omcc_encrypt'].title()}",
        f"  Qos mode            :{settings['qos_mode']}",
        f"  Mapping mode        :{settings['mapping_mode']}",
        f"  TR069 management    :{'Enable' if settings['tr069_enabled'] else 'Disable'}",
        f"  TR069 IP index      :{settings['tr069_ip_index']}",
        f"  GEM encrypt mode    :{settings['gem_encrypt_mode'].upper()}",
        "  -----------------------------------------------------------------------------",
    ]
    for tcont in sorted(self.pending_lineprofile_tconts, key=lambda item: item['tcont_id']):
      lines.append(
          f"  <T-CONT {tcont['tcont_id']:>3}>          DBA Profile-ID:{tcont['dba_profile_id']}"
      )
    maps = {}
    for mapping in self.pending_lineprofile_mappings:
      maps.setdefault(mapping['gem_id'], []).append(mapping)
    for gem in sorted(self.pending_lineprofile_gems, key=lambda item: item['gem_id']):
      lines.extend([
          f"  <Gem Index {gem['gem_id']}>",
          f"  Serv-Type:{gem['service_type'].upper()} Encrypt:{gem['encrypt']} Cascade:{gem['cascade']} T-CONT:{gem['tcont_id']}",
          "  Mapping-index  Type       Value",
      ])
      for mapping in maps.get(gem['gem_id'], []):
        lines.append(
            f"  {mapping['mapping_id']:<14} {mapping['match_type']:<10} {mapping['match_value']}"
        )
    lines.append("  -----------------------------------------------------------------------------")
    lines.append("  Note: Current configuration is not active until commit.")
    return "\n".join(lines)

  def _apply_vlan_operation(self, operation, vlan_ids, options):
    conn = get_connection()
    cursor = conn.cursor()
    succeeded = 0
    if operation == "create":
      for vlan_id in vlan_ids:
        cursor.execute(
            "INSERT OR IGNORE INTO olt_vlan (username,vlan_id,vlan_type,vlan_attr,dedicated_net_id) VALUES (?,?,?,'common',?)",
            (self.username, vlan_id, options["type"], options["dnet"]),
        )
        succeeded += cursor.rowcount
    elif operation == "delete":
      for vlan_id in vlan_ids:
        if vlan_id == 1:
          conn.close(); raise ValueError("VLAN 1 is the system default and cannot be deleted.")
        cursor.execute("SELECT bound_port, ip_address FROM olt_vlan WHERE username = ? AND vlan_id = ?", (self.username, vlan_id))
        row = cursor.fetchone()
        if not row: continue
        cursor.execute("SELECT COUNT(*) FROM olt_service_ports WHERE username = ? AND vlan = ?", (self.username, vlan_id))
        service_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM olt_node_vlanifs WHERE username = ? AND vlan_id = ?", (self.username, vlan_id))
        vlanif_count = cursor.fetchone()[0]
        if row[0] or row[1] or service_count or vlanif_count:
          conn.close(); raise ValueError(f"VLAN {vlan_id} still has a port, VLANIF, or service-port binding.")
        cursor.execute("DELETE FROM olt_vlan WHERE username = ? AND vlan_id = ?", (self.username, vlan_id))
        succeeded += cursor.rowcount
    elif operation == "bind-port":
      ports = options["ports"]
      for vlan_id in vlan_ids:
        cursor.execute("SELECT 1 FROM olt_vlan WHERE username = ? AND vlan_id = ?", (self.username, vlan_id))
        if not cursor.fetchone():
          conn.close(); raise ValueError(f"VLAN {vlan_id} does not exist.")
        for port in ports:
          cursor.execute("INSERT OR IGNORE INTO olt_node_vlan_ports (username,node_name,vlan_id,port) VALUES (?,?,?,?)", (self.username, self.node_name, vlan_id, port))
        cursor.execute("SELECT DISTINCT port FROM olt_node_vlan_ports WHERE username = ? AND vlan_id = ? ORDER BY port", (self.username, vlan_id))
        bound = [row[0] for row in cursor.fetchall()]
        cursor.execute("UPDATE olt_vlan SET bound_port = ?, bound_state = 'up', bound_tag = 'tag' WHERE username = ? AND vlan_id = ?", (",".join(bound), self.username, vlan_id))
        succeeded += 1
    elif operation == "unbind-port":
      ports = options["ports"]
      for vlan_id in vlan_ids:
        cursor.execute("SELECT 1 FROM olt_vlan WHERE username = ? AND vlan_id = ?", (self.username, vlan_id))
        if not cursor.fetchone(): continue
        for port in ports:
          cursor.execute("DELETE FROM olt_node_vlan_ports WHERE username = ? AND node_name = ? AND vlan_id = ? AND port = ?", (self.username, self.node_name, vlan_id, port))
        cursor.execute("SELECT DISTINCT port FROM olt_node_vlan_ports WHERE username = ? AND vlan_id = ? ORDER BY port", (self.username, vlan_id))
        bound = [row[0] for row in cursor.fetchall()]
        cursor.execute("UPDATE olt_vlan SET bound_port = ?, bound_state = ?, bound_tag = ? WHERE username = ? AND vlan_id = ?", (",".join(bound), "up" if bound else "", "tag" if bound else "", self.username, vlan_id))
        succeeded += 1
    conn.commit(); conn.close()
    return succeeded

  def _configure_vlanif_ip(self, cmd, remove=False):
    parts = shlex.split(cmd)
    if remove and parts and parts[0].lower() == "undo":
      parts = parts[1:]
    conn = get_connection(); cursor = conn.cursor()
    if remove:
      if len(parts) <= 2:
        cursor.execute("DELETE FROM olt_node_vlanif_addresses WHERE username = ? AND node_name = ? AND vlan_id = ?", (self.username, self.node_name, self.current_vlanif_id))
      else:
        address = parts[2]
        cursor.execute("SELECT secondary FROM olt_node_vlanif_addresses WHERE username = ? AND node_name = ? AND vlan_id = ? AND address = ?", (self.username, self.node_name, self.current_vlanif_id, address))
        row = cursor.fetchone()
        if not row: conn.close(); raise ValueError("IP address was not configured on this VLANIF.")
        if not row[0]:
          cursor.execute("SELECT COUNT(*) FROM olt_node_vlanif_addresses WHERE username = ? AND node_name = ? AND vlan_id = ? AND secondary = 1", (self.username, self.node_name, self.current_vlanif_id))
          if cursor.fetchone()[0]: conn.close(); raise ValueError("Delete secondary IP addresses before deleting the primary address.")
        cursor.execute("DELETE FROM olt_node_vlanif_addresses WHERE username = ? AND node_name = ? AND vlan_id = ? AND address = ?", (self.username, self.node_name, self.current_vlanif_id, address))
      conn.commit(); conn.close()
      self.append_plain_text("\n  IP address configuration removed from the VLANIF.")
      return

    if len(parts) < 4: conn.close(); raise ValueError("Use ip address <IPv4> <mask|prefix> [sub] [tag <tag>] [description <text>].")
    address, mask = parts[2], parts[3]
    try:
      ip_obj = ipaddress.IPv4Address(address)
      network = ipaddress.IPv4Network(f"{address}/{mask}", strict=False)
    except ipaddress.AddressValueError as exc:
      conn.close(); raise ValueError("Invalid IPv4 address or subnet mask.") from exc
    except ipaddress.NetmaskValueError as exc:
      conn.close(); raise ValueError("Mask must be a valid IPv4 mask or prefix length 0-32.") from exc
    if network.prefixlen < 31 and ip_obj in (network.network_address, network.broadcast_address):
      conn.close(); raise ValueError("Network and broadcast addresses cannot be assigned to the interface.")
    lower = [part.lower() for part in parts]
    secondary = int("sub" in lower)
    tag = int(parts[lower.index("tag") + 1]) if "tag" in lower else None
    description = " ".join(parts[lower.index("description") + 1:]) if "description" in lower else ""
    if tag is not None and not 1 <= tag <= 4294967295: conn.close(); raise ValueError("tag must be in range 1-4294967295.")
    if description and len(description) > 192: conn.close(); raise ValueError("description must not exceed 192 characters.")
    if secondary:
      cursor.execute("SELECT COUNT(*) FROM olt_node_vlanif_addresses WHERE username = ? AND node_name = ? AND vlan_id = ? AND secondary = 0", (self.username, self.node_name, self.current_vlanif_id))
      if not cursor.fetchone()[0]: conn.close(); raise ValueError("Configure a primary IP address before a secondary address.")
    cursor.execute("SELECT vlan_id,address,mask FROM olt_node_vlanif_addresses WHERE username = ? AND node_name = ? AND vlan_id <> ?", (self.username, self.node_name, self.current_vlanif_id))
    for other_vlan, other_address, other_mask in cursor.fetchall():
      other_net = ipaddress.IPv4Network(f"{other_address}/{other_mask}", strict=False)
      if network.overlaps(other_net): conn.close(); raise ValueError(f"Subnet overlaps with VLANIF {other_vlan}.")
    if not secondary:
      cursor.execute("DELETE FROM olt_node_vlanif_addresses WHERE username = ? AND node_name = ? AND vlan_id = ? AND secondary = 0", (self.username, self.node_name, self.current_vlanif_id))
    cursor.execute("INSERT OR REPLACE INTO olt_node_vlanif_addresses (username,node_name,vlan_id,address,mask,secondary,tag,description) VALUES (?,?,?,?,?,?,?,?)", (self.username, self.node_name, self.current_vlanif_id, str(ip_obj), str(network.netmask), secondary, tag, description))
    conn.commit(); conn.close()
    self.append_plain_text(f"\n  Command:\n          {cmd}")

  def _display_interface_vlanif(self, cmd, parts):
    """Display VLANIF state, addressing, and interface details."""
    if len(parts) > 4:
      self.append_plain_text("\n  Failure: Use display interface vlanif [<VLAN-ID>|main].")
      return
    requested = None
    if len(parts) == 4 and parts[3].lower() != "main":
      try:
        requested = int(parts[3])
      except ValueError:
        self.append_plain_text("\n  Failure: VLAN-ID must be an integer or main.")
        return
      if not 1 <= requested <= 4093:
        self.append_plain_text("\n  Failure: VLAN-ID must be in range 1-4093.")
        return

    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT vlan_id FROM olt_node_vlanifs WHERE username=? AND node_name=?"
    params = [self.username, self.node_name]
    if requested is not None:
      query += " AND vlan_id=?"
      params.append(requested)
    query += " ORDER BY vlan_id"
    vlan_ids = [row[0] for row in cursor.execute(query, params).fetchall()]
    if requested is not None and not vlan_ids:
      conn.close()
      self.append_plain_text(f"\n  Failure: VLANIF {requested} has not been created.")
      return

    cursor.execute(
        "SELECT vlan_id,address,mask,secondary,description FROM olt_node_vlanif_addresses "
        "WHERE username=? AND node_name=? ORDER BY vlan_id,secondary,address",
        (self.username, self.node_name),
    )
    addresses = {}
    for vlan_id, address, mask, secondary, description in cursor.fetchall():
      addresses.setdefault(vlan_id, []).append((address, mask, secondary, description or ""))
    cursor.execute(
        "SELECT source_node,target_node,source_port,target_port,is_up FROM olt_device_links "
        "WHERE username=? AND is_up=1",
        (self.username,),
    )
    links = cursor.fetchall()
    cursor.execute(
        "SELECT vlan_id,port FROM olt_node_vlan_ports WHERE username=? AND node_name=?",
        (self.username, self.node_name),
    )
    vlan_ports = {}
    for vlan_id, port in cursor.fetchall():
      vlan_ports.setdefault(vlan_id, set()).add(port)
    conn.close()

    now = datetime.now().astimezone()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S%z")
    timestamp = timestamp[:-2] + ":" + timestamp[-2:]
    output = [f"\n  Command:\n          {cmd}"]
    if not vlan_ids:
      output.append("  (No VLANIF interfaces configured)")
    for vlan_id in vlan_ids:
      allowed_ports = vlan_ports.get(vlan_id, set())
      physical_up = False
      for source_node, target_node, source_port, target_port, _is_up in links:
        if source_node == self.node_name:
          local_port = source_port
        elif target_node == self.node_name:
          local_port = target_port
        else:
          continue
        if allowed_ports and local_port not in allowed_ports:
          continue
        physical_up = True
        break
      configured = addresses.get(vlan_id, [])
      primary = next((row for row in configured if not row[2]), None)
      state = "UP" if physical_up else "DOWN"
      protocol = "UP" if physical_up and primary else "DOWN"
      mac_seed = sum((index + 1) * ord(char) for index, char in enumerate(self.node_name))
      mac = f"02:00:{(mac_seed >> 16) & 255:02x}:{(mac_seed >> 8) & 255:02x}:{mac_seed & 255:02x}:{vlan_id & 255:02x}"
      description = primary[3] if primary and primary[3] else f"HUAWEI, AC Series, Vlanif{vlan_id} Interface"
      output.extend([
          f"\n  Vlanif{vlan_id} current state : {state}",
          f"  Line protocol current state : {protocol}",
          f"  Description : {description}",
          "  Route Port,The Maximum Transmit Unit is 1500 bytes",
      ])
      if primary:
        prefix = ipaddress.IPv4Network(f"0.0.0.0/{primary[1]}").prefixlen
        output.append(f"  Internet Address is {primary[0]}/{prefix}")
      else:
        output.append("  Internet protocol processing : disabled")
      output.extend([
          f"  IP Sending Frames' Format is PKTFMT_ETHNT_2, Hardware address is {mac.replace(':', '')}",
          f"  Current system time: {timestamp}",
          "  Last 300 seconds input bytes 0, packets 0, drops 0",
          "  Last 300 seconds output bytes 0, packets 0, drops 0",
          "  Input: 0 packets, 0 bytes",
          "  Output: 0 packets, 0 bytes",
          "  Input bandwidth utilization  : --",
          "  Output bandwidth utilization : --",
      ])
    self.append_plain_text("\n".join(output))

  def _configure_static_route(self, cmd, remove=False):
    parts = shlex.split(cmd)
    if remove and parts and parts[0].lower() == "undo":
      parts = parts[1:]
    lower = [part.lower() for part in parts]
    conn = get_connection(); cursor = conn.cursor()
    if remove and len(parts) > 2 and lower[2] == "all":
      cursor.execute("DELETE FROM olt_node_routes WHERE username = ? AND node_name = ?", (self.username, self.node_name))
      count = cursor.rowcount; conn.commit(); conn.close()
      self.append_plain_text(f"\n  Deleted {count} static route(s).")
      return
    if len(parts) < 4: conn.close(); raise ValueError("Use ip route-static <destination> <mask|prefix> <next-hop|interface>.")
    try:
      destination = ipaddress.IPv4Address(parts[2])
      network = ipaddress.IPv4Network(f"{destination}/{parts[3]}", strict=False)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as exc:
      conn.close(); raise ValueError("Invalid IPv4 destination or mask.") from exc
    outbound_interface = ""
    next_hop = ""
    index = 4
    if index < len(parts) and lower[index] == "interface-type":
      if index + 2 >= len(parts): conn.close(); raise ValueError("interface-type requires an interface type and number.")
      outbound_interface = f"{parts[index + 1]}{parts[index + 2]}"
      index += 3
      if index < len(parts):
        try: next_hop = str(ipaddress.IPv4Address(parts[index])); index += 1
        except ipaddress.AddressValueError: pass
    elif index < len(parts):
      try:
        next_hop = str(ipaddress.IPv4Address(parts[index])); index += 1
      except ipaddress.AddressValueError:
        outbound_interface = parts[index]
        index += 1
        if index < len(parts):
          try: next_hop = str(ipaddress.IPv4Address(parts[index])); index += 1
          except ipaddress.AddressValueError: pass
    opts = {"preference": 60, "tag": 0, "cost": 0, "permanent": False, "inherit_cost": False, "description": ""}
    while index < len(parts):
      key = lower[index]
      if key in ("preference", "tag", "cost"):
        if index + 1 >= len(parts): conn.close(); raise ValueError(f"{key} requires a value.")
        value = int(parts[index + 1]); limits = {"preference": (1,255), "tag": (1,4294967295), "cost": (1,4294967295)}
        if not limits[key][0] <= value <= limits[key][1]: conn.close(); raise ValueError(f"{key} value is out of range.")
        opts[key] = value; index += 2
      elif key == "permanent": opts["permanent"] = True; index += 1
      elif key == "inherit-cost": opts["inherit_cost"] = True; index += 1
      elif key == "description":
        opts["description"] = " ".join(parts[index + 1:]); index = len(parts)
      elif key in ("bfd", "track", "vpn-instance"):
        # Preserve the documented optional feature values even though the simulator
        # does not emulate BFD or VPN forwarding state.
        if index + 1 >= len(parts): conn.close(); raise ValueError(f"{key} requires a value.")
        opts.setdefault("advanced", []).extend(parts[index:index + 2]); index += 2
      else:
        conn.close(); raise ValueError(f"Unsupported static-route option: {parts[index]}.")
    if opts["inherit_cost"] and outbound_interface:
      conn.close(); raise ValueError("inherit-cost cannot be used when an outbound interface is specified.")
    dest, mask = str(network.network_address), str(network.netmask)
    if remove:
      cursor.execute("DELETE FROM olt_node_routes WHERE username = ? AND node_name = ? AND dest = ? AND mask = ? AND gateway = ? AND outbound_interface = ?", (self.username, self.node_name, dest, mask, next_hop, outbound_interface))
      count = cursor.rowcount; conn.commit(); conn.close()
      if not count: raise ValueError("Static route was not found.")
      self.append_plain_text("\n  Static route deleted.")
      return
    cursor.execute("INSERT OR REPLACE INTO olt_node_routes (username,node_name,dest,mask,gateway,preference,tag,cost,permanent,outbound_interface,description,options_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (self.username, self.node_name, dest, mask, next_hop, opts["preference"], opts["tag"], opts["cost"], int(opts["permanent"]), outbound_interface, opts["description"], json.dumps(opts)))
    conn.commit(); conn.close()
    self.append_plain_text(f"\n  Static route {dest}/{network.prefixlen} via {next_hop or outbound_interface} configured.")

  def _execute_ping(self, cmd):
    try:
      tokens = shlex.split(cmd)
      lower = [token.lower() for token in tokens]
      if tokens and lower[0] == "ping": tokens = tokens[1:]; lower = lower[1:]
      if tokens and lower[0] == "ip": tokens = tokens[1:]; lower = lower[1:]
      if tokens and lower[-1] == "ip-forwarding": tokens = tokens[:-1]; lower = lower[:-1]
      count, payload, source_ip, vlanif = 5, 56, None, None
      destination_token = None
      i = 0
      while i < len(tokens):
        token = lower[i]
        if token in ("-a", "-c", "-s", "-t", "-h", "-tos", "-dscp", "-8021p", "-m", "-p"):
          if i + 1 >= len(tokens): raise ValueError(f"{tokens[i]} requires a value.")
          value = tokens[i + 1]
          if token == "-a": source_ip = str(ipaddress.IPv4Address(value))
          elif token == "-c": count = int(value)
          elif token == "-s": payload = int(value)
          i += 2; continue
        if token == "-i":
          if i + 2 >= len(tokens): raise ValueError("-i requires interface type and interface number.")
          if lower[i + 1] == "vlanif": vlanif = int(tokens[i + 2].lower().replace("vlanif", ""))
          i += 3; continue
        if token in ("-brief", "-q", "-detail", "-v", "-r", "-f", "ignore-mtu", "-system-time", "ip-forwarding"):
          i += 1; continue
        if token.startswith("-"):
          raise ValueError(f"Unsupported ping option: {tokens[i]}.")
        # The destination can appear before or after the optional parameters.
        # Validate it now, so a trailing option is not mistaken for the target.
        try:
          destination_token = str(ipaddress.IPv4Address(tokens[i]))
        except ipaddress.AddressValueError as exc:
          raise ValueError(f"Invalid ping destination: {tokens[i]}.") from exc
        i += 1
      if destination_token is None: raise ValueError("Destination host is required.")
      destination = destination_token
      if not 1 <= count <= 1000: raise ValueError("Ping count must be in range 1-1000 in the simulator.")
      if not 20 <= payload <= 8100: raise ValueError("Packet size must be in range 20-8100 bytes.")

      conn = get_connection(); cursor = conn.cursor()
      cursor.execute("SELECT vlan_id,address,mask FROM olt_node_vlanif_addresses WHERE username = ? AND node_name = ?", (self.username, self.node_name))
      local_rows = cursor.fetchall()
      cursor.execute("SELECT vlan_id,address,mask FROM olt_node_vlanif_addresses WHERE username = ? AND address = ? AND node_name <> ?", (self.username, destination, self.node_name))
      remote_rows = cursor.fetchall()
      local_address = next((row for row in local_rows if row[1] == destination), None)
      reachable = local_address is not None
      chosen_source = source_ip or (local_address[1] if local_address else None)
      target_node = self.node_name

      if not reachable:
        for remote_vlan, _remote_ip, remote_mask in remote_rows:
          remote_network = ipaddress.IPv4Network(f"{destination}/{remote_mask}", strict=False)
          for local_vlan, local_ip, local_mask in local_rows:
            local_network = ipaddress.IPv4Network(f"{local_ip}/{local_mask}", strict=False)
            if vlanif is not None and vlanif != local_vlan: continue
            if local_vlan != remote_vlan or local_network != remote_network or ipaddress.IPv4Address(destination) not in local_network: continue
            if source_ip and source_ip != local_ip: continue
            chosen_source = source_ip or local_ip
            cursor.execute("SELECT node_name FROM olt_node_vlanif_addresses WHERE username = ? AND vlan_id = ? AND address = ?", (self.username, remote_vlan, destination))
            remote_node_row = cursor.fetchone()
            if not remote_node_row: continue
            target_node = remote_node_row[0]

            cursor.execute("SELECT source_node,target_node,source_port,target_port,is_up FROM olt_device_links WHERE username = ? AND is_up = 1", (self.username,))
            edges = cursor.fetchall()
            cursor.execute("SELECT node_name,port FROM olt_node_vlan_ports WHERE username = ? AND vlan_id = ?", (self.username, local_vlan))
            vlan_ports = {}
            for node_name, port in cursor.fetchall(): vlan_ports.setdefault(node_name, set()).add(port)
            graph = {}
            for a, b, port_a, port_b, is_up in edges:
              if not is_up: continue
              # MA5801 10GE uplink ports are the physical path between OLTs.
              if not (port_a.startswith("0/2/") and port_b.startswith("0/2/")): continue
              def lag_membership(node_name, port):
                return cursor.execute(
                    "SELECT g.master_port,g.workmode FROM olt_link_aggregation_members m "
                    "JOIN olt_link_aggregations g ON g.username=m.username AND g.node_name=m.node_name AND g.master_port=m.master_port "
                    "WHERE m.username=? AND m.node_name=? AND m.member_port=?",
                    (self.username, node_name, port),
                ).fetchone()
              lag_a, lag_b = lag_membership(a, port_a), lag_membership(b, port_b)
              # A static-LACP member only forwards when its peer belongs to a
              # matching static-LACP group. Unbundled ports remain usable as
              # ordinary links, matching the non-LAG simulator behavior.
              if bool(lag_a) != bool(lag_b): continue
              if lag_a and lag_a[1] != lag_b[1]: continue
              if lag_a and lag_a[1] not in ("manual", "lacp-static"): continue
              allowed_a, allowed_b = vlan_ports.get(a, set()), vlan_ports.get(b, set())
              # Enforce membership independently at each endpoint. A VLAN binding
              # on an unrelated OLT must not block this link; an endpoint with no
              # explicit VLAN port list remains an open trunk in the simulator.
              if (allowed_a and port_a not in allowed_a) or (allowed_b and port_b not in allowed_b): continue
              graph.setdefault(a, set()).add(b); graph.setdefault(b, set()).add(a)
            visited, queue = {self.node_name}, [self.node_name]
            while queue:
              current = queue.pop(0)
              if current == target_node: reachable = True; break
              for neighbor in graph.get(current, ()):
                if neighbor not in visited: visited.add(neighbor); queue.append(neighbor)
            if reachable: break
          if reachable: break

      if not reachable:
        # OLT <-> DHCP/PPPoE server lewat access port + VLANIF se-subnet
        servers = cursor.execute(
            "SELECT node_name, local_ip FROM sim_service_nodes"
            " WHERE username = ? AND local_ip <> ''",
            (self.username,),
        ).fetchall()
        for server_node, server_ip in servers:
          if server_ip != destination:
            continue
          link = cursor.execute(
              "SELECT source_node, source_port, target_node, target_port"
              " FROM olt_device_links WHERE username = ? AND is_up = 1 AND"
              " ((source_node = ? AND target_node = ?) OR"
              "  (source_node = ? AND target_node = ?))",
              (
                  self.username,
                  self.node_name,
                  server_node,
                  server_node,
                  self.node_name,
              ),
          ).fetchone()
          if not link:
            continue
          olt_port = link[1] if link[0] == self.node_name else link[3]
          board, _, port_text = olt_port.rpartition("/")
          if not port_text.isdigit():
            continue
          native = cursor.execute(
              "SELECT vlan_id FROM olt_eth_native_vlans WHERE username = ?"
              " AND node_name = ? AND board = ? AND port_id = ?",
              (self.username, self.node_name, board, int(port_text)),
          ).fetchone()
          if not native:
            continue
          for local_vlan, local_ip, local_mask in local_rows:
            if local_vlan != native[0]:
              continue
            if vlanif is not None and vlanif != local_vlan:
              continue
            network = ipaddress.IPv4Network(f"{local_ip}/{local_mask}", strict=False)
            if ipaddress.IPv4Address(destination) in network:
              reachable = True
              chosen_source = source_ip or local_ip
              target_node = server_node
              break
          if reachable:
            break
      conn.close()
      if source_ip and not any(row[1] == source_ip for row in local_rows):
        reachable = False
      if chosen_source is None: chosen_source = "0.0.0.0"
      if "-brief" in lower:
        self.append_plain_text(f"\n  PING {destination}: {payload} data bytes, press CTRL_C to break\n    {'.' * count if reachable else '!' * count}")
      else:
        self.append_plain_text(f"\nPING {destination}: {payload} data bytes, press CTRL_C to break")
        for sequence in range(1, count + 1):
          if reachable:
            self.append_plain_text(f"\nReply from {destination}: bytes={payload} Sequence={sequence} ttl=255 time=1 ms")
          else:
            self.append_plain_text(f"\nRequest time out")
      received = count if reachable else 0
      loss = (count - received) * 100 / count
      self.append_plain_text(
          f"\n\n--- {destination} ping statistics ---\n{count} packet(s) transmitted\n"
          f"{received} packet(s) received\n{loss:.2f}% packet loss"
          + ("\nround-trip min/avg/max = 1/1/1 ms" if reachable else "")
      )
    except (ValueError, IndexError) as exc:
      self.append_plain_text(f"\n  Failure: {exc}")

  def _handle_traffic_table_command(self, cmd, parts, lower):
    """Create, remove, and display Huawei-style IP traffic descriptors."""
    conn = get_connection(); cursor = conn.cursor()
    try:
      if lower[:4] == ["undo", "traffic", "table", "ip"]:
        tail = lower[4:]
        if not tail:
          raise ValueError("Use undo traffic table ip {index <ID>|name <name>|unused {all|first}}.")
        if tail[0] == "unused":
          if len(tail) != 2 or tail[1] not in ("all", "first"):
            raise ValueError("Use undo traffic table ip unused {all|first}.")
          cursor.execute("SELECT profile_id FROM olt_traffic_tables WHERE username = ? ORDER BY profile_id", (self.username,))
          candidates = [row[0] for row in cursor.fetchall() if row[0] > 6]
          if tail[1] == "first": candidates = candidates[:1]
          deleted = 0
          for profile_id in candidates:
            cursor.execute("SELECT COUNT(*) FROM olt_service_ports WHERE username = ? AND (inbound_traffic_index = ? OR outbound_traffic_index = ?)", (self.username, profile_id, profile_id))
            if not cursor.fetchone()[0]:
              cursor.execute("DELETE FROM olt_traffic_tables WHERE username = ? AND profile_id = ?", (self.username, profile_id)); deleted += cursor.rowcount
          conn.commit()
          self.append_plain_text(f"\n  Deleted {deleted} unused traffic table profile(s).")
          return True
        selector = tail[0]
        value = tail[1] if len(tail) > 1 and selector in ("index", "name") else selector
        if selector == "index" or (selector not in ("name",) and value.isdigit()):
          profile_id = int(value)
          if profile_id <= 6: raise ValueError("Default traffic table profiles 0-6 cannot be deleted.")
          cursor.execute("SELECT COUNT(*) FROM olt_service_ports WHERE username = ? AND (inbound_traffic_index = ? OR outbound_traffic_index = ?)", (self.username, profile_id, profile_id))
          if cursor.fetchone()[0]: raise ValueError(f"Traffic table {profile_id} is referenced by a service port.")
          cursor.execute("DELETE FROM olt_traffic_tables WHERE username = ? AND profile_id = ?", (self.username, profile_id))
        elif selector == "name":
          cursor.execute("SELECT profile_id FROM olt_traffic_tables WHERE username = ? AND (profile_name = ? OR ('ip-traffic-table_' || profile_id) = ?)", (self.username, value, value))
          profile_row = cursor.fetchone()
          if not profile_row: raise ValueError("Traffic table profile was not found.")
          profile_id = profile_row[0]
          if profile_id <= 6: raise ValueError("Default traffic table profiles 0-6 cannot be deleted.")
          cursor.execute("SELECT COUNT(*) FROM olt_service_ports WHERE username = ? AND (inbound_traffic_index = ? OR outbound_traffic_index = ?)", (self.username, profile_id, profile_id))
          if cursor.fetchone()[0]: raise ValueError(f"Traffic table {profile_id} is referenced by a service port.")
          cursor.execute("DELETE FROM olt_traffic_tables WHERE username = ? AND profile_id = ?", (self.username, profile_id))
        else:
          raise ValueError("Specify traffic table index or name.")
        if not cursor.rowcount: raise ValueError("Traffic table profile was not found.")
        conn.commit(); self.append_plain_text("\n  Traffic table profile deleted.")
        return True

      if lower[:3] == ["display", "traffic", "table"]:
        tail = lower[3:]
        if tail and tail[0] == "ip": tail = tail[1:]
        query_kind, query_value, detail = "all", None, False
        if tail:
          if tail[0] in ("index", "name") and len(tail) >= 2:
            query_kind, query_value = tail[0], parts[4 if lower[3] == "ip" else 3 + 1]
          elif tail[0] == "from-index" and len(tail) >= 2:
            query_kind, query_value = "range", int(tail[1])
            if not 0 <= query_value <= 1023: raise ValueError("from-index must be in range 0-1023.")
            if "to-index" in tail:
              query_end = int(tail[tail.index("to-index") + 1])
              if query_end < 0 or query_end > 1023: raise ValueError("to-index must be in range 0-1023.")
            else: query_end = 1023
            detail = "detail" in tail
          else: raise ValueError("Use display traffic table ip {index <ID>|name <name>|from-index <ID> [to-index <ID>] [detail]}.")
        if query_kind == "index":
          cursor.execute("SELECT profile_id,profile_name,cir,priority,priority_policy,options_json FROM olt_traffic_tables WHERE username = ? AND profile_id = ?", (self.username, int(query_value)))
        elif query_kind == "name":
          cursor.execute("SELECT profile_id,profile_name,cir,priority,priority_policy,options_json FROM olt_traffic_tables WHERE username = ? AND (profile_name = ? OR ('ip-traffic-table_' || profile_id) = ?)", (self.username, query_value, query_value))
        elif query_kind == "range":
          cursor.execute("SELECT profile_id,profile_name,cir,priority,priority_policy,options_json FROM olt_traffic_tables WHERE username = ? AND profile_id BETWEEN ? AND ? ORDER BY profile_id", (self.username, query_value, query_end))
        else:
          cursor.execute("SELECT profile_id,profile_name,cir,priority,priority_policy,options_json FROM olt_traffic_tables WHERE username = ? ORDER BY profile_id", (self.username,))
        rows = cursor.fetchall()
        if query_kind in ("index", "name") or detail:
          lines = [f"\n  Command:\n          {cmd}"]
          for profile_id, name, cir, priority, policy, options_json in rows:
            opts = json.loads(options_json or "{}")
            lines.extend([
                "  --------------------------------------------------------",
                f"  Traffic Table Index          : {profile_id}",
                f"  Traffic Table Name           : {name or f'ip-traffic-table_{profile_id}'}",
                f"  CIR                          : {cir}",
                f"  CBS                          : {opts.get('cbs', '-')}",
                f"  PIR                          : {opts.get('pir', '-')}",
                f"  PBS                          : {opts.get('pbs', '-')}",
                f"  Fix                          : {opts.get('fix', '-')}",
                f"  Priority                     : {opts.get('priority_source', 'user-cos')} {priority}",
                f"  Priority Policy              : {policy}",
            ])
            for key, value in opts.items():
              if key not in ("priority_source", "cbs", "pir", "pbs", "fix"):
                lines.append(f"  {key.replace('_', ' ').title():<30}: {value}")
          lines.append("  --------------------------------------------------------")
        else:
          lines = [f"\n  Command:\n          {cmd}", "  Index       Name                              CIR       Priority  Priority-policy", "  -------------------------------------------------------------------------------"]
          lines.extend(f"  {r[0]:<11} {(r[1] or f'ip-traffic-table_{r[0]}'):<33} {r[2]:<9} {r[3]:<9} {r[4]}" for r in rows)
          lines.extend(["  -------------------------------------------------------------------------------", f"  Total: {len(rows)}"])
        self.append_plain_text("\n".join(lines))
        return True

      if lower[:3] != ["traffic", "table", "ip"]:
        raise ValueError("Unsupported traffic table command.")
      tokens = parts[3:]
      token_lower = [token.lower() for token in tokens]
      if token_lower and token_lower[0] == "modify":
        tokens, token_lower = tokens[1:], token_lower[1:]
      options = {}
      profile_id, profile_name = None, ""
      if "index" in token_lower:
        idx = token_lower.index("index")
        profile_id = int(tokens[idx + 1])
      if "name" in token_lower:
        idx = token_lower.index("name")
        profile_name = tokens[idx + 1]
      if profile_id is None:
        cursor.execute("SELECT profile_id FROM olt_traffic_tables WHERE username = ? ORDER BY profile_id", (self.username,))
        used = {row[0] for row in cursor.fetchall()} | set(range(7))
        profile_id = next((candidate for candidate in range(7, 1024) if candidate not in used), None)
        if profile_id is None: raise ValueError("No traffic table index is available.")
      if not 0 <= profile_id <= 1023: raise ValueError("Traffic table index must be in range 0-1023.")
      cir = "off"
      if "cir" in token_lower:
        cir = tokens[token_lower.index("cir") + 1].lower()
        if cir != "off":
          cir_value = int(cir)
          if not 64 <= cir_value <= 2_000_000: raise ValueError("CIR must be off or in range 64-2000000 kbit/s.")
          adjusted = ((cir_value + 63) // 64) * 64
          if adjusted != cir_value:
            self.append_plain_text(f"\n  CIR {cir_value} has been adjusted to rank of {adjusted} <kbps>.")
          cir = str(adjusted)
      if "rate" in token_lower and "profile" in token_lower:
        pos = token_lower.index("profile")
        if pos + 2 < len(tokens):
          profile_key = token_lower[pos + 1]
          profile_value = tokens[pos + 2]
          if profile_key not in ("profile-id", "profile-name"): raise ValueError("Use rate profile profile-id <id> or profile-name <name>.")
          options[f"rate_{profile_key.replace('-', '_')}"] = int(profile_value) if profile_key == "profile-id" else profile_value
      for key in ("cbs", "pir", "pbs", "fix", "convergence-ratio", "car-threshold", "color-mode", "coupling-flag", "color-policy", "color-dei", "color-source", "drop-precedence", "priority-group"):
        if key in token_lower:
          pos = token_lower.index(key)
          if pos + 1 >= len(tokens): raise ValueError(f"{key} requires a value.")
          if key == "priority-group" and token_lower[pos + 1] == "profile" and pos + 2 < len(tokens):
            options["priority_group_profile"] = tokens[pos + 2]
          else:
            options[key.replace("-", "_")] = tokens[pos + 1]
      if "priority" not in token_lower: raise ValueError("Specify priority <0-7> and priority-policy <policy>.")
      priority_pos = token_lower.index("priority") + 1
      priority_source = "user-cos"
      if priority_pos < len(tokens) and token_lower[priority_pos] in ("user-cos", "user-inner-cos", "user-tos", "user-dscp"):
        priority_source = token_lower[priority_pos]; priority_pos += 1
        if priority_pos < len(tokens) and token_lower[priority_pos] == "mapping-profile":
          options["mapping_profile"] = int(tokens[priority_pos + 1]); priority_pos += 2
      if priority_pos >= len(tokens): raise ValueError("Priority value is required.")
      priority_value = tokens[priority_pos].lower()
      if priority_value == "defaultval":
        if priority_pos + 1 >= len(tokens): raise ValueError("defaultval requires a priority value.")
        priority_value = tokens[priority_pos + 1]
      priority = int(priority_value)
      if not 0 <= priority <= 7: raise ValueError("Priority must be in range 0-7.")
      options["priority_source"] = priority_source
      if "priority-policy" not in token_lower: raise ValueError("Specify priority-policy.")
      policy_pos = token_lower.index("priority-policy") + 1
      policy = token_lower[policy_pos]
      if policy == "profile" and policy_pos + 1 < len(tokens):
        options["priority_policy_profile"] = int(tokens[policy_pos + 1]); policy = "queue-profile"
      if policy not in ("local-setting", "user-setting", "profile", "queue-profile"):
        raise ValueError("priority-policy must be local-setting, user-setting, or profile <ID>.")
      if profile_name and (len(profile_name) > 32 or profile_name.lower().startswith("ip-traffic-table_")):
        raise ValueError("Traffic table name must be 1-32 characters and cannot use the reserved default name format.")
      options["command_options"] = tokens
      cursor.execute("INSERT OR REPLACE INTO olt_traffic_tables (username,profile_id,cir,priority,priority_policy,profile_name,options_json) VALUES (?,?,?,?,?,?,?)", (self.username, profile_id, cir, priority, policy, profile_name, json.dumps(options)))
      conn.commit()
      self.append_plain_text(f"\n  Create traffic descriptor record successfully\n  Traffic Table Index : {profile_id}\n  Traffic Table Name  : {profile_name or f'ip-traffic-table_{profile_id}'}\n  CIR                 : {cir}\n  Priority            : {priority}\n  Priority Policy     : {policy}")
      return True
    finally:
      conn.close()

  def _handle_service_port_command(self, cmd, parts, lower):
    """Handle the documented GPON service-port provisioning and display forms."""
    if lower[0] == "display":
      conn = get_connection(); cursor = conn.cursor()
      cursor.execute("SELECT sp_id,vlan,port_type,port,ont_id,gemport,user_vlan,state,inbound_traffic_index,outbound_traffic_index,options_json FROM olt_service_ports WHERE username = ? ORDER BY sp_id", (self.username,))
      rows = cursor.fetchall(); conn.close()
      if "index" in lower:
        try: selected = int(parts[lower.index("index") + 1]); rows = [row for row in rows if row[0] == selected]
        except (ValueError, IndexError): self.append_plain_text("\n  Failure: Use display service-port {all|index <ID>}."); return True
      lines = [f"\n  Command:\n          {cmd}", "  -----------------------------------------------------------------------------------------------", "  Index  VLAN  Type  F/S/P       ONT  GEM  User-VLAN     State  Inbound-Traffic  Outbound-Traffic", "  -----------------------------------------------------------------------------------------------"]
      lines.extend(f"  {r[0]:<6} {r[1]:<5} {r[2]:<5} {r[3]:<11} {str(r[4] or '-'): <4} {str(r[5] or '-'): <4} {str(r[6] or '-'): <13} {r[7]:<6} {str(r[8] if r[8] is not None else '-'): <16} {r[9] if r[9] is not None else '-'}" for r in rows)
      lines.extend(["  -----------------------------------------------------------------------------------------------", f"  Total service-port number: {len(rows)}"])
      self.append_plain_text("\n".join(lines)); return True

    if lower[0] == "undo":
      conn = get_connection(); cursor = conn.cursor()
      if len(lower) >= 4 and lower[1:3] == ["service-port", "index"]:
        criteria, value = "sp_id", int(parts[3])
        cursor.execute("DELETE FROM olt_service_ports WHERE username = ? AND sp_id = ?", (self.username, value))
      elif len(lower) >= 4 and lower[1:3] == ["service-port", "vlan"]:
        value = parts[3]
        filters, values = [], []
        try:
          vlan_id = int(value)
          filters.append("vlan = ?"); values.append(vlan_id)
        except ValueError:
          filters.append("vlan IN (SELECT vlan_id FROM olt_vlan WHERE username = ? AND vlan_name = ?)"); values.extend((self.username, value))
        if "dedicated-net-id" in lower: filters.append("dedicated_net_id = ?"); values.append(int(parts[lower.index("dedicated-net-id") + 1]))
        if "gpon" in lower or "epon" in lower or "eth" in lower:
          access = next(item for item in ("gpon", "epon", "eth") if item in lower)
          port_pos = lower.index(access)
          filters.extend(("port_type = ?", "port = ?")); values.extend((access, parts[port_pos + 1]))
        if "ont" in lower:
          filters.append("ont_id = ?"); values.append(int(parts[lower.index("ont") + 1]))
        if "gemport" in lower:
          filters.append("gemport = ?"); values.append(int(parts[lower.index("gemport") + 1]))
        cursor.execute("DELETE FROM olt_service_ports WHERE username = ? AND " + " AND ".join(filters), [self.username, *values])
      elif len(lower) == 3 and lower[1:] == ["service-port", "all"]:
        cursor.execute("DELETE FROM olt_service_ports WHERE username = ?", (self.username,))
      elif len(lower) >= 4 and lower[1:3] == ["service-port", "port"]:
        port = parts[3]
        if "ont" not in lower: raise ValueError("Use undo service-port port <F/S/P> ont <ONT-ID|ONT-list>.")
        ont_value = parts[lower.index("ont") + 1]
        ids = set()
        for segment in ont_value.split(","):
          if "-" in segment:
            start, end = map(int, segment.split("-", 1)); ids.update(range(start, end + 1))
          else: ids.add(int(segment))
        placeholders = ",".join("?" for _ in ids)
        cursor.execute(f"DELETE FROM olt_service_ports WHERE username = ? AND port = ? AND ont_id IN ({placeholders})", [self.username, port, *sorted(ids)])
      else:
        conn.close(); raise ValueError("Unsupported undo service-port form.")
      count = cursor.rowcount; conn.commit(); conn.close()
      self.append_plain_text(f"\n  {count} service-port(s) deleted.")
      return True

    tokens = parts[1:]
    token_lower = [token.lower() for token in tokens]
    sp_id = None
    if token_lower and token_lower[0] == "index": sp_id = int(tokens[1]); tokens, token_lower = tokens[2:], token_lower[2:]
    elif token_lower and token_lower[0].isdigit(): sp_id = int(tokens[0]); tokens, token_lower = tokens[1:], token_lower[1:]
    if "vlan" not in token_lower: raise ValueError("Use service-port [index <ID>] vlan <VLAN> gpon <F/S/P> ont <ID> gemport <ID> [traffic-table ...].")
    vlan_pos = token_lower.index("vlan")
    vlan_value = tokens[vlan_pos + 1]
    vlan_name_form = vlan_value.lower() == "name"
    if vlan_name_form:
      if vlan_pos + 2 >= len(tokens): raise ValueError("vlan name requires a VLAN name.")
      vlan_value = tokens[vlan_pos + 2]
    conn = get_connection(); cursor = conn.cursor()
    try:
      try:
        vlan_id = int(vlan_value)
        cursor.execute("SELECT vlan_name FROM olt_vlan WHERE username = ? AND vlan_id = ?", (self.username, vlan_id))
      except ValueError:
        cursor.execute("SELECT vlan_id,vlan_name FROM olt_vlan WHERE username = ? AND vlan_name = ?", (self.username, vlan_value))
        vlan_row = cursor.fetchone()
        if not vlan_row: raise ValueError(f"VLAN {vlan_value} does not exist.")
        vlan_id = vlan_row[0]
      vlan_row = cursor.fetchone() if vlan_value.isdigit() else (vlan_id, vlan_value)
      if not vlan_row: raise ValueError(f"VLAN {vlan_id} does not exist.")
      access_pos = next((idx for idx, item in enumerate(token_lower) if item in ("gpon", "epon", "eth", "port")), None)
      if access_pos is None or access_pos + 1 >= len(tokens): raise ValueError("Specify GPON/EPON/ETH access port.")
      access_type = token_lower[access_pos]
      physical_port = tokens[access_pos + 1]
      ont_id = gemport = None
      if "ont" in token_lower:
        ont_id = int(tokens[token_lower.index("ont") + 1])
      if "gemport" in token_lower:
        gemport = int(tokens[token_lower.index("gemport") + 1])
      if access_type == "gpon" and (ont_id is None or gemport is None): raise ValueError("GPON service-port requires ont <ID> and gemport <ID>.")
      user_vlan = None
      if "user-vlan" in token_lower:
        user_vlan = tokens[token_lower.index("user-vlan") + 1].lower()
        if user_vlan not in ("untagged", "priority-tagged", "other-all"):
          user_vlan = int(user_vlan)
          if not 1 <= user_vlan <= 4094: raise ValueError("user-vlan must be untagged, priority-tagged, other-all, or 1-4094.")
      elif "multi-service" in token_lower:
        raise ValueError("multi-service requires a user-vlan, user-encap, or user-8021p classifier.")
      traffic = {"inbound": None, "outbound": None}
      for direction in traffic:
        if direction in token_lower:
          pos = token_lower.index(direction)
          if pos + 3 >= len(tokens) or token_lower[pos + 1] != "traffic-table": raise ValueError(f"{direction} requires traffic-table index <ID> or name <name>.")
          selector, value = token_lower[pos + 2], tokens[pos + 3]
          if selector == "index":
            profile_id = int(value)
            cursor.execute("SELECT 1 FROM olt_traffic_tables WHERE username = ? AND profile_id = ?", (self.username, profile_id))
            reference_exists = cursor.fetchone() is not None
          elif selector == "name":
            cursor.execute("SELECT 1 FROM olt_traffic_tables WHERE username = ? AND profile_name = ?", (self.username, value))
            reference_exists = cursor.fetchone() is not None
            if reference_exists:
              cursor.execute("SELECT profile_id FROM olt_traffic_tables WHERE username = ? AND profile_name = ?", (self.username, value)); profile_id = cursor.fetchone()[0]
          else: raise ValueError(f"Use {direction} traffic-table {{index <ID>|name <name>}}.")
          if not reference_exists: raise ValueError(f"Traffic table {value} does not exist.")
          traffic[direction] = profile_id
      dedicated_net_id = int(tokens[token_lower.index("dedicated-net-id") + 1]) if "dedicated-net-id" in token_lower else None
      uplink_port = ""
      if "uplink-port" in token_lower:
        uplink_pos = token_lower.index("uplink-port")
        uplink_port = tokens[uplink_pos + 1]
        if uplink_port.lower() == "vport" and uplink_pos + 2 < len(tokens): uplink_port += " " + tokens[uplink_pos + 2]
      tag_transform = tokens[token_lower.index("tag-transform") + 1].lower() if "tag-transform" in token_lower else "default"
      bundle_id = int(tokens[token_lower.index("bundle") + 1]) if "bundle" in token_lower else None
      cos = int(tokens[token_lower.index("cos") + 1]) if "cos" in token_lower else None
      if sp_id is None:
        cursor.execute("SELECT sp_id FROM olt_service_ports WHERE username = ? ORDER BY sp_id", (self.username,))
        used = {row[0] for row in cursor.fetchall()}; sp_id = next((candidate for candidate in range(4096) if candidate not in used), None)
      if not 0 <= sp_id <= 65535: raise ValueError("Service-port index must be in range 0-65535.")
      cursor.execute("SELECT vlan_attr FROM olt_vlan WHERE username=? AND vlan_id=?", (self.username, vlan_id))
      vlan_attribute = (cursor.fetchone() or ["common"])[0] or "common"
      qinq_inner_vlan = user_vlan
      if vlan_attribute == "q-in-q" and qinq_inner_vlan is None and access_type == "gpon" and ont_id is not None and gemport is not None:
        cursor.execute("SELECT line_profile_id FROM olt_onts WHERE username=? AND port=? AND ont_id=?", (self.username, physical_port, ont_id))
        ont_profile = cursor.fetchone()
        if ont_profile:
          cursor.execute("SELECT match_value FROM olt_line_profile_mappings WHERE username=? AND profile_id=? AND gem_id=? AND lower(match_type)='vlan' ORDER BY mapping_id LIMIT 1", (self.username, ont_profile[0], gemport))
          mapping = cursor.fetchone()
          if mapping:
            qinq_inner_vlan = mapping[0]
      stored_options = {"command": cmd, "access_type": access_type, "multi_service": "multi-service" in token_lower, "user_encap": tokens[token_lower.index("user-encap") + 1] if "user-encap" in token_lower else None, "user_8021p": int(tokens[token_lower.index("user-8021p") + 1]) if "user-8021p" in token_lower else None, "tag_transform_options": tokens[token_lower.index("tag-transform") + 2:] if "tag-transform" in token_lower else [], "vlan_attribute": vlan_attribute, "qinq_outer_vlan": vlan_id if vlan_attribute == "q-in-q" else None, "qinq_inner_vlan": qinq_inner_vlan if vlan_attribute == "q-in-q" else None}
      cursor.execute("INSERT OR REPLACE INTO olt_service_ports (username,sp_id,vlan,port_type,port,ont_id,gemport,user_vlan,state,inbound_traffic_index,outbound_traffic_index,uplink_port,dedicated_net_id,tag_transform,bundle_id,cos,options_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (self.username, sp_id, vlan_id, access_type, physical_port, ont_id, gemport, str(user_vlan) if user_vlan is not None else None, "up", traffic["inbound"], traffic["outbound"], uplink_port, dedicated_net_id, tag_transform, bundle_id, cos, json.dumps(stored_options)))
      conn.commit()
      self.append_plain_text(f"\n  Service virtual port {sp_id} created successfully.\n  VLAN: {vlan_id}; Access: {access_type} {physical_port}; ONT: {ont_id if ont_id is not None else '-'}; GEM: {gemport if gemport is not None else '-'}")
      return True
    finally:
      conn.close()

  def _display_eth_ddm_info(self, cmd, parts, lower):
    """Show simulated digital diagnostics for the local or peer SFP+ module."""
    if len(parts) not in (4, 5) or lower[:3] != ["display", "port", "ddm-info"]:
      self.append_plain_text("\n  Failure: Use display port ddm-info <portid> [remote].")
      return
    try:
      port_id = int(parts[3])
      max_port = 3 if self.current_eth_board.endswith("/2") else 27
      if not 0 <= port_id <= max_port:
        raise ValueError(f"Port ID must be in range 0-{max_port} on board {self.current_eth_board}.")
      remote = len(parts) == 5 and lower[4] == "remote"
      if len(parts) == 5 and not remote:
        raise ValueError("The optional parameter must be remote.")
    except ValueError as exc:
      self.append_plain_text(f"\n  Failure: {exc}")
      return

    local_port = f"{self.current_eth_board}/{port_id}"
    conn = get_connection()
    try:
      row = conn.execute(
          "SELECT source_node,target_node,source_port,target_port,sfp_module,"
          "fiber_type,length_km,is_up FROM olt_device_links WHERE username = ? "
          "AND ((source_node = ? AND source_port = ?) OR "
          "(target_node = ? AND target_port = ?)) LIMIT 1",
          (self.username, self.node_name, local_port, self.node_name, local_port),
      ).fetchone()
    finally:
      conn.close()
    if not row:
      self.append_plain_text(f"\n  Failure: No optical module/link is present on Ethernet port {local_port}.")
      return
    source_node, target_node, source_port, target_port, module_name, fiber_type, length_km, is_up = row
    optical = None
    # Resolve SFP+ specifications from the same registry used by the topology UI.
    from gui.models.link import OpticalLinkItem
    optical = OpticalLinkItem.SFP_PLUS_MODULES.get(module_name)
    if not optical or fiber_type not in ("SMF", "MMF"):
      self.append_plain_text(f"\n  Failure: No DDM-capable SFP+ optical module is present on port {local_port}.")
      return

    if remote:
      if source_node == self.node_name:
        peer_node, peer_port = target_node, target_port
      else:
        peer_node, peer_port = source_node, source_port
      tx_power = optical["tx_min_dbm"] + 1.0
    else:
      peer_node, peer_port = self.node_name, local_port
      tx_power = optical["tx_min_dbm"] + 1.0
    rx_power = tx_power - float(length_km or 0) * optical["fiber_loss_db_km"]
    tx_bias = 11.462 if optical["wavelength_nm"] == 1310 else 7.300
    temperature = 51.0 if optical["wavelength_nm"] == 1310 else 47.2
    voltage = 3.2409 if optical["wavelength_nm"] == 1310 else 3.2926
    output = [f"\n  Command:\n          {cmd}"]
    if remote:
      output.append(f"  Peer optical module: {peer_node} port {peer_port}")
      output.extend([
          f"\nTemperature(C)                       : {temperature:.6f}",
          f"Supply voltage(V)                    : {voltage:.6f}",
          f"TX bias current(mA)                  : {tx_bias:.6f}",
          f"TX power(dBm)                        : {tx_power:.6f}",
          f"RX power(dBm)                        : {rx_power:.6f}",
      ])
    else:
      output.extend([
          f"\nTemperature(C)                       : {temperature:.6f}  [-128.00,  128.00]",
          f"Supply voltage(V)                    : {voltage:.6f}  [   0.00,    6.55]",
          f"TX bias current(mA)                  : {tx_bias:.6f}  [   0.0,   131.0]",
          f"TX power(dBm)                        : {tx_power:.6f}  [-50.000000,   8.200000]",
          f"RX power(dBm)                        : {rx_power:.6f}  [-50.000000,   8.200000]",
      ])
    output.append(f"\n  Module: {module_name}; Fiber: {fiber_type}; Link: {'up' if is_up else 'down'}")
    self.append_plain_text("\n".join(output))

  # ------------------------------------------------------------------
  # ONT auto-find: hanya ONT yang benar-benar terhubung fisik ke OLT
  # ------------------------------------------------------------------
  def _linked_nodes_from_olt(self, olt_port=None):
    """Daftar node yang tercapai dari OLT melalui link yang UP (BFS).

    Bila ``olt_port`` diisi (mis. "0/1/0"), penelusuran hanya dimulai dari
    link yang terpasang pada port OLT tersebut.
    """
    conn = get_connection()
    try:
      links = conn.execute(
          "SELECT source_node, target_node, source_port, target_port"
          " FROM olt_device_links WHERE username = ? AND is_up = 1",
          (self.username,),
      ).fetchall()
    finally:
      conn.close()

    adjacency = {}
    for source, target, source_port, target_port in links:
      adjacency.setdefault(source, []).append((target, source_port))
      adjacency.setdefault(target, []).append((source, target_port))

    starts = [
        neighbor
        for neighbor, port in adjacency.get(self.node_name, [])
        if olt_port is None or port == olt_port
    ]
    visited, queue = set(), list(starts)
    while queue:
      node = queue.pop()
      if node in visited:
        continue
      visited.add(node)
      for neighbor, _port in adjacency.get(node, []):
        if neighbor not in visited and neighbor != self.node_name:
          queue.append(neighbor)
    return visited

  def _allocate_sn_for_node(self, conn, node_name):
    """SN stabil untuk ONT yang terhubung tetapi belum punya baris di DB."""
    used = {
        row[0]
        for row in conn.execute(
            "SELECT sn FROM olt_onts WHERE username = ?", (self.username,)
        )
    }
    pool = [
        row[0]
        for row in conn.execute(
            "SELECT sn FROM ont_sn_pool WHERE is_used = 0 ORDER BY id"
        )
    ]
    free = [sn for sn in pool if sn not in used]
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

  def _autofind_entries(self, port_token):
    """ONT yang terpasang di port GPON ini dan belum di-'ont add'.

    Sumber kebenaran adalah topologi (``olt_device_links``): ONT yang fisik
    terhubung selalu muncul, meskipun belum pernah dibuatkan baris di
    ``olt_onts`` (mis. topologi hasil Load Topology). ONT yang sudah
    diprovisioning (control_flag = active) tidak ditampilkan.
    Tabel cache ``olt_autofind`` ikut disinkronkan agar ``ont confirm``
    konsisten dengan kondisi kabel saat ini.
    """
    token = str(port_token)
    if token.lower() == "all":
      ports = [f"{self.current_gpon_port}/{index}" for index in range(16)]
    else:
      ports = [f"{self.current_gpon_port}/{token}"]

    conn = get_connection()
    entries = []
    try:
      now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
      known_ont_names = {
          row[0]
          for row in conn.execute(
              "SELECT DISTINCT ont_name FROM olt_onts"
              " WHERE username = ? AND ont_name IS NOT NULL",
              (self.username,),
          )
      }
      for full_port in ports:
        linked = self._linked_nodes_from_olt(full_port)
        # Hanya perangkat ONT (nama node "ONT_*" atau sudah terdaftar di DB);
        # splitter/OTB/roset bukan ONT.
        nodes = [
            node
            for node in linked
            if node.upper().startswith("ONT") or node in known_ont_names
        ]
        found = []
        for node in sorted(nodes):
          row = conn.execute(
              "SELECT ont_id, sn, control_flag FROM olt_onts"
              " WHERE username = ? AND ont_name = ?"
              " ORDER BY ont_id DESC LIMIT 1",
              (self.username, node),
          ).fetchone()
          if row and (row[2] or "").lower() == "active":
            continue  # sudah diprovisioning -> tidak masuk autofind
          if row is None:
            sn = self._allocate_sn_for_node(conn, node)
            if sn is None:
              continue
            next_id = conn.execute(
                "SELECT COALESCE(MAX(ont_id), -1) + 1 FROM olt_onts"
                " WHERE username = ? AND port = ?",
                (self.username, full_port),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO olt_onts (username, port, ont_id, sn, control_flag,"
                " run_state, config_state, match_state, description, ont_name,"
                " autofind_state) VALUES"
                " (?, ?, ?, ?, NULL, 'offline', 'initial', 'initial',"
                " 'auto-find', ?, 1)",
                (self.username, full_port, next_id, sn, node),
            )
            row = (next_id, sn, None)
          elif row[0] is not None:
            conn.execute(
                "UPDATE olt_onts SET port = ? WHERE username = ? AND ont_name = ?"
                " AND port <> ?",
                (full_port, self.username, node, full_port),
            )
          found.append((full_port, row[1], node))
        conn.execute(
            "DELETE FROM olt_autofind WHERE username = ? AND port = ?",
            (self.username, full_port),
        )
        conn.executemany(
            "INSERT INTO olt_autofind (username, port, sn, device_type, find_time)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (self.username, full_port, sn, "G-240W-F", now)
                for _port, sn, _node in found
            ],
        )
        entries.extend(found)
      conn.commit()
    finally:
      conn.close()
    return entries

  @staticmethod
  def _expand_port_list(value, maximum=3):
    ports = set()
    for segment in value.split(","):
      if "-" in segment:
        start_text, end_text = segment.split("-", 1)
        start, end = int(start_text), int(end_text)
        if end < start: raise ValueError("Port ranges must be in ascending order.")
        ports.update(range(start, end + 1))
      else:
        ports.add(int(segment))
    if not ports or any(port < 0 or port > maximum for port in ports):
      raise ValueError(f"Port list must contain IDs from 0 to {maximum}.")
    return sorted(ports)

  def _lag_member_status(self, conn, member_port, local_workmode):
    row = conn.execute(
        "SELECT source_node,target_node,source_port,target_port,is_up "
        "FROM olt_device_links WHERE username = ? AND "
        "((source_node = ? AND source_port = ?) OR (target_node = ? AND target_port = ?)) LIMIT 1",
        (self.username, self.node_name, member_port, self.node_name, member_port),
    ).fetchone()
    if not row or not row[4]:
      return "Block", "Offline", "Non-C&D" if local_workmode == "lacp-static" else "None"
    peer_node = row[1] if row[0] == self.node_name else row[0]
    peer_port = row[3] if row[0] == self.node_name else row[2]
    peer = conn.execute(
        "SELECT m.master_port,g.workmode FROM olt_link_aggregation_members m "
        "JOIN olt_link_aggregations g ON g.username=m.username AND g.node_name=m.node_name AND g.master_port=m.master_port "
        "WHERE m.username = ? AND m.node_name = ? AND m.member_port = ?",
        (self.username, peer_node, peer_port),
    ).fetchone()
    if local_workmode == "lacp-static":
      lacp_state = "C&D" if peer and peer[1] == "lacp-static" else "Non-C&D"
      if lacp_state != "C&D": return "Block", "Online", lacp_state
    else:
      lacp_state = "None"
      if peer and peer[1] == "lacp-static": return "Block", "Online", "Non-C&D"
    return "Forwarding", "Online", lacp_state

  def _handle_link_aggregation(self, cmd, parts, lower):
    conn = get_connection(); cursor = conn.cursor()
    try:
      if lower[0] == "link-aggregation":
        if self.cli_state != "CONFIG": raise ValueError("Configure link aggregation in global config mode.")
        tokens, token_lower = parts[1:], lower[1:]
        if token_lower and token_lower[0] == "add-member":
          if len(tokens) < 4: raise ValueError("Use link-aggregation add-member <master-port> <frame/slot> <port-list>.")
          master_port = tokens[1]
          if master_port.count("/") != 2: raise ValueError("Master port must use frame/slot/port format.")
          cursor.execute("SELECT workmode FROM olt_link_aggregations WHERE username = ? AND node_name = ? AND master_port = ?", (self.username, self.node_name, master_port))
          group = cursor.fetchone()
          if not group: raise ValueError(f"LAG master {master_port} does not exist.")
          members = []
          i = 2
          while i < len(tokens):
            if i + 1 >= len(tokens): raise ValueError("Each member board must be followed by a port-list.")
            board = tokens[i]
            if board != "0/2": raise ValueError("MA5801 uplink aggregation members must use board 0/2.")
            members.extend(f"{board}/{port}" for port in self._expand_port_list(tokens[i + 1]))
            i += 2
          if not members: raise ValueError("Specify at least one LAG member port.")
          current = {row[0] for row in cursor.execute("SELECT member_port FROM olt_link_aggregation_members WHERE username = ? AND node_name = ?", (self.username, self.node_name)).fetchall()}
          if any(member in current for member in members): raise ValueError("A port already belongs to a link aggregation group.")
          if master_port not in {row[0] for row in cursor.execute("SELECT member_port FROM olt_link_aggregation_members WHERE username = ? AND node_name = ? AND master_port = ?", (self.username, self.node_name, master_port)).fetchall()}:
            raise ValueError("The specified master port is not a member of its LAG.")
          for member in members:
            cursor.execute("INSERT INTO olt_link_aggregation_members (username,node_name,master_port,board,port_id,member_port) VALUES (?,?,?,?,?,?)", (self.username, self.node_name, master_port, "0/2", int(member.rsplit("/", 1)[1]), member))
          count = cursor.execute("SELECT COUNT(*) FROM olt_link_aggregation_members WHERE username = ? AND node_name = ? AND master_port = ?", (self.username, self.node_name, master_port)).fetchone()[0]
          cursor.execute("UPDATE olt_link_aggregations SET max_link_number = ? WHERE username = ? AND node_name = ? AND master_port = ?", (count, self.username, self.node_name, master_port))
          conn.commit()
          self.append_plain_text(f"\n  {len(members)} port(s) added to LAG {master_port}.")
          return True

        modes = ("ingress", "egress-ingress", "src-dst-ip", "ip-enhance", "mpls-label")
        mode_idx = next((i for i, token in enumerate(token_lower) if token in modes), None)
        if mode_idx is None: raise ValueError("Specify a distribution mode: ingress, egress-ingress, src-dst-ip, ip-enhance, or mpls-label.")
        if mode_idx % 2: raise ValueError("Specify each board and its port-list before the distribution mode.")
        member_ports = []
        for i in range(0, mode_idx, 2):
          board = tokens[i]
          if board != "0/2": raise ValueError("MA5801 uplink aggregation supports board 0/2 only.")
          member_ports.extend(f"{board}/{port}" for port in self._expand_port_list(tokens[i + 1]))
        if not member_ports: raise ValueError("Specify at least one uplink port.")
        if len(set(member_ports)) != len(member_ports): raise ValueError("Duplicate port in link aggregation list.")
        workmode = "manual"
        if "workmode" in token_lower:
          pos = token_lower.index("workmode")
          if pos + 1 >= len(tokens) or token_lower[pos + 1] != "lacp-static": raise ValueError("Supported workmode is lacp-static.")
          workmode = "lacp-static"
        existing = {row[0] for row in cursor.execute("SELECT member_port FROM olt_link_aggregation_members WHERE username = ? AND node_name = ?", (self.username, self.node_name)).fetchall()}
        if any(member in existing for member in member_ports): raise ValueError("A port already belongs to a link aggregation group.")
        master_port = min(member_ports, key=lambda item: int(item.rsplit("/", 1)[1]))
        cursor.execute("INSERT INTO olt_link_aggregations (username,node_name,master_port,mode,workmode,max_link_number,least_link_number) VALUES (?,?,?,?,?,?,1)", (self.username, self.node_name, master_port, token_lower[mode_idx], workmode, len(member_ports)))
        for member in member_ports:
          cursor.execute("INSERT INTO olt_link_aggregation_members (username,node_name,master_port,board,port_id,member_port) VALUES (?,?,?,?,?,?)", (self.username, self.node_name, master_port, "0/2", int(member.rsplit("/", 1)[1]), member))
        conn.commit()
        self.append_plain_text(f"\n  Link aggregation {master_port} created with {len(member_ports)} member port(s), mode {token_lower[mode_idx]}, workmode {workmode}.")
        return True

      if lower[0] == "undo":
        if self.cli_state != "CONFIG": raise ValueError("Run undo link-aggregation in global config mode.")
        if len(parts) != 3 or lower[1] != "link-aggregation": raise ValueError("Use undo link-aggregation <frame/slot/port|all>.")
        if lower[2] == "all":
          masters = [row[0] for row in cursor.execute("SELECT master_port FROM olt_link_aggregations WHERE username = ? AND node_name = ?", (self.username, self.node_name)).fetchall()]
        else:
          port = parts[2]
          group = cursor.execute("SELECT master_port FROM olt_link_aggregation_members WHERE username = ? AND node_name = ? AND member_port = ?", (self.username, self.node_name, port)).fetchone()
          if not group: raise ValueError(f"Port {port} is not in a link aggregation group.")
          masters = [group[0]]
        if not masters: raise ValueError("No link aggregation group exists.")
        conn.close()
        self.pending_confirmation = ("lag-delete", masters, {})
        self.append_plain_text("\n  This operation may interrupt services and remove LACP backup. Continue? (y/n)[n]:")
        return True

      if lower[0] == "display":
        if self.cli_state != "ENABLE": raise ValueError("Run display link-aggregation in privilege mode.")
        if len(parts) == 3 and lower[1:3] == ["link-aggregation", "all"]:
          groups = cursor.execute("SELECT master_port,mode,workmode,max_link_number,least_link_number FROM olt_link_aggregations WHERE username = ? AND node_name = ? ORDER BY master_port", (self.username, self.node_name)).fetchall()
          lines = [f"\n  Command:\n          {cmd}", "  -------------------------------------------------------------------------", "  Master    Link aggregation    Port  Work        Max          Least", "  port      mode                NUM   mode        link number  link number", "  -------------------------------------------------------------------------"]
          for master, mode, workmode, maximum, least in groups:
            count = cursor.execute("SELECT COUNT(*) FROM olt_link_aggregation_members WHERE username = ? AND node_name = ? AND master_port = ?", (self.username, self.node_name, master)).fetchone()[0]
            lines.append(f"  {master.replace('/', ' /'):<11} {mode:<19} {count:<5} {workmode:<11} {maximum:<12} {least}")
          lines.extend(["  -------------------------------------------------------------------------", f"  Total: {len(groups)} link aggregation(s)"])
          self.append_plain_text("\n".join(lines)); return True

        status_query = len(parts) >= 3 and lower[1:3] == ["link-aggregation", "status"]
        if status_query:
          if len(parts) != 4: raise ValueError("Use display link-aggregation status <frame/slot/port>.")
          requested = parts[3]
          group = cursor.execute("SELECT g.master_port,g.mode,g.workmode,g.max_link_number,g.least_link_number FROM olt_link_aggregations g JOIN olt_link_aggregation_members m ON m.username=g.username AND m.node_name=g.node_name AND m.master_port=g.master_port WHERE g.username = ? AND g.node_name = ? AND m.member_port = ?", (self.username, self.node_name, requested)).fetchone()
          if not group: raise ValueError(f"No LAG contains port {requested}.")
          master, mode, workmode, maximum, least = group
          members = cursor.execute("SELECT member_port FROM olt_link_aggregation_members WHERE username = ? AND node_name = ? AND master_port = ? ORDER BY port_id", (self.username, self.node_name, master)).fetchall()
          states = [(member[0], *self._lag_member_status(conn, member[0], workmode)) for member in members]
          forwarding = sum(1 for _port, state, _link, _lacp in states if state == "Forwarding")
          lag_state = "Forwarding" if forwarding >= least else "Block"
          lines = [f"\n  Command:\n          {cmd}", "  -------------------------------------------------------------------------", "  Port       Status       Link status  LACP status  BFD status", "  -------------------------------------------------------------------------"]
          lines.extend(f"  {port:<10} {state:<12} {link:<12} {lacp:<12} None" for port, state, link, lacp in states)
          lines.extend(["  -------------------------------------------------------------------------", f"  LAG status: {lag_state}"])
          self.append_plain_text("\n".join(lines)); return True

        if len(parts) == 3 and lower[1] == "link-aggregation":
          port = parts[2]
          group = cursor.execute("SELECT g.master_port,g.mode,g.workmode,g.max_link_number,g.least_link_number FROM olt_link_aggregations g JOIN olt_link_aggregation_members m ON m.username=g.username AND m.node_name=g.node_name AND m.master_port=g.master_port WHERE g.username = ? AND g.node_name = ? AND m.member_port = ?", (self.username, self.node_name, port)).fetchone()
          if not group: raise ValueError(f"No link aggregation contains port {port}.")
          master, mode, workmode, maximum, least = group
          members = cursor.execute("SELECT board,port_id,member_port FROM olt_link_aggregation_members WHERE username = ? AND node_name = ? AND master_port = ? ORDER BY port_id", (self.username, self.node_name, master)).fetchall()
          lines = [f"\n  Command:\n          {cmd}", f"  link aggregation mode: {mode}", f"  work mode:             {workmode}", f"  max link number:       {maximum}", f"  least link number:     {least}", f"  master port:           {master}", "  hash option:           CRC", "  random seed:           7", "  ---------------------------------------------------------------------", "  F/S                   Port List", "  ---------------------------------------------------------------------"]
          lines.append(f"  0/2                   {','.join(str(row[1]) for row in members)}")
          lines.append("  ---------------------------------------------------------------------")
          self.append_plain_text("\n".join(lines)); return True
        raise ValueError("Use display link-aggregation {all|<frame/slot/port>} or display link-aggregation status <frame/slot/port>.")
      return False
    except (ValueError, IndexError, sqlite3.IntegrityError) as exc:
      self.append_plain_text(f"\n  Failure: {exc}")
      return True
    finally:
      try: conn.close()
      except Exception: pass

  def handle_simplified_triple_play_command(self, cmd, cmd_lower):
    """Handle commands required by the simplified triple-play guide."""
    try:
      parts = shlex.split(cmd)
    except ValueError:
      parts = cmd.split()
    lower = [part.lower() for part in parts]

    if self.pending_confirmation:
      if cmd_lower in ("y", "yes"):
        action, vlan_id, value = self.pending_confirmation
        conn = get_connection()
        cursor = conn.cursor()
        if action == "igmp-mode":
          cursor.execute(
              "UPDATE olt_multicast_vlans SET igmp_mode = ? "
              "WHERE username = ? AND vlan_id = ?",
              (value, self.username, vlan_id),
          )
        elif action == "uplink-mode":
          cursor.execute(
              "UPDATE olt_multicast_vlans SET uplink_port_mode = ? "
              "WHERE username = ? AND vlan_id = ?",
              (value, self.username, vlan_id),
          )
        elif action == "eth-auto-neg":
          board = value["board"]
          enabled = int(value["enabled"])
          for port_id in vlan_id:
            cursor.execute(
                "INSERT INTO olt_eth_port_settings "
                "(username,node_name,board,port_id,auto_neg) VALUES (?,?,?,?,?) "
                "ON CONFLICT(username,node_name,board,port_id) DO UPDATE SET auto_neg=excluded.auto_neg",
                (self.username, self.node_name, board, port_id, enabled),
            )
          conn.commit()
          conn.close()
          self.append_plain_text(f"\n  Auto-negotiation disabled on ETH port(s) {', '.join(map(str, vlan_id))}.")
          self.pending_confirmation = None
          return True
        elif action == "lag-delete":
          for master in vlan_id:
            cursor.execute("DELETE FROM olt_link_aggregation_members WHERE username=? AND node_name=? AND master_port=?", (self.username, self.node_name, master))
            cursor.execute("DELETE FROM olt_link_aggregations WHERE username=? AND node_name=? AND master_port=?", (self.username, self.node_name, master))
          conn.commit()
          conn.close()
          self.append_plain_text(f"\n  {len(vlan_id)} link aggregation group(s) deleted.")
          self.pending_confirmation = None
          return True
        elif action == "database-reset":
          conn.close()
          try:
            reset_user_data(self.username)
          except Exception as exc:
            self.append_plain_text(f"\n  Failure: Unable to reset simulator data: {exc}")
            self.pending_confirmation = None
            return True
          self.pending_confirmation = None
          self.sysname = self.node_name
          main_window = self.window()
          if hasattr(main_window, "reset_simulator_topology"):
            main_window.reset_simulator_topology(self)
          self.append_plain_text("\n  Simulator database and saved topologies were reset to initial data.")
          return True
        elif action in ("vlan-create", "vlan-delete", "vlan-bind-port", "vlan-unbind-port"):
          conn.close()
          try:
            operation = {"vlan-create": "create", "vlan-delete": "delete", "vlan-bind-port": "bind-port", "vlan-unbind-port": "unbind-port"}[action]
            count = self._apply_vlan_operation(operation, vlan_id, value)
            total = len(vlan_id)
            label = {"create": "added", "delete": "deleted", "bind-port": "port VLAN(s) added", "unbind-port": "port VLAN(s) deleted"}[operation]
            self.append_plain_text(f"\n  The total of VLANs having been processed is {total}\n  The total of {label} is {count}")
          except ValueError as exc:
            self.append_plain_text(f"\n  Failure: {exc}")
          self.pending_confirmation = None
          return True
        conn.commit()
        conn.close()
        self.append_plain_text("\n  Command has been executed successfully.")
        self.pending_confirmation = None
        return True
      if cmd_lower in ("n", "no"):
        self.append_plain_text("\n  Operation cancelled.")
        self.pending_confirmation = None
        return True
      self.append_plain_text("\n  Please answer y or n.")
      return True

    if self.pending_command_buffer:
      if cmd_lower.startswith("user-vlan "):
        cmd = self.pending_command_buffer + " " + cmd
        cmd_lower = cmd.lower()
        parts = cmd.split()
        lower = [part.lower() for part in parts]
        self.pending_command_buffer = ""
      else:
        self.append_plain_text("\n  Failure: Finish the pending service-port command with user-vlan.")
        self.pending_command_buffer = ""
        return True

    if lower and lower[0] == "sysname":
      if self.cli_state != "ENABLE":
        self.append_plain_text("\n  Failure: Run sysname in privilege mode.")
        return True
      prompt = " ".join(parts[1:]).strip()
      if not 1 <= len(prompt) <= 50:
        self.append_plain_text("\n  Failure: System name must contain 1-50 characters.")
        return True
      conn = get_connection()
      conn.execute(
          "INSERT OR REPLACE INTO olt_node_settings (username,node_name,sysname) VALUES (?,?,?)",
          (self.username, self.node_name, prompt),
      )
      conn.commit()
      conn.close()
      self.sysname = prompt
      main_window = self.window()
      if hasattr(main_window, "update_device_sysname"):
        main_window.update_device_sysname(self.node_name, prompt)
      self.append_plain_text(f"\n  Command:\n          sysname {prompt}")
      return True

    if lower and lower[0] == "reset":
      if self.cli_state != "ENABLE":
        self.append_plain_text("\n  Failure: Run reset in privilege mode.")
        return True
      if len(parts) != 1:
        self.append_plain_text("\n  Failure: Use reset without parameters.")
        return True
      self.pending_confirmation = ("database-reset", None, None)
      self.append_plain_text(
          "\n  This will delete the current account's OLT configuration and saved topologies, "
          "then restore factory defaults. Continue? (y/n)[n]:"
      )
      return True

    if lower and (lower[0] == "link-aggregation" or lower[:2] == ["undo", "link-aggregation"] or lower[:2] == ["display", "link-aggregation"]):
      return self._handle_link_aggregation(cmd, parts, lower)

    if self.cli_state == "CONFIG" and (lower[:2] == ["mirror", "port"] or lower[:3] == ["undo", "mirror", "port"]):
      result = mirror.handle_command(self.username, self.node_name, "0/2", cmd)
      self.append_plain_text(result or "\n  Failure: Use mirror port src-port <frame/slot/port> dst-port <frame/slot/port> {ingress|egress|all}.")
      return True

    if self.cli_state == "CONFIG" and lower[:3] == ["display", "mirror", "port"]:
      self.append_plain_text(mirror.display(self.username, self.node_name, "0/2"))
      return True

    if self.cli_state == "CONFIG" and lower[:2] == ["interface", "eth"]:
      try:
        if len(parts) != 3 or parts[2].count("/") != 1:
          raise ValueError("Use interface eth <frameid/slotid>.")
        frame, slot = (int(value) for value in parts[2].split("/"))
        if not 0 <= frame <= 7 or not 0 <= slot <= 15:
          raise ValueError("ETH board frame/slot is out of range.")
        self.current_eth_board = f"{frame}/{slot}"
        self.cli_state = "INTERFACE_ETH"
      except ValueError as exc:
        self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if self.cli_state == "INTERFACE_ETH" and lower[:3] == ["display", "mirror", "port"]:
      self.append_plain_text(mirror.display(self.username, self.node_name, self.current_eth_board))
      return True

    if self.cli_state == "INTERFACE_ETH" and lower[:3] == ["display", "port", "ddm-info"]:
      self._display_eth_ddm_info(cmd, parts, lower)
      return True

    if self.cli_state == "INTERFACE_ETH" and (lower[:2] == ["mirror", "port"] or lower[:3] == ["undo", "mirror", "port"]):
      result = mirror.handle_command(self.username, self.node_name, self.current_eth_board, cmd)
      self.append_plain_text(result or "\n  Failure: Use mirror port <src-portid> <dst-portid> {ingress|egress|all}.")
      return True

    if self.cli_state == "INTERFACE_ETH" and lower and lower[0] in ("speed", "auto-neg"):
      try:
        if len(parts) != 3: raise ValueError("Use speed <portid|all> <10|100|1000|2500|10000|25000|100000> or auto-neg <portid|all> <enable|disable>.")
        selector = lower[1]
        if selector == "all":
          port_ids = list(range(4)) if self.current_eth_board.endswith("/2") else list(range(28))
        else:
          port_id = int(selector)
          max_port = 3 if self.current_eth_board.endswith("/2") else 27
          if not 0 <= port_id <= max_port: raise ValueError(f"Port ID must be in range 0-{max_port} on board {self.current_eth_board}.")
          port_ids = [port_id]

        conn = get_connection(); cursor = conn.cursor()
        if lower[0] == "speed":
          try: speed = int(parts[2])
          except ValueError as exc: raise ValueError("Speed must be one of 10, 100, 1000, 2500, 10000, 25000, or 100000 Mbit/s.") from exc
          if speed not in (10, 100, 1000, 2500, 10000, 25000, 100000):
            raise ValueError("Speed must be one of 10, 100, 1000, 2500, 10000, 25000, or 100000 Mbit/s.")
          for port_id in port_ids:
            cursor.execute("SELECT auto_neg FROM olt_eth_port_settings WHERE username = ? AND node_name = ? AND board = ? AND port_id = ?", (self.username, self.node_name, self.current_eth_board, port_id))
            current = cursor.fetchone()
            if (not current or current[0]) and speed != 10000:
              raise ValueError(f"Disable auto-negotiation on port {port_id} before setting {speed} Mbit/s.")
          for port_id in port_ids:
            cursor.execute("INSERT INTO olt_eth_port_settings (username,node_name,board,port_id,speed) VALUES (?,?,?,?,?) ON CONFLICT(username,node_name,board,port_id) DO UPDATE SET speed=excluded.speed", (self.username, self.node_name, self.current_eth_board, port_id, speed))
          conn.commit(); conn.close()
          self.append_plain_text(f"\n  Speed of ETH port(s) {', '.join(map(str, port_ids))} set to {speed} Mbit/s.")
          return True

        switch = lower[2]
        if switch not in ("enable", "disable"): raise ValueError("Use auto-neg <portid|all> {enable|disable}.")
        if switch == "disable":
          conn.close()
          self.pending_confirmation = ("eth-auto-neg", port_ids, {"board": self.current_eth_board, "enabled": False})
          self.append_plain_text("\n  This operation may interrupt services. Ensure both ends use compatible auto-negotiation settings. Continue? (y/n)[n]:")
          return True
        for port_id in port_ids:
          cursor.execute("INSERT INTO olt_eth_port_settings (username,node_name,board,port_id,auto_neg) VALUES (?,?,?,?,1) ON CONFLICT(username,node_name,board,port_id) DO UPDATE SET auto_neg=1", (self.username, self.node_name, self.current_eth_board, port_id))
        conn.commit(); conn.close()
        self.append_plain_text(f"\n  Auto-negotiation enabled on ETH port(s) {', '.join(map(str, port_ids))}.")
        return True
      except (ValueError, IndexError) as exc:
        try: conn.close()
        except (UnboundLocalError, Exception): pass
        self.append_plain_text(f"\n  Failure: {exc}")
        return True

    if self.cli_state == "INTERFACE_ETH" and lower and lower[0] in ("native-vlan", "undo"):
      conn = get_connection(); cursor = conn.cursor()
      try:
        removing = lower[0] == "undo"
        args = parts[1:] if removing else parts
        args_lower = [part.lower() for part in args]
        if (removing and (len(args) < 2 or args_lower[0] != "native-vlan")) or (not removing and args_lower[0] != "native-vlan"):
          return False
        offset = 1
        if len(args) < offset + 3 or args_lower[offset + 1] != "vlan" and not removing:
          if not removing: raise ValueError("Use native-vlan {<portid>|all} vlan <VLAN-ID> [dedicated-net-id <1-8>].")
        selector = args[offset].lower()
        if selector == "all":
          if removing:
            cursor.execute("DELETE FROM olt_eth_native_vlans WHERE username = ? AND node_name = ? AND board = ?", (self.username, self.node_name, self.current_eth_board))
            count = cursor.rowcount
            conn.commit()
            self.append_plain_text(f"\n  Native VLAN removed from {count} ETH port(s).")
            return True
          if len(args) < offset + 3 or args_lower[offset + 1] != "vlan":
            raise ValueError("Use native-vlan all vlan <VLAN-ID> [dedicated-net-id <1-8>].")
          vlan_id = int(args[offset + 2])
          port_ids = None
        else:
          port_id = int(selector)
          max_port = 3 if self.current_eth_board.endswith("/2") else 27
          if not 0 <= port_id <= max_port: raise ValueError(f"Port ID must be in range 0-{max_port} on board {self.current_eth_board}.")
          if removing:
            cursor.execute("DELETE FROM olt_eth_native_vlans WHERE username = ? AND node_name = ? AND board = ? AND port_id = ?", (self.username, self.node_name, self.current_eth_board, port_id))
            if not cursor.rowcount: raise ValueError("Native VLAN is not configured on this ETH port.")
            conn.commit()
            self.append_plain_text(f"\n  Native VLAN removed from port {port_id}.")
            return True
          if args_lower[offset + 1] != "vlan": raise ValueError("Use native-vlan <portid> vlan <VLAN-ID>.")
          vlan_id = int(args[offset + 2])
          port_ids = [port_id]

        if not 1 <= vlan_id <= 4093: raise ValueError("VLAN ID must be in range 1-4093.")
        dedicated_net_id = None
        if "dedicated-net-id" in args_lower:
          dedicated_net_id = int(args[args_lower.index("dedicated-net-id") + 1])
          if not 1 <= dedicated_net_id <= 8: raise ValueError("dedicated-net-id must be in range 1-8.")
        cursor.execute("SELECT dedicated_net_id FROM olt_vlan WHERE username = ? AND vlan_id = ?", (self.username, vlan_id))
        vlan_row = cursor.fetchone()
        if not vlan_row: raise ValueError(f"VLAN {vlan_id} does not exist.")
        if dedicated_net_id is not None and vlan_row[0] not in (None, 0, dedicated_net_id):
          raise ValueError(f"VLAN {vlan_id} belongs to dedicated network {vlan_row[0]}.")
        board_prefix = self.current_eth_board + "/"
        cursor.execute("SELECT port FROM olt_node_vlan_ports WHERE username = ? AND node_name = ? AND vlan_id = ? AND port LIKE ? ORDER BY port", (self.username, self.node_name, vlan_id, board_prefix + "%"))
        members = {int(row[0].rsplit("/", 1)[1]) for row in cursor.fetchall()}
        # VLAN 1 adalah VLAN default: semua port otomatis menjadi anggotanya.
        default_vlan = vlan_id == 1
        max_port = 3 if self.current_eth_board.endswith("/2") else 27
        if port_ids is None:
          if not members and not default_vlan:
            raise ValueError(f"No ETH ports on board {self.current_eth_board} are members of VLAN {vlan_id}.")
          port_ids = sorted(members) if members else list(range(0, max_port + 1))
        elif port_ids[0] not in members and not default_vlan:
          raise ValueError(f"Port {self.current_eth_board}/{port_ids[0]} must be added to VLAN {vlan_id} first.")
        for port_id in port_ids:
          cursor.execute("INSERT OR REPLACE INTO olt_eth_native_vlans (username,node_name,board,port_id,vlan_id,dedicated_net_id) VALUES (?,?,?,?,?,?)", (self.username, self.node_name, self.current_eth_board, port_id, vlan_id, dedicated_net_id))
        conn.commit()
        self.append_plain_text(f"\n  Command:\n          {cmd}")
        return True
      except (ValueError, IndexError) as exc:
        self.append_plain_text(f"\n  Failure: {exc}")
        return True
      finally:
        conn.close()

    if lower and lower[0] == "ping":
      if self.cli_state != "ENABLE":
        self.append_plain_text("\n  Failure: Run ping in privilege mode.")
      else:
        self._execute_ping(cmd)
      return True

    if lower and lower[0] == "board":
      if self.cli_state not in ("ENABLE", "CONFIG"):
        self.append_plain_text("\n  Failure: Run board confirm in privilege or global config mode.")
        return True
      if len(parts) != 3 or lower[1] != "confirm" or parts[2] != "0":
        self.append_plain_text("\n  Failure: Use board confirm 0.")
        return True
      conn = get_connection()
      cursor = conn.cursor()
      cursor.execute(
          "UPDATE olt_boards SET status='Normal', online_offline='Online' "
          "WHERE username=? AND lower(status)='config'",
          (self.username,),
      )
      confirmed = cursor.rowcount
      conn.commit()
      conn.close()
      self.append_plain_text(
          f"\n  Command:\n          {cmd}\n  {confirmed} board(s) in frame 0 confirmed."
      )
      return True

    if lower[:2] == ["display", "board"]:
      if self.cli_state not in ("ENABLE", "CONFIG"):
        self.append_plain_text("\n  Failure: Run display board in privilege or global config mode.")
        return True
      if len(parts) > 3:
        self.append_plain_text("\n  Failure: Use display board [<slot-id>|all].")
        return True
      frame_id = None
      if len(parts) == 3 and lower[2] != "all":
        try:
          frame_id = int(parts[2])
        except ValueError:
          self.append_plain_text("\n  Failure: Frame ID must be an integer or all.")
          return True
        if frame_id != 0:
          self.append_plain_text("\n  Failure: This simulator supports frame 0 only.")
          return True
      conn = get_connection()
      query = (
          "SELECT slot_id,board_name,status,subtype0,subtype1,online_offline "
          "FROM olt_boards WHERE username=?"
      )
      params = [self.username]
      query += " ORDER BY slot_id"
      rows = conn.execute(query, params).fetchall()
      conn.close()
      output = [
          f"\n  Command:\n          {cmd}",
          "  SlotID  BoardName   Status          SubType0  SubType1  Online/Offline",
          "  ---------------------------------------------------------------------",
      ]
      for slot, name, status, subtype0, subtype1, online in rows:
        online_text = online or ("Online" if status and "normal" in status.lower() else "Offline")
        output.append(
            f"  {slot:<7} {name or '-':<11} {status or '-':<15} "
            f"{subtype0 or '-':<8} {subtype1 or '-':<8} {online_text}"
        )
      output.extend([
          "  ---------------------------------------------------------------------",
          f"  Total: {len(rows)}",
      ])
      if not rows:
        output.insert(1, "  Failure: No boards are installed in frame 0.")
      self.append_plain_text("\n".join(output))
      return True

    dba_display = cmd_lower.startswith("display dba-profile")
    dba_config = cmd_lower.startswith("dba-profile ")
    if dba_display or dba_config:
      mode = {
          "ENABLE": "privileged",
          "INTERFACE_GPON": "interface",
          "LINEPROFILE": "lineprofile",
          "CONFIG": "config",
      }.get(self.cli_state, self.cli_state.lower())
      try:
        if dba_config and cmd_lower.startswith("dba-profile modify"):
          tokens = cmd.split()
          if len(tokens) <= 4:
            self._run_dba_modify_wizard(cmd, mode)
            return True
        if dba_config and cmd_lower.startswith("dba-profile add") and needs_multiburst_confirmation(cmd):
          answer, accepted = QInputDialog.getItem(
              self, "Confirm DBA Profile",
              "Single-frame multiburst may prevent lower-capability ONTs from going online. Continue?",
              ["no", "yes"], 0, False,
          )
          if not accepted or answer != "yes":
            self.append_plain_text("\n  Operation cancelled.")
            return True
        self.append_plain_text(execute_dba_command(self.username, cmd, mode))
      except DBACommandError as exc:
        self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if cmd_lower.startswith("display port vlan"):
      if self.cli_state not in ("ENABLE", "CONFIG", "INTERFACE_ETH"):
        self.append_plain_text("\n  Failure: Run display port vlan in privilege mode.")
      else:
        self.append_plain_text(
            vlan_info.display_port_vlan(self.username, self.node_name, cmd)
        )
      return True

    if cmd_lower.startswith("display vlan"):
      if self.cli_state not in ("ENABLE", "CONFIG"):
        self.append_plain_text("\n  Failure: Run display vlan in privilege or global config mode.")
      else:
        self.append_plain_text(
            vlan_info.display_vlan(self.username, cmd, self.node_name)
        )
      return True

    if self.cli_state == "ENABLE" and cmd_lower.startswith("undo vlan desc"):
      try:
        if len(parts) >= 5 and lower[3] == "bydesc":
          description = " ".join(parts[4:])
          cursor_field, target = "description", description
        else:
          vlan_id = int(parts[3]); cursor_field, target = "vlan_id", vlan_id
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute(f"UPDATE olt_vlan SET description = '' WHERE username = ? AND {cursor_field} = ?", (self.username, target))
        conn.commit(); conn.close()
        if cursor.rowcount == 0: self.append_plain_text("\n  Failure: VLAN description was not found.")
        else: self.append_plain_text("\n  VLAN description removed.")
      except (ValueError, IndexError):
        self.append_plain_text("\n  Failure: Use undo vlan desc {<ID>|bydesc <description>}.")
      return True

    # VLAN provisioning and display commands.
    if self.cli_state in ("CONFIG", "ENABLE") and cmd_lower.startswith("vlan "):
      try:
        if lower[1] == "desc":
          if self.cli_state != "ENABLE": raise ValueError("Use vlan desc from privilege mode.")
          desc_idx = lower.index("description")
          vlan_id = int(parts[2]); description = " ".join(parts[desc_idx + 1:])
          if not 1 <= vlan_id <= 4093 or not 1 <= len(description) <= 32 or "?" in description: raise ValueError("Invalid VLAN description.")
          conn = get_connection(); cursor = conn.cursor()
          cursor.execute("SELECT COUNT(*) FROM olt_vlan WHERE username = ? AND description = ? AND vlan_id <> ?", (self.username, description, vlan_id))
          if cursor.fetchone()[0]: conn.close(); raise ValueError("VLAN description must be unique.")
          cursor.execute("UPDATE olt_vlan SET description = ? WHERE username = ? AND vlan_id = ?", (description, self.username, vlan_id))
          if cursor.rowcount == 0: conn.close(); raise ValueError("VLAN does not exist.")
          conn.commit(); conn.close(); self.append_plain_text("\n  VLAN description configured."); return True
        if self.cli_state != "CONFIG": raise ValueError("VLAN configuration requires global config mode.")
        if lower[1] == "attrib":
          selector = lower[2]
          if selector in ("name", "bydesc"):
            value_idx = 3
            attr_index = next((idx for idx in range(value_idx, len(lower)) if lower[idx] in ("common", "q-in-q", "stacking")), len(parts))
            selector_value = " ".join(parts[value_idx:attr_index])
            vlan_ids = None
            attr = next((part.lower() for part in parts[value_idx:] if part.lower() in ("common", "q-in-q", "stacking")), None)
          else:
            vlan_ids = parse_vlan_ids(parts[2], 2, 4093)
            selector_value = None
            attr = next((part.lower() for part in parts[3:] if part.lower() in ("common", "q-in-q", "stacking")), None)
          if attr is None: raise ValueError("Specify common, q-in-q, or stacking.")
          conn = get_connection(); cursor = conn.cursor()
          if vlan_ids is None:
            field = "vlan_name" if selector == "name" else "description"
            cursor.execute(f"SELECT vlan_id FROM olt_vlan WHERE username = ? AND {field} = ?", (self.username, selector_value))
            vlan_ids = [row[0] for row in cursor.fetchall()]
            if not vlan_ids: conn.close(); raise ValueError("No VLAN matches the specified name or description.")
          # Validate every target before applying any update, so batch commands are atomic.
          for vlan_id in vlan_ids:
            cursor.execute("SELECT vlan_type, ip_address FROM olt_vlan WHERE username = ? AND vlan_id = ?", (self.username, vlan_id))
            current = cursor.fetchone()
            if not current: raise ValueError(f"VLAN {vlan_id} does not exist.")
            vlan_type, ip_address = (current[0] or "").lower(), current[1]
            if vlan_id == 1: raise ValueError("The default VLAN 1 attribute cannot be changed.")
            if ip_address:
              raise ValueError(f"VLAN {vlan_id} has a Layer 3 interface and its attribute cannot be changed.")
            cursor.execute("SELECT COUNT(*) FROM olt_node_vlanifs WHERE username = ? AND vlan_id = ?", (self.username, vlan_id))
            if cursor.fetchone()[0]: raise ValueError(f"VLAN {vlan_id} has a VLANIF and its attribute cannot be changed.")
            cursor.execute("SELECT COUNT(*) FROM olt_service_ports WHERE username = ? AND vlan = ?", (self.username, vlan_id))
            if cursor.fetchone()[0]: raise ValueError(f"VLAN {vlan_id} contains service ports and its attribute cannot be changed.")
            if attr == "stacking" and vlan_type not in ("smart", "mux"):
              raise ValueError(f"VLAN {vlan_id}: stacking is supported only on smart or MUX VLANs.")
            if attr == "q-in-q" and vlan_type not in ("standard", "smart", "mux"):
              raise ValueError(f"VLAN {vlan_id}: QinQ is not supported for this VLAN type.")
          cursor.executemany("UPDATE olt_vlan SET vlan_attr = ? WHERE username = ? AND vlan_id = ?", [(attr, self.username, vlan_id) for vlan_id in vlan_ids])
          conn.commit(); conn.close(); self.append_plain_text(f"\n  VLAN attribute set to {attr} for {len(vlan_ids)} VLAN(s).")
          return True
        if lower[1] == "type":
          vlan_id = int(parts[2]); vlan_type = parts[3].lower()
          if not 2 <= vlan_id <= 4093 or vlan_type not in ("mux", "standard", "smart"): raise ValueError("Use vlan type <2-4093> {mux|standard|smart}.")
          conn = get_connection(); cursor = conn.cursor()
          cursor.execute("SELECT COUNT(*) FROM olt_service_ports WHERE username = ? AND vlan = ?", (self.username, vlan_id))
          if cursor.fetchone()[0]: conn.close(); raise ValueError("VLAN type cannot change while service ports are bound.")
          cursor.execute("UPDATE olt_vlan SET vlan_type = ? WHERE username = ? AND vlan_id = ?", (vlan_type, self.username, vlan_id))
          if cursor.rowcount == 0: conn.close(); raise ValueError("VLAN does not exist.")
          conn.commit(); conn.close(); self.append_plain_text(f"\n  VLAN {vlan_id} type changed to {vlan_type}.")
          return True
        vlan_ids = parse_vlan_ids(parts[1], 2, 4093)
        if "to" in lower:
          end_index = lower.index("to") + 1
          vlan_ids = parse_vlan_ids(f"{parts[1]}-{parts[end_index]}", 2, 4093)
        vlan_type = next((part.lower() for part in parts[2:] if part.lower() in ("mux", "smart", "standard", "super")), "standard")
        dnet = int(parts[lower.index("dedicated-net-id") + 1]) if "dedicated-net-id" in lower else 0
        if not 1 <= dnet <= 8 and dnet != 0: raise ValueError("dedicated-net-id must be 1-8.")
        options = {"type": vlan_type, "dnet": dnet}
        if len(vlan_ids) > 1:
          self.pending_confirmation = ("vlan-create", vlan_ids, options)
          self.append_plain_text("\n  It will take several minutes to add VLANs. Are you sure to add VLANs? (y/n)[n]:")
        else:
          count = self._apply_vlan_operation("create", vlan_ids, options)
          self.append_plain_text(f"\n  VLAN {vlan_ids[0]} {vlan_type} created successfully." if count else f"\n  VLAN {vlan_ids[0]} already exists.")
      except (ValueError, IndexError) as exc:
        self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if self.cli_state == "CONFIG" and cmd_lower.startswith("undo vlan attrib"):
      try:
        conn = get_connection(); cursor = conn.cursor()
        selector = lower[3]
        if selector in ("name", "bydesc"):
          selector_value = " ".join(parts[4:])
          field = "vlan_name" if selector == "name" else "description"
          cursor.execute(f"UPDATE olt_vlan SET vlan_attr = 'common' WHERE username = ? AND {field} = ?", (self.username, selector_value))
          changed = cursor.rowcount
        else:
          vlan_ids = parse_vlan_ids(parts[3], 2, 4093)
          cursor.executemany("UPDATE olt_vlan SET vlan_attr = 'common' WHERE username = ? AND vlan_id = ?", [(self.username, vlan_id) for vlan_id in vlan_ids])
          changed = cursor.rowcount
        conn.commit(); conn.close(); self.append_plain_text(f"\n  VLAN attribute reset for {changed} VLAN(s).")
      except (ValueError, IndexError) as exc:
        self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if self.cli_state == "CONFIG" and cmd_lower.startswith("undo vlan "):
      try:
        vlan_ids = parse_vlan_ids(parts[2], 2, 4093)
        if "to" in lower:
          vlan_ids = parse_vlan_ids(f"{parts[2]}-{parts[lower.index('to') + 1]}", 2, 4093)
        if len(vlan_ids) > 1:
          self.pending_confirmation = ("vlan-delete", vlan_ids, {})
          self.append_plain_text("\n  It will take several minutes to delete VLANs. Are you sure to delete VLANs? (y/n)[n]:")
        else:
          self._apply_vlan_operation("delete", vlan_ids, {})
          self.append_plain_text(f"\n  VLAN {vlan_ids[0]} deleted successfully.")
      except (ValueError, IndexError) as exc:
        self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if self.cli_state == "CONFIG" and cmd_lower.startswith("undo port vlan "):
      try:
        vlan_ids = parse_vlan_ids(parts[3], 1, 4093)
        port_arg = 4
        if len(parts) > 5 and lower[4] == "to":
          vlan_ids = parse_vlan_ids(f"{parts[3]}-{parts[5]}", 1, 4093)
          port_arg = 6
        if len(parts) <= port_arg + 1 or "/" not in parts[port_arg]: raise ValueError("Use undo port vlan <VLAN-list> <frame/slot> <port-list>.")
        ports = [f"{parts[port_arg]}/{port}" for port in parse_vlan_ids(parts[port_arg + 1], 0, 255)]
        options = {"ports": ports}
        if len(vlan_ids) > 1:
          self.pending_confirmation = ("vlan-unbind-port", vlan_ids, options)
          self.append_plain_text("\n  Warning: The operation may interrupt services. Are you sure to delete standard port(s)? (y/n)[n]:")
        else:
          self._apply_vlan_operation("unbind-port", vlan_ids, options)
          self.append_plain_text(f"\n  VLAN uplink binding removed from {', '.join(ports)}.")
      except (ValueError, IndexError) as exc:
        self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if self.cli_state == "CONFIG" and cmd_lower.startswith("port vlan "):
      try:
        vlan_ids = parse_vlan_ids(parts[2], 1, 4093)
        port_arg = 3
        if len(parts) > 4 and lower[3] == "to":
          vlan_ids = parse_vlan_ids(f"{parts[2]}-{parts[4]}", 1, 4093)
          port_arg = 5
        if len(parts) <= port_arg + 1 or "/" not in parts[port_arg]: raise ValueError("Use port vlan <VLAN-list> <frame/slot> <port-list>.")
        ports = []
        for port_id in parse_vlan_ids(parts[port_arg + 1], 0, 255):
          ports.append(f"{parts[port_arg]}/{port_id}")
        options = {"ports": ports}
        if len(vlan_ids) > 1:
          self.pending_confirmation = ("vlan-bind-port", vlan_ids, options)
          self.append_plain_text("\n  It will take several minutes to add ports. Are you sure to add standard port(s)? (y/n)[n]:")
        else:
          count = self._apply_vlan_operation("bind-port", vlan_ids, options)
          self.append_plain_text(f"\n  VLAN uplink binding configured on {', '.join(ports)} ({count} VLAN).")
      except (ValueError, IndexError) as exc:
        self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if cmd_lower.startswith(("traffic table ip", "undo traffic table ip", "display traffic table")):
      if (cmd_lower.startswith("traffic table ip") or cmd_lower.startswith("undo traffic table ip")) and self.cli_state != "CONFIG":
        self.append_plain_text("\n  Failure: Configure traffic tables in global config mode.")
      elif cmd_lower.startswith("display traffic table") and self.cli_state != "ENABLE":
        self.append_plain_text("\n  Failure: Run display traffic table ip in privilege mode.")
      else:
        try: self._handle_traffic_table_command(cmd, parts, lower)
        except (ValueError, IndexError, TypeError) as exc: self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if self.cli_state == "CONFIG" and cmd_lower == "arp proxy enable":
      conn = get_connection()
      cursor = conn.cursor()
      cursor.execute(
          "INSERT OR REPLACE INTO olt_system_config (username, key, value) "
          "VALUES (?, 'arp_proxy', '1')",
          (self.username,),
      )
      conn.commit()
      conn.close()
      self.append_plain_text("\n  ARP proxy enabled globally.")
      return True

    if self.cli_state == "CONFIG" and cmd_lower.startswith("interface vlanif"):
      try:
        token = parts[1].lower() if len(parts) > 1 and parts[1].lower().startswith("vlanif") and parts[1].lower() != "vlanif" else parts[2].lower() if len(parts) > 2 else ""
        vlan_id = int(token.replace("vlanif", ""))
        if not 1 <= vlan_id <= 4093: raise ValueError("VLANIF ID must be 1-4093.")
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("SELECT vlan_attr, dedicated_net_id FROM olt_vlan WHERE username = ? AND vlan_id = ?", (self.username, vlan_id))
        vlan = cursor.fetchone()
        if not vlan: conn.close(); raise ValueError(f"VLAN {vlan_id} must exist before creating its VLANIF.")
        if vlan[0].lower() == "q-in-q": conn.close(); raise ValueError("A QinQ VLAN cannot have a VLANIF.")
        cursor.execute("INSERT OR IGNORE INTO olt_node_vlanifs (username,node_name,vlan_id) VALUES (?,?,?)", (self.username, self.node_name, vlan_id))
        conn.commit(); conn.close()
        self.current_vlanif_id = vlan_id
        self.cli_state = "VLANIF"
      except (ValueError, IndexError) as exc:
        self.append_plain_text(f"\n  Failure: {exc or 'Use interface vlanif <VLAN-ID>.'}")
      return True

    if self.cli_state == "CONFIG" and cmd_lower.startswith("undo interface vlanif"):
      try:
        token = parts[3] if len(parts) > 3 else parts[2]
        vlan_id = int(token.lower().replace("vlanif", ""))
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("DELETE FROM olt_node_vlanifs WHERE username = ? AND node_name = ? AND vlan_id = ?", (self.username, self.node_name, vlan_id))
        if cursor.rowcount == 0: conn.close(); raise ValueError("VLANIF interface does not exist.")
        cursor.execute("DELETE FROM olt_node_vlanif_addresses WHERE username = ? AND node_name = ? AND vlan_id = ?", (self.username, self.node_name, vlan_id))
        conn.commit(); conn.close()
        self.append_plain_text(f"\n  VLANIF {vlan_id} deleted.")
      except (ValueError, IndexError) as exc:
        self.append_plain_text(f"\n  Failure: {exc or 'Use undo interface vlanif <VLAN-ID>.'}")
      return True

    if self.cli_state == "VLANIF" and cmd_lower.startswith("ip address"):
      try: self._configure_vlanif_ip(cmd)
      except ValueError as exc: self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if self.cli_state == "VLANIF" and cmd_lower.startswith("undo ip address"):
      try: self._configure_vlanif_ip(cmd, remove=True)
      except ValueError as exc: self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if self.cli_state == "CONFIG" and cmd_lower.startswith("ip route-static"):
      try: self._configure_static_route(cmd)
      except ValueError as exc: self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if self.cli_state == "CONFIG" and cmd_lower.startswith("undo ip route-static"):
      try: self._configure_static_route(cmd, remove=True)
      except ValueError as exc: self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if lower[:3] == ["display", "interface", "vlanif"]:
      self._display_interface_vlanif(cmd, parts)
      return True

    if cmd_lower.startswith("display ip route-static"):
      if self.cli_state != "ENABLE":
        self.append_plain_text("\n  Failure: Run display ip route-static in privilege mode.")
      else:
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("SELECT dest,mask,gateway,outbound_interface,preference,tag,cost,permanent,description FROM olt_node_routes WHERE username = ? AND node_name = ? ORDER BY dest,mask,gateway", (self.username, self.node_name))
        rows = cursor.fetchall(); conn.close()
        lines = [f"\n  Command:\n          {cmd}", "  Destination       Mask              Next-hop          Interface       Preference Tag Cost Permanent Description", "  ----------------------------------------------------------------------------------------------------------------"]
        lines.extend(f"  {row[0]:<17} {row[1]:<17} {row[2] or '-':<17} {row[3] or '-':<15} {row[4]:<10} {row[5]:<3} {row[6]:<4} {bool(row[7])!s:<9} {row[8]}" for row in rows)
        lines.extend(["  -----------------------------------------------------------------------------------------------", f"  Total: {len(rows)}"])
        self.append_plain_text("\n".join(lines))
      return True

    if cmd_lower in ("display ip interface brief", "display ip interface"):
      if self.cli_state != "ENABLE":
        self.append_plain_text("\n  Failure: Run display ip interface brief in privilege mode.")
      else:
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute(
            "SELECT vlan_id FROM olt_node_vlanifs "
            "WHERE username = ? AND node_name = ? ORDER BY vlan_id",
            (self.username, self.node_name),
        )
        vlan_ids = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            "SELECT vlan_id,address,mask,secondary FROM olt_node_vlanif_addresses "
            "WHERE username = ? AND node_name = ? ORDER BY vlan_id,secondary,address",
            (self.username, self.node_name),
        )
        address_rows = cursor.fetchall()
        addresses = {}
        for vlan_id, address, mask, secondary in address_rows:
          addresses.setdefault(vlan_id, []).append((address, mask, secondary))
        cursor.execute(
            "SELECT source_node,target_node,source_port,target_port,is_up "
            "FROM olt_device_links WHERE username = ? AND is_up = 1",
            (self.username,),
        )
        links = cursor.fetchall()
        cursor.execute(
            "SELECT vlan_id,port FROM olt_node_vlan_ports "
            "WHERE username = ? AND node_name = ?",
            (self.username, self.node_name),
        )
        vlan_port_rows = cursor.fetchall()
        vlan_ports = {}
        for vlan_id, port in vlan_port_rows:
          vlan_ports.setdefault(vlan_id, set()).add(port)
        conn.close()

        lines = [
            f"\n  Command:\n          {cmd}",
            "  Interface             IP Address/Mask      Physical   Protocol",
            "  ----------------------------------------------------------------",
        ]
        for vlan_id in vlan_ids:
          allowed_ports = vlan_ports.get(vlan_id, set())
          physical_up = False
          for source_node, target_node, source_port, target_port, _is_up in links:
            if source_node == self.node_name:
              local_port = source_port
            elif target_node == self.node_name:
              local_port = target_port
            else:
              continue
            if allowed_ports and local_port not in allowed_ports:
              continue
            physical_up = True
            break
          configured = addresses.get(vlan_id, [])
          primary = next((row for row in configured if not row[2]), None)
          ip_mask = f"{primary[0]}/{ipaddress.IPv4Network(f'0.0.0.0/{primary[1]}').prefixlen}" if primary else "unassigned"
          physical = "up" if physical_up else "*down"
          protocol = "up" if physical_up and primary else "down"
          lines.append(f"  {'Vlanif' + str(vlan_id):<22} {ip_mask:<20} {physical:<10} {protocol}")
        if not vlan_ids:
          lines.append("  (No VLANIF interfaces configured)")
        self.append_plain_text("\n".join(lines))
      return True

    if self.cli_state == "VLANIF" and cmd_lower == "arp proxy enable":
      conn = get_connection()
      cursor = conn.cursor()
      cursor.execute(
          "INSERT OR REPLACE INTO olt_vlanif_settings (username, vlan_id, arp_proxy) "
          "VALUES (?, ?, 1)",
          (self.username, self.current_vlanif_id),
      )
      conn.commit()
      conn.close()
      self.append_plain_text(f"\n  ARP proxy enabled on Vlanif {self.current_vlanif_id}.")
      return True

    if self.cli_state == "CONFIG" and cmd_lower.startswith("multicast-vlan "):
      try:
        vlan_id = int(parts[1])
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO olt_multicast_vlans (username, vlan_id) VALUES (?, ?)",
            (self.username, vlan_id),
        )
        conn.commit()
        conn.close()
        self.current_mcast_vlan_id = vlan_id
        self.cli_state = "MVLAN"
      except (ValueError, IndexError):
        self.append_plain_text("\n  Failure: Use multicast-vlan <VLAN-ID>.")
      return True

    # ONT management in GPON interface mode.
    if self.cli_state == "INTERFACE_GPON" and cmd_lower.startswith("ont add "):
      try:
        port_id = parts[2]
        ont_id = int(parts[3])
        auth_idx = next(i for i, token in enumerate(lower) if token in ("password-auth", "sn-auth"))
        auth_mode = lower[auth_idx]
        auth_value = parts[auth_idx + 1]
        full_port = f"{self.current_gpon_port}/{port_id}"
        serial = auth_value if auth_mode == "sn-auth" else (
            f"OFFLINE-{self.username}-{full_port.replace('/', '-')}-{ont_id}"
        )
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM olt_onts WHERE username = ? AND port = ? AND ont_id = ?",
            (self.username, full_port, ont_id),
        )
        if cursor.fetchone():
          conn.close()
          self.append_plain_text("\n  Failure: ONT ID already exists on this GPON port.")
          return True
        previous = cursor.execute(
            "SELECT ont_name, description FROM olt_onts"
            " WHERE username = ? AND sn = ?",
            (self.username, serial),
        ).fetchone()
        ont_name = previous[0] if previous else None
        description = previous[1] if previous else None

        line_profile_id = 0
        srv_profile_id = 0
        index = 4
        while index < len(parts):
          key = lower[index]
          if key == "ont-lineprofile-id" and index + 1 < len(parts):
            line_profile_id = int(parts[index + 1]); index += 2
          elif key == "ont-srvprofile-id" and index + 1 < len(parts):
            srv_profile_id = int(parts[index + 1]); index += 2
          elif key == "ont-lineprofile-name" and index + 1 < len(parts):
            row = cursor.execute(
                "SELECT profile_id FROM olt_line_profiles"
                " WHERE username = ? AND profile_name = ?",
                (self.username, parts[index + 1].strip('"')),
            ).fetchone()
            line_profile_id = row[0] if row else 0
            index += 2
          elif key == "ont-srvprofile-name" and index + 1 < len(parts):
            row = cursor.execute(
                "SELECT profile_id FROM olt_service_profiles"
                " WHERE username = ? AND profile_name = ?",
                (self.username, parts[index + 1].strip('"')),
            ).fetchone()
            srv_profile_id = row[0] if row else 0
            index += 2
          elif key == "desc" and index + 1 < len(parts):
            description = " ".join(parts[index + 1:]).strip('"')
            break
          else:
            index += 1

        # Baris auto-find untuk SN yang sama digantikan oleh baris aktif ini,
        # agar ONT tidak muncul lagi di daftar autofind.
        cursor.execute(
            "DELETE FROM olt_onts WHERE username = ? AND sn = ?",
            (self.username, serial),
        )
        cursor.execute(
            "INSERT INTO olt_onts "
            "(username, port, ont_id, sn, control_flag, run_state, config_state, match_state, "
            "line_profile_id, service_profile_id, auth_mode, auth_value, alarm_profile_id, "
            "ont_name, description) "
            "VALUES (?, ?, ?, ?, 'active', 'offline', 'normal', 'match', ?, ?, ?, ?, 1, ?, ?)",
            (
                self.username,
                full_port,
                ont_id,
                serial,
                line_profile_id,
                srv_profile_id,
                auth_mode,
                auth_value,
                ont_name,
                description,
            ),
        )
        conn.commit()
        conn.close()
        self.append_plain_text(
            f"\n  ONT {ont_id} added on {full_port} using {auth_mode}."
        )
      except (ValueError, IndexError, StopIteration):
        self.append_plain_text("\n  Failure: Invalid ont add command syntax.")
      return True

    if self.cli_state == "INTERFACE_GPON" and len(lower) >= 4 and lower[0] == "port" and "ont-auto-find" in lower:
      port_id = parts[1]
      full_port = f"{self.current_gpon_port}/{port_id}"
      if "enable" in lower:
        self.autofind_enabled_ports.add(full_port)
        self.append_plain_text(f"\n  ONT auto-find enabled on {full_port}.")
      elif "disable" in lower:
        self.autofind_enabled_ports.discard(full_port)
        self.append_plain_text(f"\n  ONT auto-find disabled on {full_port}.")
      return True

    if self.cli_state == "INTERFACE_GPON" and cmd_lower.startswith("display ont autofind"):
      try:
        port_id = parts[3]
        full_port = f"{self.current_gpon_port}/{port_id}"
        if port_id.lower() != "all" and full_port not in self.autofind_enabled_ports:
          self.append_plain_text("\n  Failure: Enable ont-auto-find on this port first.")
          return True
        entries = self._autofind_entries(port_id)
        lines = [f"\n  Command:\n          {cmd}", "  -------------------------------------------------------------------"]
        find_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for n, (entry_port, sn, _node) in enumerate(entries, 1):
          lines.extend([
              f"  Number              : {n}",
              f"  F/S/P               : {entry_port}",
              f"  Ont SN              : {sn}",
              "  VendorID            : HWTC",
              "  Ont Version         : 120A0000",
              "  Ont SoftwareVersion : V1R001C01",
              "  Ont EquipmentID     : G-240W-F",
              f"  Ont autofind time   : {find_time}",
              "  -------------------------------------------------------------------",
          ])
        if not entries:
          lines.append("  Failure: The automatically found ONTs do not exist.")
        self.append_plain_text("\n".join(lines))
      except IndexError:
        self.append_plain_text("\n  Failure: Use display ont autofind <port-id>.")
      return True

    if self.cli_state == "INTERFACE_GPON" and cmd_lower.startswith("ont confirm "):
      try:
        port_id = parts[2]
        ont_idx = lower.index("ontid")
        ont_id = int(parts[ont_idx + 1])
        sn_idx = lower.index("sn-auth")
        sn = parts[sn_idx + 1]
        full_port = f"{self.current_gpon_port}/{port_id}"
        # Validasi kehadiran fisik: SN harus berasal dari ONT yang saat ini
        # benar-benar terhubung ke port ini (bukan cache lama).
        entries = self._autofind_entries(port_id)
        if sn not in {entry_sn for _port, entry_sn, _node in entries}:
          self.append_plain_text(
              "\n  Failure: ONT SN was not found by auto-find on this port."
          )
          return True
        conn = get_connection()
        cursor = conn.cursor()
        previous = cursor.execute(
            "SELECT ont_name, description FROM olt_onts"
            " WHERE username = ? AND port = ? AND ont_id = ?",
            (self.username, full_port, ont_id),
        ).fetchone()
        ont_name = previous[0] if previous else None
        description = previous[1] if previous else None
        cursor.execute(
            "DELETE FROM olt_onts WHERE username = ? AND port = ? AND ont_id = ?",
            (self.username, full_port, ont_id),
        )
        cursor.execute(
            "INSERT OR REPLACE INTO olt_onts "
            "(username, port, ont_id, sn, control_flag, run_state, config_state, match_state, "
            "line_profile_id, service_profile_id, auth_mode, auth_value, alarm_profile_id, "
            "ont_name, description) "
            "VALUES (?, ?, ?, ?, 'active', 'online', 'normal', 'match', 0, 0, 'sn-auth', ?, 1, ?, ?)",
            (self.username, full_port, ont_id, sn, sn, ont_name, description),
        )
        conn.commit()
        conn.close()
        self.append_plain_text(f"\n  ONT {ont_id} confirmed on {full_port}.")
      except (ValueError, IndexError):
        self.append_plain_text("\n  Failure: Invalid ont confirm command syntax.")
      return True

    if self.cli_state == "INTERFACE_GPON" and cmd_lower.startswith("ont alarm-profile "):
      try:
        port_id = parts[2]
        ont_id = int(parts[3])
        profile_idx = lower.index("profile-id")
        alarm_id = int(parts[profile_idx + 1])
        full_port = f"{self.current_gpon_port}/{port_id}"
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE olt_onts SET alarm_profile_id = ? "
            "WHERE username = ? AND port = ? AND ont_id = ?",
            (alarm_id, self.username, full_port, ont_id),
        )
        conn.commit()
        conn.close()
        self.append_plain_text(f"\n  Alarm profile {alarm_id} bound to ONT {ont_id}.")
      except (ValueError, IndexError):
        self.append_plain_text("\n  Failure: Invalid ont alarm-profile command syntax.")
      return True

    if self.cli_state == "INTERFACE_GPON" and cmd_lower.startswith("display ont info"):
      ont_status.refresh_ont_states(self.username, self.node_name)
      tokens = cmd.split()
      args = tokens[3:]
      full_port = None
      ont_filter = None
      try:
        if not args:
          raise ValueError
        if args[0].lower() == "all":
          full_port = None
        elif args[0].count("/") == 2:
          full_port = args[0]
        else:
          full_port = f"{self.current_gpon_port}/{args[0]}"
        if len(args) > 1 and args[1].lower() != "all":
          ont_filter = int(args[1])

        conn = get_connection()
        cursor = conn.cursor()
        query = (
            "SELECT port, ont_id, sn, control_flag, run_state, config_state, match_state "
            "FROM olt_onts WHERE username = ? AND control_flag IS NOT NULL"
        )
        params = [self.username]
        if full_port:
          query += " AND port = ?"
          params.append(full_port)
        if ont_filter is not None:
          query += " AND ont_id = ?"
          params.append(ont_filter)
        query += " ORDER BY port, ont_id"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        lines = [
            f"\n  Command:\n          {cmd}",
            "  ---------------------------------------------------------------------",
            "  F/S/P       ONT ID   SN                Control flag  Run state  Config state  Match state",
            "  ---------------------------------------------------------------------",
        ]
        for port, ont_id, sn, ctrl, run_state, cfg_state, match_state in rows:
          lines.append(
              f"  {port:<11} {str(ont_id if ont_id is not None else '-'):<8} {(sn or '-'):<17}"
              f" {(ctrl or '-'):<13} {(run_state or '-'):<10}"
              f" {(cfg_state or '-'):<13} {match_state or '-'}"
          )
        lines.append("  ---------------------------------------------------------------------")
        if full_port:
          lines.append(
              f"  In port {full_port}, the total of ONTs are: {len(rows)}, online: "
              f"{sum(1 for row in rows if row[4] == 'online')}"
          )
        else:
          lines.append(
              f"  Total ONTs: {len(rows)}, online: "
              f"{sum(1 for row in rows if row[4] == 'online')}"
          )
        self.append_plain_text("\n".join(lines))
      except (ValueError, IndexError):
        self.append_plain_text(
            "\n  Failure: Use display ont info {all|<port>|all} [<ont-id>|all]."
        )
      return True

    # Simplified service-port mode uses ETH/IPHOST rather than a GEM port.
    if cmd_lower.startswith("service-port ") and self.cli_state == "CONFIG":
      try:
        self._handle_service_port_command(cmd, parts, lower)
      except (ValueError, IndexError, TypeError) as exc:
        self.append_plain_text(f"\n  Failure: {exc or 'Invalid service-port syntax.'}")
      return True

    if cmd_lower.startswith("undo service-port "):
      if self.cli_state != "CONFIG":
        self.append_plain_text("\n  Failure: Run undo service-port in global config mode.")
      else:
        try: self._handle_service_port_command(cmd, parts, lower)
        except (ValueError, IndexError, TypeError) as exc: self.append_plain_text(f"\n  Failure: {exc}")
      return True

    if cmd_lower.startswith("display service-port"):
      if self.cli_state != "ENABLE":
        self.append_plain_text("\n  Failure: Run display service-port in privilege mode.")
      else:
        try: self._handle_service_port_command(cmd, parts, lower)
        except (ValueError, IndexError, TypeError) as exc: self.append_plain_text(f"\n  Failure: {exc}")
      return True

    # Multicast VLAN and BTV command modes.
    if self.cli_state == "MVLAN":
      if cmd_lower == "btv":
        self.cli_state = "BTV"
        return True
      if cmd_lower.startswith("multicast-vlan "):
        try:
          self.current_mcast_vlan_id = int(parts[1])
          self.cli_state = "MVLAN"
        except (ValueError, IndexError):
          self.append_plain_text("\n  Failure: Invalid multicast VLAN ID.")
        return True
      if len(parts) >= 3 and lower[:2] == ["igmp", "version"]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE olt_multicast_vlans SET igmp_version = ? "
            "WHERE username = ? AND vlan_id = ?",
            (parts[2].lower(), self.username, self.current_mcast_vlan_id),
        )
        conn.commit()
        conn.close()
        self.append_plain_text(f"\n  IGMP version set to {parts[2].lower()}.")
        return True
      if len(parts) >= 3 and lower[:2] == ["igmp", "mode"]:
        self.pending_confirmation = ("igmp-mode", self.current_mcast_vlan_id, parts[2].lower())
        self.append_plain_text("\n  Are you sure to change IGMP mode?(y/n)[n]:")
        return True
      if len(parts) >= 3 and lower[:2] == ["igmp", "uplink-port"]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE olt_multicast_vlans SET uplink_port = ? "
            "WHERE username = ? AND vlan_id = ?",
            (parts[2], self.username, self.current_mcast_vlan_id),
        )
        conn.commit()
        conn.close()
        self.append_plain_text(f"\n  IGMP uplink port set to {parts[2]}.")
        return True
      if len(parts) >= 4 and lower[:3] == ["igmp", "program", "add"]:
        try:
          name = parts[lower.index("name") + 1]
          ip = parts[lower.index("ip") + 1]
          source = parts[lower.index("sourceip") + 1]
          priority = int(parts[lower.index("priority") + 1])
          conn = get_connection()
          cursor = conn.cursor()
          cursor.execute(
              "INSERT OR REPLACE INTO olt_multicast_programs "
              "(username, vlan_id, program_name, program_ip, source_ip, priority) "
              "VALUES (?, ?, ?, ?, ?, ?)",
              (self.username, self.current_mcast_vlan_id, name, ip, source, priority),
          )
          conn.commit()
          conn.close()
          self.append_plain_text(f"\n  Multicast program {name} configured.")
        except (ValueError, IndexError):
          self.append_plain_text("\n  Failure: Invalid igmp program syntax.")
        return True
      if len(parts) >= 5 and lower[:3] == ["igmp", "multicast-vlan", "member"]:
        try:
          sp_id = int(parts[lower.index("service-port") + 1])
          conn = get_connection()
          cursor = conn.cursor()
          cursor.execute(
              "INSERT OR IGNORE INTO olt_multicast_members "
              "(username, vlan_id, service_port_id) VALUES (?, ?, ?)",
              (self.username, self.current_mcast_vlan_id, sp_id),
          )
          conn.commit()
          conn.close()
          self.append_plain_text(f"\n  Service port {sp_id} added to multicast VLAN.")
        except (ValueError, IndexError):
          self.append_plain_text("\n  Failure: Invalid multicast member command.")
        return True

    if self.cli_state == "BTV":
      if cmd_lower.startswith("igmp uplink-port-mode "):
        mode = parts[2].lower() if len(parts) > 2 else "default"
        self.pending_confirmation = ("uplink-mode", self.current_mcast_vlan_id, mode)
        self.append_plain_text("\n  Are you sure to change the uplink port mode?(y/n)[n]:")
        return True
      if cmd_lower.startswith("multicast-vlan "):
        try:
          self.current_mcast_vlan_id = int(parts[1])
          conn = get_connection()
          cursor = conn.cursor()
          cursor.execute(
              "INSERT OR IGNORE INTO olt_multicast_vlans (username, vlan_id) VALUES (?, ?)",
              (self.username, self.current_mcast_vlan_id),
          )
          conn.commit()
          conn.close()
          self.cli_state = "MVLAN"
        except (ValueError, IndexError):
          self.append_plain_text("\n  Failure: Invalid multicast VLAN ID.")
        return True
      if cmd_lower.startswith("igmp profile add "):
        try:
          name = parts[lower.index("profile-name") + 1]
          conn = get_connection()
          cursor = conn.cursor()
          cursor.execute(
              "INSERT OR IGNORE INTO olt_multicast_profiles "
              "(username, vlan_id, profile_name) VALUES (?, ?, ?)",
              (self.username, self.current_mcast_vlan_id, name),
          )
          conn.commit()
          conn.close()
          self.append_plain_text(f"\n  IGMP profile {name} created.")
        except IndexError:
          self.append_plain_text("\n  Failure: profile-name is required.")
        return True
      if cmd_lower.startswith("igmp profile profile-name "):
        try:
          profile_name = parts[lower.index("profile-name") + 1]
          program_name = parts[lower.index("program-name") + 1]
          permission = parts[-1].lower()
          conn = get_connection()
          cursor = conn.cursor()
          cursor.execute(
              "INSERT OR REPLACE INTO olt_multicast_profile_programs "
              "(username, vlan_id, profile_name, program_name, permission) "
              "VALUES (?, ?, ?, ?, ?)",
              (self.username, self.current_mcast_vlan_id, profile_name,
               program_name, permission),
          )
          conn.commit()
          conn.close()
          self.append_plain_text(f"\n  Program {program_name} granted to profile {profile_name}.")
        except (ValueError, IndexError):
          self.append_plain_text("\n  Failure: Invalid igmp profile program command.")
        return True
      if cmd_lower.startswith("igmp user add service-port "):
        try:
          sp_id = int(parts[lower.index("service-port") + 1])
          auth = parts[-1].lower()
          conn = get_connection()
          cursor = conn.cursor()
          cursor.execute(
              "INSERT OR REPLACE INTO olt_multicast_users "
              "(username, vlan_id, service_port_id, auth_mode) VALUES (?, ?, ?, ?)",
              (self.username, self.current_mcast_vlan_id, sp_id, auth),
          )
          conn.commit()
          conn.close()
          self.append_plain_text(f"\n  Multicast user service-port {sp_id} configured.")
        except (ValueError, IndexError):
          self.append_plain_text("\n  Failure: Invalid multicast user command.")
        return True
      if cmd_lower.startswith("igmp user bind-profile service-port "):
        try:
          sp_id = int(parts[lower.index("service-port") + 1])
          profile_name = parts[lower.index("profile-name") + 1]
          conn = get_connection()
          cursor = conn.cursor()
          cursor.execute(
              "UPDATE olt_multicast_users SET profile_name = ? "
              "WHERE username = ? AND vlan_id = ? AND service_port_id = ?",
              (profile_name, self.username, self.current_mcast_vlan_id, sp_id),
          )
          conn.commit()
          conn.close()
          self.append_plain_text(f"\n  Profile {profile_name} bound to service-port {sp_id}.")
        except (ValueError, IndexError):
          self.append_plain_text("\n  Failure: Invalid multicast profile binding command.")
        return True

    if cmd_lower == "save" and self.cli_state == "ENABLE":
      try:
        save_user_configuration(self.username)
        self.append_plain_text(
            "\n  It will take several minutes to save configuration file, please wait..."
            "\n  Configuration file had been saved successfully."
            "\n  System configuration data has been saved completely."
        )
      except Exception as exc:
        self.append_plain_text(f"\n  Failure: Configuration could not be saved ({exc}).")
      return True

    if cmd_lower == "save":
      self.append_plain_text("\n  Failure: Run this command in privilege mode.")
      return True

    return False

  def _handle_tr069_profile_remove_or_modify(self, cmd, operation):
    conn = None
    try:
      parts = shlex.split(cmd)
      lowered = [part.lower() for part in parts]
      selector = "profile-id" if "profile-id" in lowered else "profile-name" if "profile-name" in lowered else None
      if selector is None: raise ValueError("Specify profile-id or profile-name.")
      value = parts[lowered.index(selector) + 1]
      conn = get_connection(); cursor = conn.cursor()
      if selector == "profile-id":
        if not value.isdigit() or not 1 <= int(value) <= 32: raise ValueError("profile-id must be 1-32.")
        clause, target = "profile_id = ?", int(value)
      else:
        if not 1 <= len(value) <= 32 or "?" in value: raise ValueError("profile-name must be 1-32 characters.")
        clause, target = "profile_name = ? COLLATE NOCASE", value
      cursor.execute(f"SELECT profile_id FROM olt_tr069_server_profiles WHERE username = ? AND {clause}", (self.username, target))
      row = cursor.fetchone()
      if not row: raise LookupError("TR069 server profile was not found.")
      profile_id = row[0]
      cursor.execute("SELECT COUNT(*) FROM olt_tr069_bindings WHERE username = ? AND profile_id = ?", (self.username, profile_id))
      if cursor.fetchone()[0]: raise RuntimeError("Profile is bound; unbind the ONT first.")
      if operation == "delete":
        cursor.execute("DELETE FROM olt_tr069_server_profiles WHERE username = ? AND profile_id = ?", (self.username, profile_id))
        conn.commit(); conn.close()
        self.append_plain_text(f"\n  Deleting the ONT TR069 server profile succeeded")
        return
      updates = {}
      if "url" in lowered:
        updates["url"] = parts[lowered.index("url") + 1]
      if "user" in lowered:
        idx = lowered.index("user")
        updates["user_name"] = parts[idx + 1]
        if idx + 2 < len(parts) and lowered[idx + 2] != "auth-realm": updates["user_password"] = parts[idx + 2]
      if "auth-realm" in lowered: updates["auth_realm"] = parts[lowered.index("auth-realm") + 1]
      if not updates: raise ValueError("Specify at least one of url, user, or auth-realm.")
      limits = {"url": 63, "user_name": 50, "user_password": 25, "auth_realm": 24}
      for field, val in updates.items():
        if not 1 <= len(val) <= limits[field] or "?" in val: raise ValueError(f"Invalid {field.replace('_', ' ')} length or character.")
      assignments = ", ".join(f"{field} = ?" for field in updates)
      cursor.execute(f"UPDATE olt_tr069_server_profiles SET {assignments} WHERE username = ? AND profile_id = ?", (*updates.values(), self.username, profile_id))
      conn.commit(); conn.close()
      self.append_plain_text("\n  modifying the ONT TR069 server profile succeeded")
    except sqlite3.IntegrityError:
      if conn: conn.close()
      self.append_plain_text("\n  Failure: A profile with that ID or name already exists.")
    except (ValueError, IndexError, LookupError, RuntimeError) as exc:
      if conn: conn.close()
      self.append_plain_text(f"\n  Failure: {exc}")

  def _handle_tr069_binding(self, cmd, unbind=False):
    conn = None
    try:
      parts = shlex.split(cmd)
      lowered = [part.lower() for part in parts]
      # Syntax: ont tr069-server-config <port-id> <ont-id> profile-id <id>
      offset = 1 if unbind else 0
      base = 2 + offset
      if len(parts) != base + 4 or lowered[base + 2] not in ("profile-id", "profile-name"):
        raise ValueError("Use ont tr069-server-config <port-id> <ont-id> profile-id <id>.")
      port_id, ont_id = parts[base], int(parts[base + 1])
      if not port_id.isdigit() or not 0 <= int(port_id) <= 255 or ont_id < 0: raise ValueError("Invalid GPON port or ONT ID.")
      selector, value = lowered[base + 2], parts[base + 3]
      conn = get_connection(); cursor = conn.cursor()
      full_port = f"{self.current_gpon_port}/{port_id}"
      cursor.execute("SELECT 1 FROM olt_onts WHERE username = ? AND port = ? AND ont_id = ?", (self.username, full_port, ont_id))
      if not cursor.fetchone(): raise LookupError("ONT was not found on this GPON port.")
      if selector == "profile-id":
        if not value.isdigit() or not 1 <= int(value) <= 32: raise ValueError("profile-id must be 1-32.")
        clause, target = "profile_id = ?", int(value)
      else: clause, target = "profile_name = ? COLLATE NOCASE", value
      cursor.execute(f"SELECT profile_id FROM olt_tr069_server_profiles WHERE username = ? AND {clause}", (self.username, target))
      found = cursor.fetchone()
      if not found: raise LookupError("TR069 server profile was not found.")
      if unbind:
        cursor.execute("DELETE FROM olt_tr069_bindings WHERE username = ? AND port = ? AND ont_id = ?", (self.username, full_port, ont_id))
        if cursor.rowcount == 0: raise LookupError("ONT has no TR069 server profile binding.")
      else:
        cursor.execute("INSERT OR REPLACE INTO olt_tr069_bindings (username,port,ont_id,profile_id) VALUES (?,?,?,?)", (self.username, full_port, ont_id, found[0]))
      conn.commit(); conn.close()
      self.append_plain_text(f"\n  TR069 server profile {'unbound' if unbind else 'bound'} for ONT {ont_id} on {full_port}.")
    except sqlite3.IntegrityError:
      if conn: conn.close()
      self.append_plain_text("\n  Failure: Unable to bind the TR069 profile.")
    except (ValueError, IndexError, LookupError) as exc:
      if conn: conn.close()
      self.append_plain_text(f"\n  Failure: {exc}")

  def handle_user_input(self, cmd):
    # Terima variasi penulisan seperti di CLI asli:
    #   display ont-auto-find all / display ont auto-find all
    cmd = re.sub(
        r"(?i)^\s*display\s+ont[-\s]?auto[-\s]?find\b",
        "display ont autofind",
        cmd.strip(),
    )
    cmd_lower = cmd.lower().strip()

    if cmd_lower == "return":
      self.cli_state = "ENABLE"
      self.print_prompt()
      return

    if cmd_lower == "disable":
      if self.cli_state in ("ENABLE", "CONFIG"):
        self.cli_state = "USER"
      else:
        self.append_plain_text("\n  Failure: disable is available in privilege or global config mode.")
      self.print_prompt()
      return

    if self.cli_state == "USER" and cmd_lower in (
        "config", "configure", "configure terminal"
    ):
      self.append_plain_text("\n  Failure: Enter privilege mode with 'enable' first.")
      self.print_prompt()
      return

    if cmd_lower in ("quit", "exit"):
      if self.cli_state == "BTV":
        self.cli_state = "MVLAN"
      elif self.cli_state in ("INTERFACE_GPON", "INTERFACE_EPON", "INTERFACE_ETH", "LINEPROFILE", "SRVPROFILE", "VLANIF", "MVLAN", "VLAN_SRVPROFILE"):
        self.cli_state = "CONFIG"
        if getattr(self, "current_vlan_service_profile_id", None) is not None:
          self.current_vlan_service_profile_id = None
      elif self.cli_state == "CONFIG":
        self.cli_state = "ENABLE"
      elif self.cli_state == "ENABLE":
        self.cli_state = "USER"
      else:
        main_window = self.window()
        if hasattr(main_window, "close_console_widget"):
          main_window.close_console_widget(self)
        else:
          self.close()
        return
      self.print_prompt()
      return

    if cmd_lower.startswith("display native-vlan"):
      self.append_plain_text(eth_ports.display_native_vlans(self.username, cmd))
      self.print_prompt()
      return

    if cmd_lower.startswith("display bpdu tunnel"):
      if self.cli_state != "ENABLE":
        self.append_plain_text("\n  Failure: Run display bpdu tunnel in privilege mode.")
      elif cmd_lower == "display bpdu tunnel config":
        self.append_plain_text(bpdu.display_config(self.username, cmd))
      elif cmd_lower.startswith("display bpdu tunnel vlan"):
        self.append_plain_text(bpdu.display_vlan(self.username, cmd))
      else:
        self.append_plain_text("\n  Failure: Use display bpdu tunnel {config|vlan <VLAN-ID> [dedicated-net-id <1-8>]}.")
      self.print_prompt()
      return

    if cmd_lower.startswith("display vlan service-profile"):
      self.append_plain_text(vlan_service_profile.display(self.username, cmd))
      self.print_prompt()
      return

    if self.cli_state == "CONFIG" and cmd_lower.startswith("bpdu tunnel vlan"):
      self.append_plain_text(bpdu.configure(self.username, cmd))
      self.print_prompt()
      return

    if self.cli_state == "CONFIG" and cmd_lower.startswith("vlan service-profile"):
      entered = vlan_service_profile.enter(self.username, cmd)
      if entered is None:
        self.append_plain_text("\n  Failure: Use vlan service-profile profile-id <ID>.")
      elif isinstance(entered[0], bool):
        self.append_plain_text(f"\n  Failure: {entered[1]}")
      else:
        self.current_vlan_service_profile_id = entered[0]
        self.cli_state = "VLAN_SRVPROFILE"
      self.print_prompt()
      return

    if self.cli_state == "CONFIG" and cmd_lower.startswith("vlan bind service-profile"):
      result = vlan_service_profile.bind_vlan(self.username, cmd)
      self.append_plain_text(result or "\n  Failure: Use vlan bind service-profile <VLAN-ID> profile-id <ID>.")
      self.print_prompt()
      return

    pitp_mode = "vlan-srvprofile" if self.cli_state == "VLAN_SRVPROFILE" else self.cli_state.lower()
    pitp_output = pitp.handle_command(self.username, cmd, self.node_name, pitp_mode, self.current_vlan_service_profile_id)
    if pitp_output is not None:
      self.append_plain_text(pitp_output)
      self.print_prompt()
      return

    if self.cli_state == "VLAN_SRVPROFILE":
      profile_output = vlan_service_profile.handle(self.username, cmd, self.current_vlan_service_profile_id)
      self.append_plain_text(profile_output)
      self.print_prompt()
      return

    ont_port_output = ont_ports.handle_command(
        self.username, cmd, self.current_gpon_port, self.node_name
    )
    if ont_port_output is not None:
      self.append_plain_text(ont_port_output)
      self.print_prompt()
      return

    # Layanan WAN: PPPoE client ONT, DHCP lease, sesi BRAS.
    service_output = services.handle_command(        self.username,
        self.node_name,
        cmd,
        self.cli_state,
        self.current_gpon_port,
    )
    if service_output is not None:
      self.append_plain_text(service_output)
      self.print_prompt()
      return

    # Perintah tambahan: version, current-configuration, routing-table, LLDP,
    # port state/optic, dan operasi ONT (optical-info/wan-info/activate/
    # deactivate/delete). Mengembalikan None bila perintah bukan miliknya.
    extended_output = dispatch_extended(
        self.username, self.node_name, cmd, self.cli_state, self.current_gpon_port
    )
    if extended_output is not None:
      self.append_plain_text(extended_output)
      self.print_prompt()
      return

    if self.handle_simplified_triple_play_command(cmd, cmd_lower):
      self.print_prompt()
      return

    # --- Mode State Transitions ---
    if self.cli_state == "USER" and cmd_lower == "enable":
      self.cli_state = "ENABLE"

    elif self.cli_state == "ENABLE" and cmd_lower in (
        "config", "configure", "configure terminal"
    ):
      self.cli_state = "CONFIG"

    elif self.cli_state == "CONFIG" and cmd_lower.startswith("interface gpon"):
      try:
        parts = shlex.split(cmd)
      except ValueError:
        parts = cmd.split()
      if len(parts) >= 3:
        self.current_gpon_port = parts[2]
      self.cli_state = "INTERFACE_GPON"

    elif self.cli_state == "CONFIG" and cmd_lower.startswith("interface epon"):
      parts = cmd.split()
      if len(parts) >= 3:
        self.current_gpon_port = parts[2]
      self.cli_state = "INTERFACE_EPON"

    elif self.cli_state == "CONFIG" and cmd_lower.startswith("ont tr069-server-profile add"):
      try:
        parts = shlex.split(cmd)
        lower = [part.lower() for part in parts]
        profile_id = int(parts[lower.index("profile-id") + 1]) if "profile-id" in lower else None
        name = parts[lower.index("profile-name") + 1] if "profile-name" in lower else None
        url = parts[lower.index("url") + 1] if "url" in lower else None
        username = parts[lower.index("user") + 1] if "user" in lower and lower.index("user") + 1 < len(parts) else ""
        user_password = ""
        realm = ""
        if "user" in lower:
          user_index = lower.index("user")
          next_index = user_index + 2
          if next_index < len(parts) and lower[next_index] not in ("auth-realm",):
            user_password = parts[next_index]
        if "auth-realm" in lower: realm = parts[lower.index("auth-realm") + 1]
        for flag in ("profile-id", "profile-name", "url", "user", "auth-realm"):
          if flag in lower and lower.index(flag) + 1 >= len(parts): raise ValueError(f"{flag} requires a value")
        if url is None: raise ValueError("url is required")
        if not 1 <= len(url) <= 63 or "?" in url: raise ValueError("URL must be 1-63 visible characters and cannot contain '?' ")
        if username and (not 1 <= len(username) <= 50 or "?" in username): raise ValueError("user name must be 1-50 characters")
        if user_password and (not 1 <= len(user_password) <= 25 or "?" in user_password): raise ValueError("password must be 1-25 characters")
        if realm and (not 1 <= len(realm) <= 24 or "?" in realm): raise ValueError("auth realm must be 1-24 characters")
        if name and (not 1 <= len(name) <= 32 or "?" in name): raise ValueError("profile-name must be 1-32 characters")
        conn = get_connection(); cursor = conn.cursor()
        if profile_id is None:
          cursor.execute("SELECT profile_id FROM olt_tr069_server_profiles WHERE username = ? ORDER BY profile_id", (self.username,))
          occupied = {row[0] for row in cursor.fetchall()}
          profile_id = next((candidate for candidate in range(1, 33) if candidate not in occupied), None)
        if profile_id is None or not 1 <= profile_id <= 32: raise ValueError("profile-id must be 1-32; no free ID is available")
        name = name or f"tr069-server-profile_{profile_id}"
        cursor.execute("INSERT INTO olt_tr069_server_profiles (username,profile_id,profile_name,url,user_name,user_password,auth_realm) VALUES (?,?,?,?,?,?,?)", (self.username, profile_id, name, url, username, user_password, realm))
        conn.commit(); conn.close()
        self.append_plain_text(f"\n  Adding an ONT TR069 server profile succeeded\n  Profile ID  : {profile_id}\n  Profile name: {name}")
      except sqlite3.IntegrityError:
        conn.close()
        self.append_plain_text("\n  Failure: Profile ID or profile name already exists.")
      except (ValueError, IndexError) as exc:
        try: conn.close()
        except UnboundLocalError: pass
        self.append_plain_text(f"\n  Failure: {exc}")

    elif self.cli_state == "CONFIG" and cmd_lower.startswith("ont tr069-server-profile delete"):
      self._handle_tr069_profile_remove_or_modify(cmd, "delete")

    elif self.cli_state == "CONFIG" and cmd_lower.startswith("ont tr069-server-profile modify"):
      self._handle_tr069_profile_remove_or_modify(cmd, "modify")

    elif cmd_lower.startswith("display ont tr069-server-profile"):
      allowed = self.cli_state in ("ENABLE", "INTERFACE_GPON", "INTERFACE_EPON")
      if "bound-info" in cmd_lower:
        allowed = self.cli_state == "ENABLE"
      if not allowed:
        self.append_plain_text("\n  Failure: Run this display command in privilege mode or GPON/EPON mode (bound-info requires privilege mode).")
      else:
        self.append_plain_text(build_tr069_profile_display(self.username, cmd))

    elif self.cli_state in ("INTERFACE_GPON", "INTERFACE_EPON") and cmd_lower.startswith("ont tr069-server-config"):
      self._handle_tr069_binding(cmd, unbind=False)

    elif self.cli_state in ("INTERFACE_GPON", "INTERFACE_EPON") and cmd_lower.startswith("undo ont tr069-server-config"):
      self._handle_tr069_binding(cmd, unbind=True)

    # Huawei documents the effective profile query in privilege or GPON mode.
    elif (
        self.cli_state in ("ENABLE", "INTERFACE_GPON")
        and cmd_lower.startswith("display ont-lineprofile gpon")
    ):
      self.append_plain_text(build_ont_lineprofile_display(self.username, cmd))

    elif cmd_lower.startswith("display ont-lineprofile gpon"):
      self.append_plain_text(
          "\n  Failure: Run this command in privilege mode or GPON mode."
      )

    elif self.cli_state == "CONFIG" and cmd_lower.startswith("undo ont-lineprofile gpon"):
      try:
        parts = shlex.split(cmd)
        lowered = [part.lower() for part in parts]
        if "profile-id" in lowered:
          profile_id = int(parts[lowered.index("profile-id") + 1])
          lookup = ("profile_id = ?", profile_id)
        elif "profile-name" in lowered:
          profile_name = parts[lowered.index("profile-name") + 1]
          lookup = ("profile_name = ? COLLATE NOCASE", profile_name)
        else:
          raise ValueError("Specify profile-id or profile-name.")
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT profile_id FROM olt_line_profiles WHERE username=? AND {lookup[0]}",
            (self.username, lookup[1]),
        )
        row = cursor.fetchone()
        if not row:
          conn.close()
          raise ValueError("Line profile was not found.")
        target_id = row[0]
        if target_id == 0:
          conn.close()
          raise ValueError("System default line profile 0 cannot be deleted.")
        cursor.execute(
            "SELECT COUNT(*) FROM olt_onts WHERE username=? AND line_profile_id=?",
            (self.username, target_id),
        )
        ont_bindings = cursor.fetchone()[0]
        cursor.execute(
            "SELECT bind_count FROM olt_line_profiles WHERE username=? AND profile_id=?",
            (self.username, target_id),
        )
        stored = cursor.fetchone()
        if ont_bindings or (stored and stored[0] > 0):
          conn.close()
          raise ValueError("The line profile is bound to an ONT and cannot be deleted.")
        for table in (
            "olt_line_profile_settings", "olt_line_profile_tconts",
            "olt_line_profile_gems", "olt_line_profile_mappings",
            "olt_line_profile_vlan_bundles", "olt_line_profile_details",
        ):
          cursor.execute(
              f"DELETE FROM {table} WHERE username=? AND profile_id=?",
              (self.username, target_id),
          )
        cursor.execute(
            "DELETE FROM olt_line_profiles WHERE username=? AND profile_id=?",
            (self.username, target_id),
        )
        conn.commit()
        conn.close()
        self.append_plain_text(f"\n  Command:\n          {cmd}")
      except (ValueError, IndexError) as exc:
        self.append_plain_text(f"\n  Failure: {exc}")

    # 3. PERINTAH: ont-lineprofile gpon / ont-line-profile gpon[cite: 14]
    elif self.cli_state == "CONFIG" and (
        cmd_lower.startswith("ont-lineprofile gpon")
        or cmd_lower.startswith("ont-line-profile gpon")
    ):
      try:
        parts = shlex.split(cmd)
      except ValueError:
        self.append_plain_text("\n  Failure: Invalid profile-name quoting.")
        return
      prof_id = None
      prof_name = None
      invalid_profile_id = False
      for i, part in enumerate(parts[:-1]):
        if part.lower() == "profile-id":
          try:
            prof_id = int(parts[i + 1])
          except ValueError:
            invalid_profile_id = True
        elif part.lower() == "profile-name":
          prof_name = parts[i + 1].strip('"')

      if invalid_profile_id:
        self.append_plain_text("\n  Failure: profile-id must be an integer from 0 to 8195.")
        return

      conn = get_connection()
      cursor = conn.cursor()
      profile_row = None
      if prof_id is not None:
        cursor.execute(
            "SELECT profile_name FROM olt_line_profiles "
            "WHERE username = ? AND profile_id = ?",
            (self.username, prof_id),
        )
        profile_row = cursor.fetchone()
      elif prof_name:
        cursor.execute(
            "SELECT profile_id, profile_name FROM olt_line_profiles "
            "WHERE username = ? AND profile_name = ?",
            (self.username, prof_name),
        )
        profile_row = cursor.fetchone()
        if profile_row:
          prof_id = profile_row[0]
        else:
          cursor.execute(
              "SELECT profile_id FROM olt_line_profiles WHERE username = ?",
              (self.username,),
          )
          used_ids = {row[0] for row in cursor.fetchall()}
          prof_id = next((candidate for candidate in range(8196) if candidate not in used_ids), None)
      else:
        prof_id = None
        cursor.execute(
            "SELECT profile_id FROM olt_line_profiles WHERE username = ?",
            (self.username,),
        )
        used_ids = {row[0] for row in cursor.fetchall()}
        prof_id = next((candidate for candidate in range(8196) if candidate not in used_ids), None)
      if prof_id is None or not 0 <= prof_id <= 8195:
        conn.close()
        self.append_plain_text("\n  Failure: Line profile ID must be in range 0-8195.")
        return

      if prof_id is None:
        cursor_conn = get_connection()
        cursor = cursor_conn.cursor()
        cursor.execute("SELECT profile_id FROM olt_service_profiles WHERE username = ? ORDER BY profile_id", (self.username,))
        used_ids = {r[0] for r in cursor.fetchall()}
        prof_id = next((candidate for candidate in range(1, 8193) if candidate not in used_ids), 1)
        cursor_conn.close()
      self.current_profile_id = prof_id
      self.current_line_profile_name = (
          prof_name or (profile_row[1] if prof_name and profile_row else None)
          or (profile_row[0] if profile_row else None)
          or f"line-profile_{prof_id}"
      )
      if not 1 <= len(self.current_line_profile_name) <= 32 or "?" in self.current_line_profile_name:
        conn.close()
        self.append_plain_text("\n  Failure: profile-name must contain 1-32 characters and cannot contain '?'.")
        return
      cursor.execute(
          "SELECT profile_id FROM olt_line_profiles WHERE username=? "
          "AND profile_name=? COLLATE NOCASE AND profile_id<>?",
          (self.username, self.current_line_profile_name, prof_id),
      )
      if cursor.fetchone():
        conn.close()
        self.append_plain_text("\n  Failure: A line profile with this name already exists.")
        return
      cursor.execute(
          "SELECT omcc_encrypt, tr069_enabled, tr069_ip_index, fec_upstream, "
          "qos_mode, mapping_mode, gem_encrypt_mode "
          "FROM olt_line_profile_settings WHERE username = ? AND profile_id = ?",
          (self.username, prof_id),
      )
      settings = cursor.fetchone()
      self.pending_lineprofile_settings = (
          {"omcc_encrypt": settings[0], "tr069_enabled": settings[1],
           "tr069_ip_index": settings[2], "fec_upstream": settings[3],
           "qos_mode": settings[4], "mapping_mode": settings[5],
           "gem_encrypt_mode": settings[6]}
          if settings else {"omcc_encrypt": "on", "tr069_enabled": 0,
                            "tr069_ip_index": 0, "fec_upstream": "disable",
                            "qos_mode": "priority-queue", "mapping_mode": "vlan",
                            "gem_encrypt_mode": "aes-128"}
      )
      cursor.execute(
          "SELECT tcont_id, dba_profile_id FROM olt_line_profile_tconts "
          "WHERE username = ? AND profile_id = ? ORDER BY tcont_id",
          (self.username, prof_id),
      )
      self.pending_lineprofile_tconts = [
          {"tcont_id": r[0], "dba_profile_id": r[1]}
          for r in cursor.fetchall()
      ]
      if not any(t["tcont_id"] == 0 for t in self.pending_lineprofile_tconts):
        self.pending_lineprofile_tconts.append(
            {"tcont_id": 0, "dba_profile_id": 2 if prof_id == 0 else 1}
        )
      cursor.execute(
          "SELECT gem_id, service_type, tcont_id, encrypt, cascade, priority_queue, "
          "downstream_priority_queue, gem_car_index "
          "FROM olt_line_profile_gems WHERE username = ? AND profile_id = ? "
          "ORDER BY gem_id",
          (self.username, prof_id),
      )
      self.pending_lineprofile_gems = [
          {"gem_id": r[0], "service_type": r[1], "tcont_id": r[2],
           "encrypt": r[3], "cascade": r[4], "priority_queue": r[5],
           "downstream_priority_queue": r[6], "gem_car_index": r[7]}
          for r in cursor.fetchall()
      ]
      cursor.execute(
          "SELECT gem_id, mapping_id, match_type, match_value "
          "FROM olt_line_profile_mappings WHERE username = ? AND profile_id = ? "
          "ORDER BY gem_id, mapping_id",
          (self.username, prof_id),
      )
      self.pending_lineprofile_mappings = [
          {"gem_id": r[0], "mapping_id": r[1], "match_type": r[2],
           "match_value": r[3]}
          for r in cursor.fetchall()
      ]
      cursor.execute(
          "SELECT bundle_id, vlan_list FROM olt_line_profile_vlan_bundles "
          "WHERE username = ? AND profile_id = ? ORDER BY bundle_id",
          (self.username, prof_id),
      )
      self.pending_lineprofile_vlan_bundles = [
          {"bundle_id": r[0], "vlan_list": r[1]} for r in cursor.fetchall()
      ]
      conn.close()
      self.cli_state = "LINEPROFILE"

      output = (
          f"{{ profile-id<U><0,1024> }}:{prof_id}\n"
          f"{{ <cr>|profile-name<K> }}:{self.current_line_profile_name}\n\n"
          "  Command:\n"
          f"          ont-lineprofile gpon profile-name "
          f"{self.current_line_profile_name}"
      )
      self.append_plain_text(output)

    # Line profile subcommands used by the provisioning script.
    elif self.cli_state == "LINEPROFILE":
      try:
        parts = shlex.split(cmd)
      except ValueError:
        parts = cmd.split()
      if cmd_lower == "display ont-lineprofile current":
        self.append_plain_text(self._format_current_lineprofile(cmd))

      elif cmd_lower.startswith("fec-upstream"):
        if len(parts) == 2 and parts[1].lower() in ("enable", "disable"):
          self.pending_lineprofile_settings["fec_upstream"] = parts[1].lower()
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        else:
          self.append_plain_text("\n  Failure: Use fec-upstream enable|disable.")

      elif cmd_lower.startswith("qos-mode"):
        if len(parts) == 2 and parts[1].lower() in ("flow-car", "gem-car", "priority-queue"):
          self.pending_lineprofile_settings["qos_mode"] = parts[1].lower()
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        else:
          self.append_plain_text("\n  Failure: Use qos-mode flow-car|gem-car|priority-queue.")

      elif cmd_lower.startswith("mapping-mode"):
        modes = ("vlan", "priority", "vlan-priority", "port", "port-vlan",
                 "port-priority", "port-vlan-priority", "iptos", "vlan-iptos",
                 "vlan-bundle-priority", "port-vlan-bundle-priority")
        if len(parts) == 2 and parts[1].lower() in modes:
          self.pending_lineprofile_settings["mapping_mode"] = parts[1].lower()
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        else:
          self.append_plain_text("\n  Failure: Invalid mapping mode. Use mapping-mode <documented mode>.")

      elif cmd_lower.startswith("gem encrypt-mode"):
        if len(parts) == 3 and parts[2].lower() in ("aes-128", "aes-256", "sm4-128"):
          self.pending_lineprofile_settings["gem_encrypt_mode"] = parts[2].lower()
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        else:
          self.append_plain_text("\n  Failure: Use gem encrypt-mode aes-128|aes-256|sm4-128.")

      elif cmd_lower.startswith("omcc encrypt"):
        if len(parts) >= 3 and parts[2].lower() in ("on", "off"):
          self.pending_lineprofile_settings["omcc_encrypt"] = parts[2].lower()
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        else:
          self.append_plain_text("\n  Failure: Use omcc encrypt on|off.")

      elif cmd_lower.startswith("tr069-management"):
        lower_parts = [p.lower() for p in parts]
        if len(parts) >= 2 and parts[1].lower() == "enable":
          self.pending_lineprofile_settings["tr069_enabled"] = 1
        elif len(parts) >= 2 and parts[1].lower() == "disable":
          self.pending_lineprofile_settings["tr069_enabled"] = 0
        if "ip-index" in lower_parts:
          try:
            idx = lower_parts.index("ip-index")
            ip_index = int(parts[idx + 1])
            if not 0 <= ip_index <= 3:
              raise ValueError
            self.pending_lineprofile_settings["tr069_ip_index"] = ip_index
          except (ValueError, IndexError):
            self.append_plain_text("\n  Failure: Invalid TR-069 IP index.")
            self.print_prompt()
            return
        if len(parts) >= 2 and parts[1].lower() in ("enable", "disable", "ip-index"):
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        else:
          self.append_plain_text(
              "\n  Failure: Use tr069-management enable|disable or "
              "tr069-management ip-index <index>."
          )

      elif cmd_lower.startswith("tcont"):
        try:
          tcont_ids = parse_id_list(parts[1], 0, 127, "T-CONT ID")
          lower_parts = [part.lower() for part in parts]
          dba_key = "dba-profile-id" if "dba-profile-id" in lower_parts else "dba-profile-name"
          if dba_key not in lower_parts:
            raise ValueError("Specify dba-profile-id or dba-profile-name.")
          dba_value = parts[lower_parts.index(dba_key) + 1]
          conn = get_connection()
          cursor = conn.cursor()
          if dba_key == "dba-profile-id":
            dba_id = int(dba_value)
            cursor.execute("SELECT 1 FROM olt_dba_profiles WHERE username=? AND profile_id=?", (self.username, dba_id))
          else:
            cursor.execute("SELECT profile_id FROM olt_dba_profiles WHERE username=? AND profile_name=? COLLATE NOCASE", (self.username, dba_value))
            dba_row = cursor.fetchone()
            dba_id = dba_row[0] if dba_row else None
            if dba_id is None:
              conn.close()
              raise ValueError("DBA profile name was not found.")
            cursor.execute("SELECT 1 FROM olt_dba_profiles WHERE username=? AND profile_id=?", (self.username, dba_id))
          if not cursor.fetchone():
            conn.close()
            raise ValueError("DBA profile was not found.")
          conn.close()
          if not 0 <= dba_id <= 515:
            raise ValueError("DBA profile ID must be in range 0-515.")
          existing = {item["tcont_id"]: item for item in self.pending_lineprofile_tconts}
          for tcont_id in tcont_ids:
            existing[tcont_id] = {"tcont_id": tcont_id, "dba_profile_id": dba_id}
          if len(existing) > 128:
            raise ValueError("A line profile supports at most 128 T-CONTs.")
          self.pending_lineprofile_tconts = list(existing.values())
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        except (ValueError, IndexError) as exc:
          self.append_plain_text(f"\n  Failure: {exc or 'Use tcont <ID-list> dba-profile-id <ID>.'}")

      elif cmd_lower.startswith("undo tcont"):
        try:
          if len(parts) != 3:
            raise ValueError
          tcont_ids = parse_id_list(parts[2], 0, 127, "T-CONT ID")
          if 0 in tcont_ids:
            raise ValueError("T-CONT 0 cannot be removed from a manually created line profile.")
          used = {g["tcont_id"] for g in self.pending_lineprofile_gems}
          if used.intersection(tcont_ids):
            raise ValueError("A T-CONT bound to a GEM port cannot be removed.")
          self.pending_lineprofile_tconts = [
              t for t in self.pending_lineprofile_tconts if t["tcont_id"] not in tcont_ids
          ]
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        except (ValueError, IndexError) as exc:
          self.append_plain_text(f"\n  Failure: {exc or 'Use undo tcont <ID-list>.'}")

      elif cmd_lower.startswith("gem add"):
        try:
          gem_id = int(parts[2])
          service_type = parts[3].lower()
          tcont_idx = [p.lower() for p in parts].index("tcont")
          tcont_id = int(parts[tcont_idx + 1])
          if not 0 <= gem_id <= 1023 or service_type not in ("eth", "tdm"):
            raise ValueError("GEM index must be 0-1023 and service type must be eth or tdm.")
          if not 0 <= tcont_id <= 127 or tcont_id not in {t["tcont_id"] for t in self.pending_lineprofile_tconts}:
            raise ValueError("The specified T-CONT is not configured in this profile.")
          lower_parts = [p.lower() for p in parts]
          def option_value(key, default):
            return parts[lower_parts.index(key) + 1].lower() if key in lower_parts else default
          encrypt = option_value("encrypt", "on")
          cascade = option_value("cascade", "off")
          priority_queue = int(option_value("priority-queue", "0"))
          downstream = option_value("downstream-priority-queue", "adapt")
          gem_car = None
          if "gem-car" in lower_parts:
            gem_car = int(option_value("gem-car", "0"))
          if encrypt not in ("on", "off") or cascade not in ("on", "off"):
            raise ValueError("encrypt and cascade accept on|off.")
          if not 0 <= priority_queue <= 7:
            raise ValueError("priority-queue must be in range 0-7.")
          if downstream != "adapt":
            downstream = str(int(downstream))
            if not 0 <= int(downstream) <= 7:
              raise ValueError("downstream-priority-queue must be 0-7 or adapt.")
          if gem_car is not None and not 0 <= gem_car <= 1023:
            raise ValueError("gem-car traffic-table index must be in range 0-1023.")
          self.pending_lineprofile_gems = [
              item for item in self.pending_lineprofile_gems
              if item["gem_id"] != gem_id
          ]
          self.pending_lineprofile_gems.append(
              {"gem_id": gem_id, "service_type": service_type,
               "tcont_id": tcont_id, "encrypt": encrypt, "cascade": cascade,
               "priority_queue": priority_queue,
               "downstream_priority_queue": downstream, "gem_car_index": gem_car}
          )
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        except (ValueError, IndexError) as exc:
          self.append_plain_text(f"\n  Failure: {exc or 'Use gem add <ID> eth|tdm tcont <ID> [encrypt on|off].'}")

      elif cmd_lower.startswith("gem modify"):
        try:
          gem_id = int(parts[2])
          gem = next(g for g in self.pending_lineprofile_gems if g["gem_id"] == gem_id)
          lower_parts = [p.lower() for p in parts]
          for key in ("encrypt", "cascade"):
            if key in lower_parts:
              value = parts[lower_parts.index(key) + 1].lower()
              if value not in ("on", "off"):
                raise ValueError(f"{key} accepts on|off.")
              gem[key] = value
          for key, min_value, max_value in (("priority-queue", 0, 7), ("downstream-priority-queue", 0, 7), ("gem-car", 0, 1023)):
            if key in lower_parts:
              raw = parts[lower_parts.index(key) + 1].lower()
              if key == "downstream-priority-queue" and raw == "adapt":
                gem["downstream_priority_queue"] = "adapt"
              else:
                value = int(raw)
                if not min_value <= value <= max_value:
                  raise ValueError(f"{key} must be in range {min_value}-{max_value}.")
                field = {"priority-queue": "priority_queue", "downstream-priority-queue": "downstream_priority_queue", "gem-car": "gem_car_index"}[key]
                gem[field] = value
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        except (ValueError, IndexError, StopIteration) as exc:
          self.append_plain_text(f"\n  Failure: {exc or 'Use gem modify <ID> with GEM attributes.'}")

      elif cmd_lower.startswith("undo gem "):
        try:
          gem_id = int(parts[2])
          self.pending_lineprofile_gems = [g for g in self.pending_lineprofile_gems if g["gem_id"] != gem_id]
          self.pending_lineprofile_mappings = [m for m in self.pending_lineprofile_mappings if m["gem_id"] != gem_id]
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        except (ValueError, IndexError):
          self.append_plain_text("\n  Failure: Use undo gem <GEM-index>.")

      elif cmd_lower.startswith("gem mapping"):
        try:
          gem_id = int(parts[2])
          mapping_id = int(parts[3])
          match_type = parts[4].lower()
          match_value = int(parts[5])
          if not 0 <= gem_id <= 1023 or not 0 <= mapping_id <= 7:
            raise ValueError("GEM index must be 0-1023 and mapping index must be 0-7.")
          if not any(g["gem_id"] == gem_id for g in self.pending_lineprofile_gems):
            raise ValueError("The GEM port is not configured in this profile.")
          if match_type == "vlan" and not 0 <= match_value <= 4095:
            raise ValueError("VLAN ID must be in range 0-4095.")
          if match_type == "priority" and not 0 <= match_value <= 7:
            raise ValueError("Priority must be in range 0-7.")
          if match_type not in ("vlan", "priority"):
            raise ValueError("Use vlan or priority mapping in this command format.")
          if match_type == "priority" and not self.pending_lineprofile_mappings:
            self.pending_lineprofile_settings["mapping_mode"] = "priority"
          self.pending_lineprofile_mappings = [
              item for item in self.pending_lineprofile_mappings
              if (item["gem_id"], item["mapping_id"]) != (gem_id, mapping_id)
          ]
          self.pending_lineprofile_mappings.append(
              {"gem_id": gem_id, "mapping_id": mapping_id,
               "match_type": match_type, "match_value": match_value}
          )
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        except (ValueError, IndexError) as exc:
          self.append_plain_text(f"\n  Failure: {exc or 'Use gem mapping <GEM> <MAP> vlan|priority <value>.'}")

      elif cmd_lower.startswith("undo gem mapping"):
        try:
          gem_id, mapping_id = int(parts[3]), int(parts[4])
          self.pending_lineprofile_mappings = [
              m for m in self.pending_lineprofile_mappings
              if (m["gem_id"], m["mapping_id"]) != (gem_id, mapping_id)
          ]
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        except (ValueError, IndexError):
          self.append_plain_text("\n  Failure: Use undo gem mapping <GEM-index> <mapping-index>.")

      elif cmd_lower.startswith("vlan-bundle"):
        try:
          bundle_id = int(parts[1])
          if not 0 <= bundle_id <= 7 or parts[2].lower() != "vlan":
            raise ValueError("Use vlan-bundle <0-7> vlan <VLAN-list>.")
          vlan_ids = parse_id_list(parts[3], 1, 4094, "VLAN ID")
          if len(vlan_ids) > 8:
            raise ValueError("A VLAN bundle supports at most 8 VLANs.")
          self.pending_lineprofile_vlan_bundles = [
              b for b in self.pending_lineprofile_vlan_bundles if b["bundle_id"] != bundle_id
          ]
          self.pending_lineprofile_vlan_bundles.append(
              {"bundle_id": bundle_id, "vlan_list": ",".join(map(str, vlan_ids))}
          )
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        except (ValueError, IndexError) as exc:
          self.append_plain_text(f"\n  Failure: {exc or 'Use vlan-bundle <0-7> vlan <VLAN-list>.'}")

      elif cmd_lower.startswith("undo vlan-bundle"):
        try:
          bundle_id = int(parts[2])
          self.pending_lineprofile_vlan_bundles = [
              b for b in self.pending_lineprofile_vlan_bundles if b["bundle_id"] != bundle_id
          ]
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        except (ValueError, IndexError):
          self.append_plain_text("\n  Failure: Use undo vlan-bundle <ID>.")

      elif cmd_lower == "commit":
        conn = get_connection()
        cursor = conn.cursor()
        configured_tconts = {t["tcont_id"] for t in self.pending_lineprofile_tconts}
        if 0 not in configured_tconts:
          conn.close()
          self.append_plain_text("\n  Failure: T-CONT 0 must remain configured in the line profile.")
          return
        for tcont in self.pending_lineprofile_tconts:
          if not cursor.execute(
              "SELECT 1 FROM olt_dba_profiles WHERE username=? AND profile_id=?",
              (self.username, tcont["dba_profile_id"]),
          ).fetchone():
            conn.close()
            self.append_plain_text(f"\n  Failure: DBA profile {tcont['dba_profile_id']} does not exist.")
            return
        for gem in self.pending_lineprofile_gems:
          if gem["tcont_id"] not in configured_tconts:
            conn.close()
            self.append_plain_text(f"\n  Failure: GEM {gem['gem_id']} references an unconfigured T-CONT.")
            return
        gem_ids = {g["gem_id"] for g in self.pending_lineprofile_gems}
        if any(m["gem_id"] not in gem_ids for m in self.pending_lineprofile_mappings):
          conn.close()
          self.append_plain_text("\n  Failure: A GEM mapping references an unconfigured GEM port.")
          return
        cursor.execute(
            "INSERT OR REPLACE INTO olt_line_profiles (username, profile_id,"
            " profile_name, bind_count) VALUES (?, ?, ?, COALESCE((SELECT bind_count "
            "FROM olt_line_profiles WHERE username = ? AND profile_id = ?), 0))",
            (self.username, self.current_profile_id,
             self.current_line_profile_name, self.username, self.current_profile_id),
        )
        cursor.execute(
            "INSERT OR REPLACE INTO olt_line_profile_settings "
            "(username, profile_id, omcc_encrypt, tr069_enabled, tr069_ip_index, "
            "fec_upstream, qos_mode, mapping_mode, gem_encrypt_mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.username, self.current_profile_id,
             self.pending_lineprofile_settings["omcc_encrypt"],
             self.pending_lineprofile_settings["tr069_enabled"],
             self.pending_lineprofile_settings["tr069_ip_index"],
             self.pending_lineprofile_settings["fec_upstream"],
             self.pending_lineprofile_settings["qos_mode"],
             self.pending_lineprofile_settings["mapping_mode"],
             self.pending_lineprofile_settings["gem_encrypt_mode"]),
        )
        for table in ("olt_line_profile_tconts", "olt_line_profile_gems",
                      "olt_line_profile_mappings", "olt_line_profile_vlan_bundles"):
          cursor.execute(
              f"DELETE FROM {table} WHERE username = ? AND profile_id = ?",
              (self.username, self.current_profile_id),
          )
        cursor.executemany(
            "INSERT INTO olt_line_profile_tconts "
            "(username, profile_id, tcont_id, dba_profile_id) VALUES (?, ?, ?, ?)",
            [(self.username, self.current_profile_id, t["tcont_id"],
              t["dba_profile_id"]) for t in self.pending_lineprofile_tconts],
        )
        cursor.executemany(
            "INSERT INTO olt_line_profile_gems "
            "(username, profile_id, gem_id, service_type, tcont_id, encrypt, "
            "cascade, priority_queue, downstream_priority_queue, gem_car_index) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(self.username, self.current_profile_id, g["gem_id"],
              g["service_type"], g["tcont_id"], g["encrypt"], g["cascade"],
              g["priority_queue"], g["downstream_priority_queue"],
              g["gem_car_index"])
             for g in self.pending_lineprofile_gems],
        )
        cursor.executemany(
            "INSERT INTO olt_line_profile_mappings "
            "(username, profile_id, gem_id, mapping_id, match_type, match_value) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(self.username, self.current_profile_id, m["gem_id"],
              m["mapping_id"], m["match_type"], m["match_value"])
             for m in self.pending_lineprofile_mappings],
        )
        cursor.executemany(
            "INSERT INTO olt_line_profile_vlan_bundles "
            "(username, profile_id, bundle_id, vlan_list) VALUES (?, ?, ?, ?)",
            [(self.username, self.current_profile_id, b["bundle_id"], b["vlan_list"])
             for b in self.pending_lineprofile_vlan_bundles],
        )
        conn.commit()
        conn.close()
        self.append_plain_text(
            f"\n  Line profile {self.current_line_profile_name} committed successfully."
        )

    # 4. PERINTAH: ont-srvprofile gpon / ont-srv-profile gpon[cite: 15]
    elif self.cli_state == "CONFIG" and cmd_lower.startswith("undo ont-srvprofile gpon"):
      try:
        parts = shlex.split(cmd)
        if len(parts) != 5 or parts[3].lower() not in ("profile-id", "profile-name"):
          raise ValueError()
        selector, value = parts[3].lower(), parts[4]
        conn = get_connection()
        cursor = conn.cursor()
        if selector == "profile-id":
          profile_id = int(value)
        else:
          cursor.execute("SELECT profile_id FROM olt_service_profiles WHERE username = ? AND profile_name = ? COLLATE NOCASE", (self.username, value))
          found = cursor.fetchone()
          if not found: raise LookupError()
          profile_id = found[0]
        if profile_id == 0: raise PermissionError()
        cursor.execute("SELECT COUNT(*) FROM olt_onts WHERE username = ? AND service_profile_id = ?", (self.username, profile_id))
        if cursor.fetchone()[0]: raise RuntimeError()
        cursor.execute("DELETE FROM olt_service_profiles WHERE username = ? AND profile_id = ?", (self.username, profile_id))
        for table in ("olt_service_profile_port_vlans", "olt_service_profile_multicast", "olt_service_profile_settings"):
          cursor.execute(f"DELETE FROM {table} WHERE username = ? AND profile_id = ?", (self.username, profile_id))
        if cursor.rowcount == 0:
          pass
        conn.commit()
        conn.close()
        self.append_plain_text(f"\n  Command:\n          {cmd}\n  Service profile {profile_id} deleted.")
      except PermissionError:
        conn.close()
        self.append_plain_text("\n  Failure: Default service profile 0 cannot be deleted.")
      except RuntimeError:
        conn.close()
        self.append_plain_text("\n  Failure: This service profile is bound to an ONT and cannot be deleted.")
      except LookupError:
        conn.close()
        self.append_plain_text("\n  Failure: Service profile was not found.")
      except (ValueError, IndexError):
        try: conn.close()
        except UnboundLocalError: pass
        self.append_plain_text("\n  Failure: Use undo ont-srvprofile gpon {profile-id <ID>|profile-name <name>}.")

    elif self.cli_state == "CONFIG" and (
        cmd_lower.startswith("ont-srvprofile gpon")
        or cmd_lower.startswith("ont-srv-profile gpon")
    ):
      try:
        parts = shlex.split(cmd)
      except ValueError:
        self.append_plain_text("\n  Failure: Invalid quoted profile name.")
        self.print_prompt()
        return
      prof_id = None
      prof_name = None
      for i, part in enumerate(parts[:-1]):
        if part.lower() == "profile-id":
          try:
            prof_id = int(parts[i + 1])
          except ValueError:
            pass
        elif part.lower() == "profile-name":
          prof_name = parts[i + 1].strip('"')

      if prof_id is not None and not 0 <= prof_id <= 8192:
        self.append_plain_text("\n  Failure: profile-id must be in the range 0-8192.")
        self.print_prompt()
        return
      if prof_name is not None and not 1 <= len(prof_name) <= 32:
        self.append_plain_text("\n  Failure: profile-name length must be 1-32 characters.")
        self.print_prompt()
        return

      conn = get_connection()
      cursor = conn.cursor()
      row = None
      if prof_id is not None:
        cursor.execute(
            "SELECT profile_name, eth, pots, catv FROM olt_service_profiles "
            "WHERE username = ? AND profile_id = ?",
            (self.username, prof_id),
        )
        row = cursor.fetchone()
      elif prof_name:
        cursor.execute(
            "SELECT profile_id, profile_name, eth, pots, catv "
            "FROM olt_service_profiles WHERE username = ? AND profile_name = ?",
            (self.username, prof_name),
        )
        existing = cursor.fetchone()
        if existing:
          prof_id = existing[0]
          row = existing[1:]
        else:
          cursor.execute("SELECT profile_id FROM olt_service_profiles WHERE username = ?", (self.username,))
          used_ids = {item[0] for item in cursor.fetchall()}
          prof_id = next((candidate for candidate in range(1, 8193) if candidate not in used_ids), None)
          if prof_id is None:
            conn.close()
            self.append_plain_text("\n  Failure: No service profile ID is available.")
            self.print_prompt()
            return
      else:
        prof_id = 10
        cursor.execute(
            "SELECT profile_name, eth, pots, catv FROM olt_service_profiles "
            "WHERE username = ? AND profile_id = ?",
            (self.username, prof_id),
        )
        row = cursor.fetchone()
      cursor.execute(
          "SELECT port_type, port_id, vlan_mode, vlan_id, user_vlan "
          "FROM olt_service_profile_port_vlans "
          "WHERE username = ? AND profile_id = ? ORDER BY port_type, port_id",
          (self.username, prof_id),
      )
      vlan_rules = cursor.fetchall()
      cursor.execute(
          "SELECT forward_mode, translation_vlan "
          "FROM olt_service_profile_multicast "
          "WHERE username = ? AND profile_id = ?",
          (self.username, prof_id),
      )
      multicast_row = cursor.fetchone()
      conn.close()

      self.current_profile_id = prof_id
      self.current_service_profile_name = prof_name or (row[0] if row else None) or f"srv-profile_{prof_id}"
      self.pending_service_profile_data = (
          {"eth": row[1], "pots": row[2], "catv": row[3]}
          if row
          else {"eth": 0, "pots": 0, "catv": 0}
      )
      conn = get_connection()
      cursor = conn.cursor()
      cursor.execute("SELECT settings_json FROM olt_service_profile_settings WHERE username = ? AND profile_id = ?", (self.username, prof_id))
      settings_row = cursor.fetchone()
      conn.close()
      self.pending_service_settings = json.loads(settings_row[0]) if settings_row else {
          "ports": {}, "tdm_type": "E1", "tdm_srvtype": "TDMoGEM",
          "mac_learning": "enable", "transparent": "disable",
          "native_vlan": "concern", "qinq": {},
      }
      self.pending_service_vlan_rules = [
          {"port_type": r[0], "port_id": r[1], "vlan_mode": r[2],
           "vlan_id": r[3], "user_vlan": r[4]}
          for r in vlan_rules
      ]
      self.pending_multicast_forward = multicast_row
      self.cli_state = "SRVPROFILE"

      output = (
          f"{{ profile-id<U><0,8192> }}:{prof_id}\n"
          f"{{ <cr>|profile-name<K> }}:{self.current_service_profile_name}\n\n"
          "  Command:\n"
          f"          ont-srvprofile gpon profile-id {prof_id} "
          f"profile-name {self.current_service_profile_name}"
      )
      self.append_plain_text(output)

    # Sub-command Service Profile Mode (ont-port, commit)[cite: 15]
    elif self.cli_state == "SRVPROFILE":
      if cmd_lower.startswith("ont-port"):
        parts = shlex.split(cmd)
        ranges = {"pots": (0, 32), "eth": (0, 28), "vdsl": (0, 8), "tdm": (0, 8), "moca": (0, 8), "catv": (0, 8), "wifi": (0, 8)}
        ports = self.pending_service_settings.setdefault("ports", {})
        i = 1
        try:
          while i < len(parts):
            key = parts[i].lower()
            if key == "tdm-type":
              value = parts[i + 1].upper()
              if value not in ("E1", "T1"): raise ValueError("tdm-type must be E1 or T1")
              self.pending_service_settings["tdm_type"] = value
              i += 2; continue
            if key == "tdm-srvtype":
              value = parts[i + 1].upper()
              if value != "TDMOGEM": raise ValueError("tdm-srvtype supports TDMoGEM")
              self.pending_service_settings["tdm_srvtype"] = "TDMoGEM"
              i += 2; continue
            if key not in ranges: raise ValueError(f"unknown port type '{key}'")
            low, high = ranges[key]
            value = parts[i + 1].lower()
            if value == "adaptive":
              maximum = high
              if i + 2 < len(parts) and parts[i + 2].isdigit():
                maximum = int(parts[i + 2]); i += 1
              if not 1 <= maximum <= high: raise ValueError(f"adaptive {key} maximum must be 1-{high}")
              ports[key] = {"mode": "adaptive", "max": maximum}
              if key in self.pending_service_profile_data: self.pending_service_profile_data[key] = f"adaptive:{maximum}"
            else:
              count = int(value)
              if not low <= count <= high: raise ValueError(f"{key} must be 0-{high}")
              ports[key] = {"mode": "count", "count": count}
              if key in self.pending_service_profile_data: self.pending_service_profile_data[key] = count
            i += 2
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        except (ValueError, IndexError) as exc:
          self.append_plain_text(f"\n  Failure: {exc}.")

      elif cmd_lower.startswith("native-vlan"):
        parts = shlex.split(cmd)
        if len(parts) != 2 or parts[1].lower() not in ("concern", "unconcern"):
          self.append_plain_text("\n  Failure: Use native-vlan concern|unconcern.")
        elif parts[1].lower() == "unconcern" and any(v == "enable" for v in self.pending_service_settings.get("qinq", {}).values()):
          self.append_plain_text("\n  Failure: Native VLAN cannot be unconcern while QinQ is enabled.")
        else:
          self.pending_service_settings["native_vlan"] = parts[1].lower()
          self.append_plain_text(f"\n  Command:\n          {cmd}")

      elif cmd_lower.startswith("mac-learning") or cmd_lower.startswith("transparent"):
        parts = shlex.split(cmd)
        key = "mac_learning" if parts[0].lower() == "mac-learning" else "transparent"
        if len(parts) != 2 or parts[1].lower() not in ("enable", "disable"):
          self.append_plain_text(f"\n  Failure: Use {parts[0]} enable|disable.")
        else:
          self.pending_service_settings[key] = parts[1].lower()
          self.append_plain_text(f"\n  Command:\n          {cmd}")

      elif cmd_lower.startswith("port q-in-q"):
        parts = shlex.split(cmd)
        try:
          if len(parts) != 5 or parts[1].lower() not in ("eth", "vdsl"): raise ValueError()
          ptype, port_id, switch = parts[2].lower(), int(parts[3]), parts[4].lower()
          maximum = 28 if ptype == "eth" else 8
          if not 1 <= port_id <= maximum or switch not in ("enable", "disable", "unconcern"): raise ValueError()
          if switch == "enable" and self.pending_service_settings.get("native_vlan", "concern") != "concern":
            raise ValueError()
          self.pending_service_settings.setdefault("qinq", {})[f"{ptype}:{port_id}"] = switch
          self.append_plain_text(f"\n  Command:\n          {cmd}")
        except (ValueError, IndexError):
          self.append_plain_text("\n  Failure: Use port q-in-q {eth|vdsl} <port-id> {enable|disable|unconcern}.")

      elif cmd_lower.startswith("port vlan eth"):
        parts = cmd.split()
        lower_parts = [part.lower() for part in parts]
        try:
          port_id = int(parts[3])
          if "translation" in lower_parts:
            mode = "translation"
            vlan_id = int(parts[lower_parts.index("translation") + 1])
            user_vlan = int(parts[lower_parts.index("user-vlan") + 1])
          else:
            mode = "access"
            vlan_id = int(parts[4])
            user_vlan = None
          rule = {
              "port_type": "eth", "port_id": port_id, "vlan_mode": mode,
              "vlan_id": vlan_id, "user_vlan": user_vlan,
          }
          self.pending_service_vlan_rules = [
              r for r in self.pending_service_vlan_rules
              if (r["port_type"], r["port_id"]) != ("eth", port_id)
          ]
          self.pending_service_vlan_rules.append(rule)
          self.append_plain_text(
              f"\n  Command:\n          {cmd}\n  Command is being executed."
          )
        except (ValueError, IndexError):
          self.append_plain_text(
              "\n  Failure: Invalid port VLAN syntax. Example: "
              "port vlan eth 1 1001"
          )

      elif cmd_lower.startswith("multicast-forward"):
        parts = cmd.split()
        if len(parts) == 2 and parts[1].lower() == "unconcern":
          self.pending_multicast_forward = None
        elif len(parts) >= 2 and parts[1].lower() == "untag":
          self.pending_multicast_forward = ("untag", None)
        elif len(parts) >= 4 and parts[1].lower() == "tag" and parts[2].lower() == "translation":
          try:
            self.pending_multicast_forward = ("tag-translation", int(parts[3]))
          except ValueError:
            self.append_plain_text("\n  Failure: Invalid multicast VLAN.")
            self.print_prompt()
            return
        else:
          self.append_plain_text(
              "\n  Failure: Use multicast-forward untag or "
              "multicast-forward tag translation <VLAN>."
          )
          self.print_prompt()
          return
        self.append_plain_text(f"\n  Command:\n          {cmd}")

      elif cmd_lower == "commit":
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO olt_service_profiles (username, profile_id,"
            " profile_name, eth, pots, catv) VALUES (?, ?, ?, ?, ?, ?)",
            (
                self.username,
                self.current_profile_id,
                self.current_service_profile_name,
                self.pending_service_profile_data["eth"],
                self.pending_service_profile_data["pots"],
                self.pending_service_profile_data["catv"],
            ),
        )
        cursor.execute(
            "DELETE FROM olt_service_profile_port_vlans "
            "WHERE username = ? AND profile_id = ?",
            (self.username, self.current_profile_id),
        )
        cursor.executemany(
            "INSERT INTO olt_service_profile_port_vlans "
            "(username, profile_id, port_type, port_id, vlan_mode, vlan_id, user_vlan) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (self.username, self.current_profile_id, rule["port_type"],
                 rule["port_id"], rule["vlan_mode"], rule["vlan_id"],
                 rule["user_vlan"])
                for rule in self.pending_service_vlan_rules
            ],
        )
        cursor.execute(
            "DELETE FROM olt_service_profile_multicast "
            "WHERE username = ? AND profile_id = ?",
            (self.username, self.current_profile_id),
        )
        if self.pending_multicast_forward:
          cursor.execute(
              "INSERT INTO olt_service_profile_multicast "
              "(username, profile_id, forward_mode, translation_vlan) "
              "VALUES (?, ?, ?, ?)",
              (self.username, self.current_profile_id,
               self.pending_multicast_forward[0],
               self.pending_multicast_forward[1]),
          )
        cursor.execute(
            "INSERT OR REPLACE INTO olt_service_profile_settings "
            "(username, profile_id, settings_json) VALUES (?, ?, ?)",
            (self.username, self.current_profile_id,
             json.dumps(self.pending_service_settings, separators=(",", ":"))),
        )
        conn.commit()
        conn.close()
        self.append_plain_text(
            f"\n  Service profile {self.current_service_profile_name} "
            f"(ID {self.current_profile_id}) committed successfully."
        )

    elif (
        self.cli_state in ("ENABLE", "INTERFACE_GPON")
        and cmd_lower.startswith("display ont-srvprofile gpon")
    ):
      self.append_plain_text(build_ont_srvprofile_display(self.username, cmd))

    elif self.cli_state == "SRVPROFILE" and cmd_lower == "display ont-srvprofile current":
      self.append_plain_text(build_current_srvprofile_display(
          self.current_profile_id, self.current_service_profile_name,
          self.pending_service_profile_data, self.pending_service_settings))

    elif cmd_lower.startswith("display ont-srvprofile gpon"):
      self.append_plain_text(
          "\n  Failure: Run this command in privilege mode or GPON mode."
      )

    # 5. PERINTAH: port <portid> ont-auto-find enable/disable
    elif (
        self.cli_state == "INTERFACE_GPON"
        and "ont-auto-find" in cmd_lower
    ):
      port_id = cmd.split()[1] if len(cmd.split()) >= 2 else "0"
      if "enable" in cmd_lower:
        self.autofind_enabled_ports.add(port_id)
        self.append_plain_text(
            f"\n  Command:\n          port {port_id} ont-auto-find enable"
        )
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT sn FROM olt_onts WHERE username = ? LIMIT 1",
            (self.username,),
        )
        row = cursor.fetchone()
        if row:
          sn = row[0]
          warning_msg = (
              f"\n! RUNNING WARNING 2026-10-16 17:00:00+08:00\n  EVENT NAME"
              " :The GPON ONT is discovered by the OLT\n  PARAMETERS"
              f" :FrameID: 0, SlotID: 1, PortID: {port_id}, Serial Number: {sn},"
              " Password: 0x0000000000000000, Loid: , Checkcode:\n"
          )
          self.append_plain_text(warning_msg)
          cursor.execute(
              "UPDATE olt_onts SET autofind_state = 1 WHERE username = ?",
              (self.username,),
          )
          conn.commit()
        conn.close()
      elif "disable" in cmd_lower:
        self.autofind_enabled_ports.discard(port_id)
        self.append_plain_text(
            f"\n  Command:\n          port {port_id} ont-auto-find disable"
        )

    # 6. PERINTAH: ont add <portid> <ontid> sn-auth <sn> ...[cite: 16, 18]
    elif self.cli_state == "INTERFACE_GPON" and cmd_lower.startswith("ont add"):
      parts = cmd.split()
      port_id = parts[2] if len(parts) > 2 else "0"
      ont_id = parts[3] if len(parts) > 3 else "1"

      sn_val = None
      if "sn-auth" in parts:
        sn_idx = parts.index("sn-auth") + 1
        if sn_idx < len(parts):
          sn_val = parts[sn_idx]

      if not sn_val:
        self.append_plain_text(
            f"\n  Command:\n          {cmd}\n  Failure: Invalid parameters"
        )
      else:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ont_id FROM olt_onts WHERE username = ? AND sn = ? AND"
            " control_flag = 'active'",
            (self.username, sn_val),
        )
        exists = cursor.fetchone()

        if exists:
          self.append_plain_text(
              f"\n  Command:\n          {cmd}\n  Failure: SN already exists"
          )
        else:
          previous = cursor.execute(
              "SELECT ont_name, description FROM olt_onts"
              " WHERE username = ? AND sn = ?",
              (self.username, sn_val),
          ).fetchone()
          cursor.execute(
              "DELETE FROM olt_onts WHERE username = ? AND sn = ?",
              (self.username, sn_val),
          )
          cursor.execute(
              "INSERT INTO olt_onts (username, port, ont_id, sn,"
              " control_flag, run_state, config_state, match_state,"
              " autofind_state, ont_name, description) VALUES (?, ?, ?, ?,"
              " 'active', 'offline', 'normal', 'match', 1, ?, ?)",
              (
                  self.username,
                  f"0/1/{port_id}",
                  int(ont_id),
                  sn_val,
                  previous[0] if previous else None,
                  previous[1] if previous else None,
              ),
          )
          conn.commit()
          self.append_plain_text(
              f"\n  Command:\n          {cmd}\n  Number of ONTs that can be"
              f" added: 1, success: 1\n  PortID :{port_id}, ONTID :{ont_id}"
          )
        conn.close()

    # 7. PERINTAH: display ont info <portid> all / <ontid>[cite: 17]
    elif self.cli_state == "INTERFACE_GPON" and cmd_lower.startswith(
        "display ont info"
    ):
      ont_status.refresh_ont_states(self.username, self.node_name)
      parts = cmd.split()
      port_id = parts[3] if len(parts) > 3 else "0"

      conn = get_connection()
      cursor = conn.cursor()
      cursor.execute(
          "SELECT ont_id, sn, control_flag, run_state, config_state,"
          " match_state FROM olt_onts WHERE username = ? AND control_flag ="
          " 'active'",
          (self.username,),
      )
      rows = cursor.fetchall()
      conn.close()

      if rows:
        lines = [
            f"\n  Command:\n          {cmd}",
            "  -------------------------------------------------------------------",
            "  F/S/P       ONT ID   SN                Control flag  Run state "
            " Config state  Match state  Protect side",
            "  -------------------------------------------------------------------",
        ]
        for r in rows:
          lines.append(
              f"  0/1/{port_id}    {str(r[0] or '-'):<8} {(r[1] or '-'):<17}"
              f" {(r[2] or '-'):<13} {(r[3] or '-'):<10}"
              f" {(r[4] or '-'):<13} {(r[5] or '-'):<12} no"
          )
        lines.extend([
            "  -------------------------------------------------------------------",
            "  F/S/P       ONT-ID   Description",
            "  -------------------------------------------------------------------",
        ])
        for r in rows:
          lines.append(f"  0/1/{port_id}    {r[0]:<8} ONT_NO_DESCRIPTION")
        lines.extend([
            "  -------------------------------------------------------------------",
            f"  In port 0/1/{port_id} , the total of ONTs are: {len(rows)},"
            f" online: {len(rows)}",
        ])
        self.append_plain_text("\n".join(lines))
      else:
        self.append_plain_text(
            f"\n  Command:\n          {cmd}\n  In port 0/1/{port_id} , the"
            " total of ONTs are: 0, online: 0"
        )

    # 8. PERINTAH: display ont autofind
    elif "display ont autofind" in cmd_lower:
      tokens = cmd.split()
      port_id = tokens[3] if len(tokens) > 3 else "0"
      full_port = f"{self.current_gpon_port}/{port_id}"
      if port_id.lower() != "all" and full_port not in self.autofind_enabled_ports:
        self.append_plain_text(
            f"\n  Command:\n          {cmd}\n  Failure: Enable ont-auto-find on"
            f" this port first."
        )
      else:
        entries = self._autofind_entries(port_id)
        lines = [f"\n  Command:\n          {cmd}", "  -------------------------------------------------------------------"]
        find_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for n, (entry_port, sn, _node) in enumerate(entries, 1):
          lines.extend([
              f"  Number              : {n}",
              f"  F/S/P               : {entry_port}",
              f"  Ont SN              : {sn}",
              "  VendorID            : HWTC",
              "  Ont Version         : 120A0000",
              "  Ont SoftwareVersion : V1R001C01",
              "  Ont EquipmentID     : G-240W-F",
              f"  Ont autofind time   : {find_time}",
              "  -------------------------------------------------------------------",
          ])
        if not entries:
          lines.append("  Failure: The automatically found ONTs do not exist.")
        self.append_plain_text("\n".join(lines))

    else:
      self.append_plain_text(
          "\n  ^\n  Error: Unrecognized command found at '^' position."
      )

    self.print_prompt()
