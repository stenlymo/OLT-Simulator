import sqlite3
from colorama import Fore
from modules.database import get_connection


def handle_service_port_add(username, cmd, current_mode):
  if current_mode not in ["config", "interface"]:
    print(Fore.RED + "  ^\nError: Must be in config mode.")
    return
  try:
    parts = cmd.split()
    sp_id = int(parts[1])
    vlan = int(parts[3])
    port = parts[5]
    ont_id = int(parts[7])
    gemport = int(parts[9])
    user_vlan = int(parts[11])

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
            INSERT OR REPLACE INTO olt_service_ports 
            (username, sp_id, vlan, port_type, port, ont_id, gemport, user_vlan, state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (username, sp_id, vlan, "gpon", port, ont_id, gemport, user_vlan, "up"),
    )
    conn.commit()
    conn.close()
    print(Fore.GREEN + f"  Success: Service-port {sp_id} created for VLAN {vlan}.\n")
  except Exception:
    print(Fore.RED + "  Error parsing service-port syntax.\n")


def handle_display_service_port(username, cmd):
  conn = get_connection()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT sp_id, vlan, port_type, port, ont_id, gemport, user_vlan, state"
      " FROM olt_service_ports WHERE username = ?",
      (username,),
  )
  rows = cursor.fetchall()
  conn.close()

  if rows:
    for r in rows:
      sp_id, vlan, p_type, port, ont_id, gem, u_vlan, state = r
      print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
      print(f"         {cmd}")
      print(
          " --------------------------------------------------------------------------"
      )
      print(f" INDEX          : {sp_id}")
      print(f" VLAN ID        : {vlan}")
      print(f" PORT TYPE      : {p_type}")
      print(f" F/S/P          : {port}")
      print(f" ONT ID         : {ont_id}")
      print(f" GEMPORT ID     : {gem}")
      print(f" USER VLAN      : {u_vlan}")
      print(f" STATE          : {state}")
      print(
          " --------------------------------------------------------------------------\n"
      )
  else:
    print(Fore.YELLOW + "  No service-port configured yet.\n")