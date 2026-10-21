"""PITP command handling and per-OLT configuration."""

from __future__ import annotations

import re
from datetime import datetime

from modules.database import get_connection

GLOBAL_KEY = "pitp_global"


def ensure_schema():
  conn = get_connection()
  try:
    conn.execute("""CREATE TABLE IF NOT EXISTS pitp_bindings (
      username TEXT NOT NULL, pppoe_user TEXT NOT NULL, access_port TEXT NOT NULL,
      node_name TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL DEFAULT '',
      PRIMARY KEY (username, pppoe_user))""")
    conn.commit()
  finally:
    conn.close()


ensure_schema()


def _get(conn, username, key):
  row = conn.execute("SELECT value FROM olt_system_config WHERE username=? AND key=?", (username, key)).fetchone()
  return row[0] if row else None


def _set(conn, username, key, value):
  conn.execute("INSERT OR REPLACE INTO olt_system_config (username,key,value) VALUES (?,?,?)", (username, key, value))


def state(username):
  conn = get_connection()
  try:
    value = (_get(conn, username, GLOBAL_KEY) or "disable:pmode").lower()
  finally:
    conn.close()
  action, _, mode = value.partition(":")
  if action not in ("enable", "disable", "forward", "rebuild"):
    action, mode = "disable", "pmode"
  return {"enabled": action == "enable", "action": action, "mode": mode or "pmode", "raw": value}


def port_enabled(username, fsp):
  conn = get_connection()
  try:
    value = _get(conn, username, f"pitp_port_{fsp.replace('/', '_')}")
    if value is None:
      value = _get(conn, username, f"pitp_board_{'_'.join(fsp.split('/')[:2])}")
    return (value or "enable").lower() == "enable"
  finally:
    conn.close()


