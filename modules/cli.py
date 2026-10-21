import os
import sys
from colorama import Fore, Style
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
    handle_display_ont_autofind,
    handle_display_ont_info,
    handle_display_ont_optical,
    handle_ont_add,
)
from modules.commands.service_port import (
    handle_display_service_port,
    handle_service_port_add,
)

from modules.commands.vlan import (
    handle_display_current_configuration_vlanif,
    handle_display_interface_vlanif,
    handle_display_ip_routing_table,  # <-- Tambahkan ini
    handle_display_vlan,
    handle_eth_interface,
    handle_ip_route_static,          # <-- Tambahkan ini
    handle_port_vlan_bind,
    handle_undo_vlan,
    handle_vlan_create,
    handle_vlanif_interface,
    handle_vlanif_submode,
)

from modules.commands.system_config import (
    handle_display_system,
    handle_system_config,
)

from modules.database import get_connection, init_user_data, save_user_configuration
from modules.commands.extended import dispatch as dispatch_extended


def clear_screen():
  os.system("cls" if os.name == "nt" else "clear")


def run_simulator(username):
  """Looping Utama Terminal CLI Huawei OLT"""
  clear_screen()
  print(
      Fore.GREEN
      + "Huawei Integrated Access Software (SmartAX MA5600T/MA5800)"
  )
  print(
      Fore.GREEN
      + "Copyright (c) Huawei Technologies Co., Ltd. 1998-2026. All rights"
      " reserved."
  )
  print(
      Style.DIM
      + "Ketik 'help' untuk melihat daftar perintah atau 'exit' untuk keluar.\n"
  )

  current_mode = (
      "user"  # 'user', 'privileged', 'config', 'interface', 'eth_interface', 'vlanif_interface', 'lineprofile'
  )
  active_interface = None
  active_eth_interface = None
  active_vlanif_id = None
  active_lineprofile_id = None

  while True:
    if current_mode == "user":
      prompt_str = "huawei> "
    elif current_mode == "privileged":
      prompt_str = "huawei# "
    elif current_mode == "config":
      prompt_str = "[OLT-Huawei] "
    elif current_mode == "interface":
      prompt_str = f"[OLT-Huawei-gpon-{active_interface}] "
    elif current_mode == "eth_interface":
      prompt_str = f"[OLT-Huawei-config-if-eth-{active_eth_interface}] "
    elif current_mode == "vlanif_interface":
      prompt_str = f"[OLT-Huawei-config-if-vlanif{active_vlanif_id}] "
    elif current_mode == "lineprofile":
      prompt_str = f"[OLT-Huawei-gpon-lineprofile-{active_lineprofile_id}] "

    try:
      cmd = input(Fore.GREEN + prompt_str).strip()
    except (KeyboardInterrupt, EOFError):
      print("\nKeluar dari simulator...")
      sys.exit()

    if not cmd:
      continue

    cmd_lower = cmd.lower()

    # --- HANDLE EXIT / QUIT / RETURN ---
    if cmd_lower in ["exit", "quit", "return"]:
      if current_mode == "lineprofile":
        current_mode = "config"
        active_lineprofile_id = None
        continue
      elif current_mode == "vlanif_interface":
        current_mode = "config"
        active_vlanif_id = None
        continue
      elif current_mode == "interface":
        current_mode = "config"
        active_interface = None
        continue
      elif current_mode == "eth_interface":
        current_mode = "config"
        active_eth_interface = None
        continue
      elif current_mode == "config":
        current_mode = "privileged"
        continue
      elif current_mode == "privileged":
        current_mode = "user"
        continue
      else:
        print(Fore.YELLOW + "Menutup sesi terminal...")
        break

    elif cmd_lower == "clear":
      clear_screen()
      continue

    elif cmd_lower == "help":
      print(Fore.CYAN + "\nDaftar Perintah Simulator Huawei OLT:")
      print("  enable                     - Masuk ke privileged mode")
      print("  config                     - Masuk ke global configuration mode")
      print("  vlan <ID> smart            - Membuat smart VLAN")
      print("  interface vlanif <ID>      - Masuk ke sub-mode interface Vlanif")
      print("  display interface vlanif   - Menampilkan status & IP Vlanif")
      print("  port vlan <ID> <port>      - Mengikat VLAN ke port uplink")
      print("  display vlan <ID>          - Menampilkan detail/status VLAN")
      print("  exit / quit / return       - Keluar / kembali mode sebelumnya\n")
      continue

    # --- ENABLE ---
    elif cmd_lower == "enable":
      if current_mode == "user":
        current_mode = "privileged"
      else:
        print("  Info: Already in privileged mode.")
      continue

    # --- CONFIG ---
    elif cmd_lower in ["config", "configure", "configure terminal"]:
      if current_mode == "privileged":
        current_mode = "config"
        print("Configured from terminal by admin@telnet")
      elif current_mode == "config":
        pass
      else:
        print(
            Fore.RED
            + "  ^\nError: Please enter 'enable' first to change configuration."
        )
      continue

    # --- INTERFACE GPON ---
    elif cmd_lower.startswith("interface gpon"):
      if current_mode != "config":
        print(
            Fore.RED
            + "  ^\nError: Please enter 'config' mode first to enter interface."
        )
        continue
      parts = cmd.split()
      if len(parts) >= 3:
        active_interface = parts[2]
        current_mode = "interface"
      else:
        print(Fore.RED + "  Error: Invalid interface specification.")
      continue

    # --- INTERFACE ETH (UPLINK) ---
    elif cmd_lower.startswith("interface eth"):
      mode, eth_target = handle_eth_interface(username, cmd, current_mode)
      if mode:
        current_mode, active_eth_interface = mode, eth_target
      continue

    # --- INTERFACE VLANIF ---
    elif cmd_lower.startswith("interface vlanif"):
      mode, vlan_target = handle_vlanif_interface(username, cmd, current_mode)
      if mode:
        current_mode, active_vlanif_id = mode, vlan_target
      continue

    # --- SUB-MODE VLANIF ---
    elif current_mode == "vlanif_interface":
      handle_vlanif_submode(username, cmd, active_vlanif_id)
      continue

    elif cmd_lower.startswith("ip route-static"):
     handle_ip_route_static(username, cmd, current_mode)
    
    elif cmd_lower.startswith("display ip routing-table"):
     handle_display_ip_routing_table(username, cmd)

    # --- ONT LINEPROFILE ---
    elif cmd_lower.startswith("ont-lineprofile gpon"):
      if current_mode != "config":
        print(Fore.RED + "  ^\nError: Must be in config mode.")
        continue
      # Kirim seluruh string command utuh agar bisa di-parsing di modul line_profile
      mode, pid = handle_line_profile_creation(username, cmd, current_mode)
      if mode:
        current_mode, active_lineprofile_id = mode, pid
      continue

    # --- SUB-MODE LINEPROFILE ---
    elif current_mode == "lineprofile":
      if cmd_lower == "quit":
        current_mode = "config"
        active_lineprofile_id = None
      else:
        handle_line_profile_submode(username, cmd, active_lineprofile_id)
      continue

    # --- ROUTING PERINTAH LAINNYA ---
    elif cmd_lower.startswith("display board"):
      handle_display_board(username, cmd)
    elif cmd_lower.startswith("board confirm"):
      handle_board_confirm(username, cmd, current_mode)
    elif cmd_lower.startswith("display dba-profile"):
      try:
        print(execute_dba_command(username, cmd, current_mode))
      except DBACommandError as exc:
        print(Fore.RED + f"Failure: {exc}")
    elif cmd_lower.startswith("dba-profile "):
      try:
        if cmd_lower.startswith("dba-profile add") and needs_multiburst_confirmation(cmd):
          answer = input(
              "The profile uses single-frame multiburst; lower-capability ONTs may fail. "
              "Are you sure to execute this command? (y/n)[n]: "
          ).strip().lower()
          if answer not in ("y", "yes"):
            print(Fore.YELLOW + "Operation cancelled.")
            continue
        print(execute_dba_command(username, cmd, current_mode))
      except DBACommandError as exc:
        print(Fore.RED + f"Failure: {exc}")
    elif cmd_lower.startswith("display ont-lineprofile gpon"):
      handle_display_line_profile(username, cmd)
    elif cmd_lower.startswith("display ont autofind"):
      handle_display_ont_autofind(username, cmd)
    elif cmd_lower.startswith("display ont info"):
      handle_display_ont_info(username, cmd)
    elif cmd_lower.startswith("display ont optical-info"):
      handle_display_ont_optical(username, cmd)
    elif cmd_lower.startswith("ont add"):
      handle_ont_add(username, cmd, current_mode, active_interface)
    elif cmd_lower.startswith("service-port"):
      handle_service_port_add(username, cmd, current_mode)
    elif cmd_lower.startswith("display service-port"):
      handle_display_service_port(username, cmd)

    # --- ROUTING PERINTAH VLAN & VLANIF ---
    elif cmd_lower.startswith("vlan "):
      handle_vlan_create(username, cmd, current_mode)
    elif cmd_lower.startswith("port vlan"):
      handle_port_vlan_bind(username, cmd, current_mode)
    elif cmd_lower.startswith("display interface vlanif"):
      handle_display_interface_vlanif(username, cmd)
    elif cmd_lower.startswith("display current-configuration interface vlanif"):
      handle_display_current_configuration_vlanif(username, cmd)
    elif cmd_lower.startswith("display vlan"):
      handle_display_vlan(username, cmd)
    elif cmd_lower.startswith("undo vlan") or cmd_lower.startswith(
        "undo port vlan"
    ):
      handle_undo_vlan(username, cmd, current_mode)

    elif (
       cmd_lower.startswith("clock timezone")
       or cmd_lower.startswith("ntp-service")
       or cmd_lower.startswith("snmp-agent")
       or cmd_lower.startswith("sysman service")
       or cmd_lower.startswith("sysman server")
   ):
     handle_system_config(username, cmd, current_mode)

    elif (
       cmd_lower.startswith("display clock")
       or cmd_lower.startswith("display ntp-service")
       or cmd_lower.startswith("display snmp-agent")
       or cmd_lower.startswith("display sysman server")
   ):
     handle_display_system(username, cmd)

    # --- SAVE CONFIG ---
    elif cmd_lower == "save":
      if current_mode != "privileged":
        print(Fore.RED + "Failure: Run this command in privilege mode.")
      else:
        try:
          save_user_configuration(username)
          print(
              Fore.GREEN
              + "It will take several minutes to save configuration file, please wait...\r\n"
              "Configuration file had been saved successfully.\r\n"
              "System configuration data has been saved completely."
          )
        except Exception as exc:
          print(Fore.RED + f"Configuration could not be saved: {exc}")

    # --- RESET ---
    elif cmd_lower == "reset":
      confirm = (
          input(
              Fore.RED
              + "Yakin ingin mereset konfigurasi akun Anda ke awal? (y/n): "
          )
          .strip()
          .lower()
      )
      if confirm == "y":
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM olt_boards WHERE username = ?", (username,))
        cursor.execute("DELETE FROM olt_onts WHERE username = ?", (username,))
        cursor.execute(
            "DELETE FROM olt_autofind WHERE username = ?", (username,)
        )
        cursor.execute(
            "DELETE FROM olt_dba_profiles WHERE username = ?", (username,)
        )
        cursor.execute(
            "DELETE FROM olt_line_profiles WHERE username = ?", (username,)
        )
        cursor.execute(
            "DELETE FROM olt_service_profiles WHERE username = ?", (username,)
        )
        cursor.execute(
            "DELETE FROM olt_service_ports WHERE username = ?", (username,)
        )
        cursor.execute("DELETE FROM olt_vlan WHERE username = ?", (username,))
        conn.commit()
        conn.close()
        init_user_data(username)
        print(
            Fore.YELLOW
            + f"  [SYSTEM] Konfigurasi akun [{username}] telah direset ke awal.\n"
        )
      else:
        print(Fore.DIM + "  Reset dibatalkan.\n")

    else:
      extended = dispatch_extended(
          username,
          "Huawei_1",
          cmd,
          current_mode,
          active_interface or "0/1",
      )
      if extended is not None:
        print(extended)
      else:
        print(
            Fore.RED
            + f"  ^\nError: Unrecognized command found at '^' position.\n"
        )
