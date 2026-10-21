"""Engine perintah CLI untuk REST API (tanpa PyQt).

Menjalankan perintah yang sama dengan CLI terminal dengan memakai ulang
handler di ``modules.commands`` dan ``modules.commands.extended``.
"""

from __future__ import annotations

import contextlib
import io
import re
import threading

from modules.commands import bpdu, eth_ports, extended, ont_ports, pitp, provision, vlan_info
from modules.commands.board import handle_board_confirm, handle_display_board
from modules.commands.dba import (
    DBACommandError,
    execute_dba_command,
    needs_multiburst_confirmation,
)
from modules.commands.line_profile import (
    handle_display_line_profile,
    handle_line_profile_creation,
    handle_line_profile_submode,
    handle_undo_line_profile,
)
from modules.commands.ont import (
    handle_display_ont_info,
    handle_display_ont_optical,
    handle_ont_add,
)
from modules.commands.service_port import (
    handle_display_service_port,
    handle_service_port_add,
)
from modules.commands.system_config import (
    handle_display_system,
    handle_system_config,
)
from modules.commands.vlan import (
    handle_display_current_configuration_vlanif,
    handle_display_interface_vlanif,
    handle_display_ip_routing_table,
    handle_display_vlan,
    handle_eth_interface,
    handle_ip_route_static,
    handle_port_vlan_bind,
    handle_undo_vlan,
    handle_vlan_create,
    handle_vlanif_interface,
    handle_vlanif_submode,
)
from modules.database import get_connection, reset_user_data, save_user_configuration
from modules import services

from api import store

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

STATE_TO_EXTENDED = {
    "user": "USER",
    "privileged": "ENABLE",
    "config": "CONFIG",
    "interface": "INTERFACE_GPON",
    "eth_interface": "INTERFACE_ETH",
    "vlanif_interface": "VLANIF",
    "lineprofile": "LINEPROFILE",
}


class CommandError(Exception):
  """Perintah tidak valid / tidak didukung lewat API."""


def _capture(func, *args):
  """Jalankan handler yang mencetak ke stdout dan kembalikan teksnya."""
  buf = io.StringIO()
  with contextlib.redirect_stdout(buf):
    result = func(*args)
  text = ANSI.sub("", buf.getvalue())
  if isinstance(result, str) and result.strip():
    text = f"{text}\n{result}".strip()
  return text.strip()