def bind(username, pppoe_user, access_port, node_name=""):
  current = state(username)
  if not current["enabled"] or not port_enabled(username, access_port.split(":")[0]):
    return True, ""
  conn = get_connection()
  try:
    row = conn.execute("SELECT access_port FROM pitp_bindings WHERE username=? AND pppoe_user=?", (username, pppoe_user)).fetchone()
    if row and row[0] != access_port:
      return False, f"PITP: akun '{pppoe_user}' sudah terikat ke port {row[0]} (PITP {current['mode']})."
    conn.execute("INSERT OR REPLACE INTO pitp_bindings (username,pppoe_user,access_port,node_name,updated) VALUES (?,?,?,?,?)",
                 (username, pppoe_user, access_port, node_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
  finally:
    conn.close()
  return True, f"PITP bound {pppoe_user} -> {access_port}"


def _store(username, key, value):
  conn = get_connection()
  try:
    _set(conn, username, key, value)
    conn.commit()
  finally:
    conn.close()


def display(username, cmd):
  conn = get_connection()
  try:
    rows = conn.execute("SELECT key,value FROM olt_system_config WHERE username=? AND key LIKE 'pitp_%' ORDER BY key", (username,)).fetchall()
    bindings = conn.execute("SELECT pppoe_user,access_port,updated FROM pitp_bindings WHERE username=? ORDER BY pppoe_user", (username,)).fetchall()
  finally:
    conn.close()
  current = state(username)
  lines = ["", "  Command:", f"          {cmd}", f"  Global PITP          : {current['action'].upper()} ({current['mode']})", "  Configuration:"]
  lines += [f"  {key[5:]:<38} : {value}" for key, value in rows]
  lines += ["  ------------------------------------------------------------------", "  PPPoE user        Access port         Updated"]
  lines += [f"  {u:<17} {p:<19} {t}" for u, p, t in bindings]
  if not bindings:
    lines.append("  (belum ada binding)")
  lines.append("  ------------------------------------------------------------------")
  return "\n".join(lines)


def handle_command(username, cmd, node_name="", mode=None, profile_id=None):
  text = " ".join(cmd.strip().split())
  low = text.lower()
  if low.startswith("display pitp"):
    return display(username, cmd)

  # VLAN service-profile mode command, committed explicitly by `commit`.
  if mode == "vlan-srvprofile":
    match = re.fullmatch(r"pitp (enable|disable)", text, re.I)
    if match:
      if profile_id is None:
        return "  Failure: VLAN service profile context is missing."
      _store(username, f"pitp_vlan_profile_pending_{profile_id}", match.group(1).lower())
      return f"\n  Command:\n          {cmd}\n  Info: Please use the commit command to make modifications take effect"

  match = re.fullmatch(r"pitp (enable|disable)(?: (pmode|vmode))?", text, re.I)
  if match:
    action, pitp_mode = match.groups()
    action = action.lower()
    if action == "enable" and not pitp_mode:
      return "  Failure: Use pitp enable {pmode|vmode}."
    pitp_mode = (pitp_mode or "pmode").lower()
    _store(username, GLOBAL_KEY, f"{action}:{pitp_mode}")
    return f"\n  Command:\n          {cmd}\n  Info: PITP {'enabled' if action == 'enable' else 'disabled'}{' ('+pitp_mode+')' if action == 'enable' else ''}.success"

  match = re.fullmatch(r"pitp (forward|rebuild) pmode", text, re.I)
  if match:
    action = match.group(1).lower()
    _store(username, GLOBAL_KEY, f"{action}:pmode")
    return f"\n  Command:\n          {cmd}\n  Info: PITP {action} pmode.success"

  match = re.fullmatch(r"pitp vmode ether-type (0x[0-9a-f]+|\d+)", text, re.I)
  if match:
    value = int(match.group(1), 0)
    if not 0 <= value <= 65535:
      return "  Failure: Ether-type must be in range 0-65535."
    _store(username, "pitp_vmode_ether_type", f"0x{value:04x}")
    return f"\n  Command:\n          {cmd}"

  match = re.fullmatch(r"pitp (?:permit-forwarding )?service-port (\d+) (enable|disable)", text, re.I)
  if match:
    sp_id, action = match.groups()
    key = "pitp_service_port_forward_" if "permit-forwarding" in low else "pitp_service_port_"
    _store(username, key + sp_id, action.lower())
    return f"\n  Command:\n          {cmd}\n  Info: PITP service-port {sp_id} {action.lower()}.success"

  match = re.fullmatch(r"pitp port (\d+/\d+/\d+) epon ont (\d+) (enable|disable)", text, re.I)
  if match:
    fsp, ont, action = match.groups()
    key = f"pitp_epon_ont_{fsp.replace('/', '_')}_{ont}"
    _store(username, key, action.lower())
    return f"\n  Command:\n          {cmd}\n  Info: PITP {action.lower()} for {fsp} epon ont {ont}."

  match = re.fullmatch(r"pitp port (\d+/\d+/\d+) ont (\d+) gemport (\d+) (enable|disable)", text, re.I)
  if match:
    fsp, ont, gem, action = match.groups()
    if int(gem) > 1023:
      return "  Failure: gemport-index must be in range 0-1023."
    _store(username, f"pitp_gpon_ont_{fsp.replace('/', '_')}_{ont}_{gem}", action.lower())
    return f"\n  Command:\n          {cmd}\n  Info: PITP {action.lower()} for {fsp} ont {ont} gemport {gem}."

  match = re.fullmatch(r"pitp port (\d+/\d+/\d+) (enable|disable)", text, re.I)
  if match:
    fsp, action = match.groups()
    _store(username, f"pitp_port_{fsp.replace('/', '_')}", action.lower())
    return f"\n  Command:\n          {cmd}\n  Info: PITP {action.lower()} on port {fsp}."

  match = re.fullmatch(r"pitp board (\d+/\d+) (enable|disable)", text, re.I)
  if match:
    board, action = match.groups()
    _store(username, f"pitp_board_{board.replace('/', '_')}", action.lower())
    return f"\n  Command:\n          {cmd}\n  Info: PITP {action.lower()} on board {board}."

  if low.startswith("pitp"):
    return "  Failure: Use pitp {enable pmode|enable vmode|disable|forward pmode|rebuild pmode|port|board|service-port} ... ."
  return None
