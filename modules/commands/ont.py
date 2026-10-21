import sqlite3
from colorama import Fore
from modules.database import get_connection
from modules.commands.extended import dispatch as dispatch_extended


def handle_ont_add(username, cmd, current_mode, active_interface):
  if current_mode not in ["config", "interface"]:
    print(Fore.RED + "  ^\nError: Must be in config mode.")
    return
  try:
    parts = cmd.split()
    ont_id = int(parts[2])
    sn = parts[parts.index("sn-auth") + 1].strip('"')
    line_p = int(parts[parts.index("ont-lineprofile-id") + 1])
    serv_p = int(parts[parts.index("ont-serviceprofile-id") + 1])

    target_port = active_interface if active_interface else "0/1/0"

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
            INSERT OR REPLACE INTO olt_onts 
            (username, port, ont_id, sn, control_flag, run_state, config_state, match_state, description, last_down_cause, rx_power, tx_power, olt_rx_power, line_profile_id, service_profile_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            username,
            target_port,
            ont_id,
            sn,
            "active",
            "online",
            "normal",
            "match",
            "Pelanggan_Baru",
            "power-off",
            -19.45,
            2.15,
            -20.10,
            line_p,
            serv_p,
        ),
    )
    conn.commit()
    conn.close()
    print(
        Fore.GREEN
        + f"  Success: ONT ID {ont_id} with SN {sn} successfully registered on"
        f" port {target_port}!\n"
    )
  except Exception:
    print(Fore.RED + "  Error: Invalid ont add syntax or parameters.\n")


def handle_display_ont_autofind(username, cmd):
  conn = get_connection()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT port, device_type, sn, find_time FROM olt_autofind WHERE"
      " username = ?",
      (username,),
  )
  rows = cursor.fetchall()
  conn.close()

  print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
  print(f"         {cmd}")
  print(
      " ----------------------------------------------------------------------------"
  )
  print(" Number F/S/P   Device-type   SN                 Password     Find-time")
  print(
      " ----------------------------------------------------------------------------"
  )
  for idx, r in enumerate(rows, 1):
    port, dev_type, sn, f_time = r
    print(f" {idx:<6} {port:<6} {dev_type:<13} {sn:<18} {'-':<12} {f_time}")
  print(
      " ----------------------------------------------------------------------------"
  )
  print(f" Total number of automatic finding ONTs is {len(rows)}\n")


def handle_display_ont_info(username, cmd):
  conn = get_connection()
  cursor = conn.cursor()
  cursor.execute(
      """
        SELECT port, ont_id, control_flag, run_state, config_state, match_state, 
               description, last_down_cause FROM olt_onts WHERE username = ?
    """,
      (username,),
  )
  ont = cursor.fetchone()
  conn.close()

  if ont:
    port, ont_id, ctrl, run, cfg, match, desc, down_cause = ont
    print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
    print(f"         {cmd}")
    print(
        " -----------------------------------------------------------------------------"
    )
    print(f" F/S/P                : {port}")
    print(f" ONT-ID               : {ont_id}")
    print(f" Control flag         : {ctrl}")
    print(f" Run state            : {run}")
    print(f" Config state         : {cfg}")
    print(f" Match state          : {match}")
    print(f" Description          : {desc}")
    print(f" Last down cause      : {down_cause}")
    print(
        " -----------------------------------------------------------------------------\n"
    )
  else:
    print(Fore.RED + "  Error: No ONT found.\n")


def handle_display_ont_optical(username, cmd):
  output = dispatch_extended(username, "Huawei_1", cmd, "ENABLE", "0/1")
  if output:
    print(output)
  else:
    print(Fore.RED + "  Error: ONT optical info not found.\n")