class Session:
  """Sesi CLI: menyimpan mode dan konteks interface."""

  def __init__(self, username="admin", node_name="Huawei_1", sysname=None):
    self.username = username
    self.node_name = node_name
    self.sysname = sysname or node_name
    self.mode = "user"
    self.active_interface = None
    self.active_eth_interface = None
    self.active_vlanif_id = None
    self.active_lineprofile_id = None
    self.active_srvprofile_id = None
    self.lock = threading.Lock()

  # ------------------------------------------------------------------ #
  def prompt(self):
    name = self.sysname or self.node_name
    if self.mode == "user":
      return f"{name}>"
    if self.mode == "privileged":
      return f"{name}#"
    if self.mode == "config":
      return f"{name}(config)#"
    if self.mode == "interface":
      return f"{name}(config-if-gpon-{self.active_interface})#"
    if self.mode == "eth_interface":
      return f"{name}(config-if-eth-{self.active_eth_interface})#"
    if self.mode == "vlanif_interface":
      return f"{name}(config-if-vlanif{self.active_vlanif_id})#"
    if self.mode == "lineprofile":
      return f"{name}(config-gpon-lineprofile-{self.active_lineprofile_id})#"
    if self.mode == "srvprofile":
      return f"{name}(config-gpon-srvprofile-{self.active_srvprofile_id})#"
    return f"{name}>"

  # ------------------------------------------------------------------ #
  def run(self, command, confirm=False):
    """Jalankan satu perintah; kembalikan dict hasil."""
    cmd = " ".join(str(command).strip().split())
    with self.lock:
      try:
        output = self._dispatch(cmd, confirm)
        failed = bool(
            re.search(r"(?m)^\s*\^\s*$", output or "")
            or "  Error:" in (output or "")
            or "  Failure:" in (output or "")
        )
        return {
            "ok": not failed,
            "command": cmd,
            "mode": self.mode,
            "prompt": self.prompt(),
            "output": output,
            **({"error": "Perintah ditolak perangkat."} if failed else {}),
        }
      except CommandError as exc:
        return {
            "ok": False,
            "command": cmd,
            "mode": self.mode,
            "prompt": self.prompt(),
            "output": str(exc),
            "error": str(exc),
        }
      except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "command": cmd,
            "mode": self.mode,
            "prompt": self.prompt(),
            "output": f"Failure: {type(exc).__name__}: {exc}",
            "error": f"{type(exc).__name__}: {exc}",
        }

  def _port_vlan(self, cmd):
    """port vlan <vlan-list> <frame/slot> <port> — catat membership + bind."""
    tokens = cmd.split()
    text = _capture(handle_port_vlan_bind, self.username, cmd, self.mode)
    if len(tokens) >= 5:
      vlan_token = tokens[2]
      port = f"{tokens[3]}/{tokens[4]}"
      conn = get_connection()
      try:
        for part in vlan_token.split(","):
          part = part.strip()
          if not part.isdigit():
            continue
          conn.execute(
              "INSERT OR IGNORE INTO olt_node_vlan_ports"
              " (username, node_name, vlan_id, port) VALUES (?, ?, ?, ?)",
              (self.username, self.node_name, int(part), port),
          )
        conn.commit()
      finally:
        conn.close()
    return text

  # ------------------------------------------------------------------ #
  def _autofind(self, cmd, low):
    """display ont autofind [port|all] — dihitung dari topologi fisik."""
    tokens = low.split()
    selector = tokens[3] if len(tokens) > 3 else "all"
    entries = store.autofind(self.node_name, self.username)
    if selector != "all":
      entries = [
          entry
          for entry in entries
          if entry["fsp"] == selector or entry["fsp"].endswith(f"/{selector}")
      ]

    lines = [
        f"\n  Command:\n          {cmd}",
        "  -------------------------------------------------------------------",
    ]
    for number, entry in enumerate(entries, 1):
      lines.extend([
          f"  Number              : {number}",
          f"  F/S/P               : {entry['fsp']}",
          f"  Ont SN              : {entry['sn']}",
          f"  VendorID            : {entry['vendor']}",
          f"  Ont Version         : {entry['version']}",
          f"  Ont SoftwareVersion : {entry['software']}",
          f"  Ont EquipmentID     : {entry['equipment']}",
          f"  Ont autofind time   : {entry['found_at']}",
          "  -------------------------------------------------------------------",
      ])
    if not entries:
      lines.append("  Failure: The automatically found ONTs do not exist.")
    return "\n".join(lines)

  # ------------------------------------------------------------------ #
  def _dispatch(self, cmd, confirm):
    if not cmd:
      raise CommandError("Empty command.")
    low = cmd.lower()

    # variasi penulisan seperti CLI asli: display ont-auto-find / ont auto-find
    if low.startswith(("display ont-auto-find", "display ont auto-find")):
      cmd = re.sub(
          r"(?i)^display\s+ont[-\s]?auto[-\s]?find", "display ont autofind", cmd
      )
      cmd = " ".join(cmd.split())
      low = cmd.lower()

    parts = cmd.split()

    # --- keluar dari mode ---
    if low in ("quit", "exit", "return", "end"):
      if self.mode == "lineprofile":
        self.mode, self.active_lineprofile_id = "config", None
      elif self.mode == "srvprofile":
        self.mode, self.active_srvprofile_id = "config", None
      elif self.mode == "vlanif_interface":
        self.mode, self.active_vlanif_id = "config", None
      elif self.mode == "interface":
        self.mode, self.active_interface = "config", None
      elif self.mode == "eth_interface":
        self.mode, self.active_eth_interface = "config", None
      elif self.mode == "config":
        self.mode = "privileged"
      elif self.mode == "privileged":
        self.mode = "user"
      return ""

    if low == "disable":
      if self.mode not in ("privileged", "config"):
        raise CommandError("Failure: disable is available in privilege or global config mode.")
      self.mode = "user"
      return ""

    # --- transisi mode ---
    if self.mode == "user" and low == "enable":
      self.mode = "privileged"
      return ""
    if self.mode == "privileged" and low in ("config", "configure", "configure terminal"):
      self.mode = "config"
      return ""
    if self.mode == "user" and low in ("config", "configure", "configure terminal"):
      raise CommandError("Failure: Enter privilege mode with 'enable' first.")

    if self.mode == "config" and low.startswith("interface gpon"):
      if len(parts) < 3:
        raise CommandError("Failure: Invalid interface specification.")
      self.active_interface = parts[2]
      self.mode = "interface"
      return ""

    if self.mode == "config" and low.startswith("interface eth"):
      (mode, target), text = _capture_pair(
          handle_eth_interface, self.username, cmd, self.mode
      )
      if mode:
        self.mode, self.active_eth_interface = mode, target
      return text

    if self.mode == "config" and low.startswith("interface vlanif"):
      (mode, target), text = _capture_pair(
          handle_vlanif_interface, self.username, cmd, self.mode
      )
      if mode:
        self.mode, self.active_vlanif_id = mode, target
      return text

    if self.mode == "config" and low.startswith(("ont-lineprofile gpon", "ont-line-profile gpon")):
      (mode, pid), text = _capture_pair(
          handle_line_profile_creation, self.username, cmd, self.mode
      )
      if mode:
        self.mode, self.active_lineprofile_id = mode, pid
      return text

    if self.mode == "config" and low.startswith(("ont-srvprofile gpon", "ont-srv-profile gpon")):
      profile_id, name = provision.create_srvprofile(self.username, cmd)
      self.active_srvprofile_id = profile_id
      self.mode = "srvprofile"
      return f"  Service profile {name} (ID {profile_id}) created."

    # --- sub-mode ---
    if self.mode == "vlanif_interface":
      return _capture(
          handle_vlanif_submode, self.username, cmd, self.active_vlanif_id
      )

    if self.mode == "lineprofile":
      return provision.lineprofile_submode(
          self.username, cmd, self.active_lineprofile_id
      )

    if self.mode == "srvprofile":
      return provision.srvprofile_submode(
          self.username, cmd, self.active_srvprofile_id
      )

    if self.mode == "config" and low.startswith("bpdu tunnel vlan"):
      return bpdu.configure(self.username, cmd)
    if low.startswith("display bpdu tunnel"):
      if self.mode != "privileged":
        return "Failure: Run display bpdu tunnel in privilege mode."
      if low == "display bpdu tunnel config":
        return bpdu.display_config(self.username, cmd)
      if low.startswith("display bpdu tunnel vlan"):
        return bpdu.display_vlan(self.username, cmd)
      return "Failure: Use display bpdu tunnel {config|vlan <VLAN-ID> [dedicated-net-id <1-8>]}."

    # --- PITP (binding akun PPPoE ke port akses) ---
    pitp_output = pitp.handle_command(self.username, cmd, self.node_name, self.mode)
    if pitp_output is not None:
      return pitp_output

    # --- native VLAN / access port ---
    ont_port_output = ont_ports.handle_command(
        self.username, cmd, self.active_interface or "0/1", self.node_name
    )
    if ont_port_output is not None:
      return ont_port_output
    if low.startswith("display native-vlan"):
      return eth_ports.display_native_vlans(self.username, cmd)
    if self.mode == "eth_interface" and low.startswith(
        ("native-vlan", "undo native-vlan")
    ):
      return eth_ports.native_vlan(
          self.username,
          self.node_name,
          self.active_eth_interface or "0/2",
          cmd,
      )

    # --- layanan DHCP/PPPoE (WAN ONT) ---
    service_output = services.handle_command(
        self.username,
        self.node_name,
        cmd,
        STATE_TO_EXTENDED.get(self.mode, "ENABLE"),
        self.active_interface or "0/1",
    )
    if service_output is not None:
      return service_output

    # --- perintah tambahan (version, config, routing, lldp, port, ont ops) ---
    extra = extended.dispatch(
        self.username,
        self.node_name,
        cmd,
        STATE_TO_EXTENDED.get(self.mode, "ENABLE"),
        self.active_interface or "0/1",
    )
    if extra is not None:
      return extra

    # --- display / konfigurasi lain ---
    if low.startswith("display board"):
      return _capture(handle_display_board, self.username, cmd)
    if low.startswith("board confirm"):
      return _capture(handle_board_confirm, self.username, cmd, self.mode)

    if low.startswith("display dba-profile") or low.startswith("dba-profile "):
      if low.startswith("dba-profile add") and needs_multiburst_confirmation(cmd) and not confirm:
        raise CommandError(
            "Perintah memakai single-frame multiburst. Kirim ulang dengan `confirm: true`."
        )
      try:
        return _capture(execute_dba_command, self.username, cmd, self.mode)
      except DBACommandError as exc:
        raise CommandError(f"Failure: {exc}") from exc

    if low.startswith("display ont-lineprofile gpon"):
      return _capture(handle_display_line_profile, self.username, cmd)
    if low.startswith("undo ont-lineprofile gpon"):
      return _capture(handle_undo_line_profile, self.username, cmd, self.mode)

    if low.startswith("display ont autofind"):
      return self._autofind(cmd, low)
    if low.startswith("display ont-srvprofile gpon"):
      return provision.display_srvprofile(self.username, cmd)
    if low.startswith("display ont info"):
      return provision.display_ont_info(
          self.username, cmd, self.active_interface or "0/1", self.node_name
      )
    if low.startswith("display ont optical-info"):
      return _capture(handle_display_ont_optical, self.username, cmd)
    if low.startswith("ont add"):
      if self.mode != "interface":
        raise CommandError(
            "Failure: Masuk ke 'interface gpon 0/1' dulu sebelum 'ont add'."
        )
      return provision.ont_add(
          self.username, cmd, self.active_interface or "0/1", self.node_name
      )

    if low.startswith("display service-port"):
      return _capture(handle_display_service_port, self.username, cmd)
    if low.startswith("service-port"):
      return provision.service_port(self.username, cmd)

    if low.startswith("vlan "):
      return _capture(handle_vlan_create, self.username, cmd, self.mode)
    if low.startswith("port vlan"):
      return self._port_vlan(cmd)
    if low.startswith(("undo vlan", "undo port vlan")):
      return _capture(handle_undo_vlan, self.username, cmd, self.mode)
    if low.startswith("display interface vlanif"):
      return _capture(handle_display_interface_vlanif, self.username, cmd)
    if low.startswith("display current-configuration interface vlanif"):
      return _capture(handle_display_current_configuration_vlanif, self.username, cmd)
    if low.startswith("display port vlan"):
      return vlan_info.display_port_vlan(self.username, self.node_name, cmd)
    if low.startswith("display vlan"):
      return vlan_info.display_vlan(self.username, cmd, self.node_name)
    if low.startswith("display ip routing-table"):
      return _capture(handle_display_ip_routing_table, self.username, cmd)
    if low.startswith("ip route-static"):
      return _capture(handle_ip_route_static, self.username, cmd, self.mode)

    if low.startswith(("clock timezone", "ntp-service", "snmp-agent", "sysman service", "sysman server")):
      return _capture(handle_system_config, self.username, cmd, self.mode)
    if low.startswith(("display clock", "display ntp-service", "display snmp-agent", "display sysman server")):
      return _capture(handle_display_system, self.username, cmd)

    if low == "save":
      if self.mode != "privileged":
        raise CommandError("Failure: Run this command in privilege mode.")
      save_user_configuration(self.username)
      return (
          "It will take several minutes to save configuration file, please wait...\n"
          "Configuration file had been saved successfully.\n"
          "System configuration data has been saved completely."
      )

    if low == "reset":
      if not confirm:
        raise CommandError("Perintah reset butuh konfirmasi. Kirim ulang dengan `confirm: true`.")
      reset_user_data(self.username)
      return f"Konfigurasi akun [{self.username}] telah direset ke awal."

    raise CommandError(
        "  ^\n  Error: Unrecognized command found at '^' position."
    )


def _capture_pair(func, *args):
  """Untuk handler yang mengembalikan (mode, target); teks error tetap dibawa."""
  buf = io.StringIO()
  with contextlib.redirect_stdout(buf):
    result = func(*args)
  text = ANSI.sub("", buf.getvalue()).strip()
  pair = result if isinstance(result, tuple) else (None, None)
  return pair, text


SESSIONS: dict[str, Session] = {}
_SESSIONS_LOCK = threading.Lock()


def get_session(session_id, username="admin", node_name="Huawei_1", sysname=None):
  with _SESSIONS_LOCK:
    session = SESSIONS.get(session_id)
    if session is None:
      session = Session(username=username, node_name=node_name, sysname=sysname)
      SESSIONS[session_id] = session
    return session


def drop_session(session_id):
  with _SESSIONS_LOCK:
    SESSIONS.pop(session_id, None)
