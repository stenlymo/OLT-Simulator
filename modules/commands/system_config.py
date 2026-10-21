import sqlite3
from colorama import Fore
from modules.database import get_connection


def handle_system_config(username, cmd, current_mode):
  if current_mode not in [
      "config",
      "interface",
      "eth_interface",
      "vlanif_interface",
  ]:
    print(Fore.RED + "  ^\nError: Must be in config mode.")
    return

  cmd_lower = cmd.lower()
  conn = get_connection()
  cursor = conn.cursor()

  try:
    if cmd_lower.startswith("clock timezone"):
      parts = cmd.split()
      tz_name = parts[2]
      tz_offset = parts[4] if len(parts) > 4 else "+07:00:00"
      cursor.execute(
          """
              INSERT OR REPLACE INTO olt_system_config (username, key, value)
              VALUES (?, 'timezone', ?)
          """,
          (username, f"{tz_name} add {tz_offset}"),
      )

    elif cmd_lower.startswith("ntp-service unicast-server"):
      parts = cmd.split()
      ntp_ip = parts[2]
      cursor.execute(
          """
              INSERT OR REPLACE INTO olt_system_config (username, key, value)
              VALUES (?, 'ntp_server', ?)
          """,
          (username, ntp_ip),
      )

    elif cmd_lower.startswith("snmp-agent sys-info version"):
      parts = cmd.split()
      version = parts[3]
      cursor.execute(
          """
              INSERT OR REPLACE INTO olt_system_config (username, key, value)
              VALUES (?, 'snmp_version', ?)
          """,
          (username, version),
      )

    elif cmd_lower.startswith("snmp-agent community"):
      # Contoh: snmp-agent community read cesn@ atau snmp-agent community write cesna@lab
      parts = cmd.split()
      if len(parts) >= 3:
        comm_type = parts[2]  # read atau write
        # Ambil argumen terakhir sebagai string komunitas (mengabaikan kata 'cipher' jika ada)
        comm_val = parts[-1].strip('"')
        key_name = (
            "snmp_community_read"
            if comm_type == "read"
            else "snmp_community_write"
        )
        cursor.execute(
            """
                INSERT OR REPLACE INTO olt_system_config (username, key, value)
                VALUES (?, ?, ?)
            """,
            (username, key_name, comm_val),
        )

    elif cmd_lower.startswith("snmp-agent target-host"):
      parts = cmd.split()
      if "trap-hostname" in cmd_lower and "address" in cmd_lower:
        h_name = parts[parts.index("trap-hostname") + 1]
        h_addr = parts[parts.index("address") + 1]
        p_name = (
            parts[parts.index("trap-paramsname") + 1]
            if "trap-paramsname" in parts
            else "ABC"
        )

        cursor.execute(
            """
                INSERT OR REPLACE INTO olt_snmp_targets (username, hostname, address, params_name, version)
                VALUES (?, ?, ?, ?, 'v2c')
            """,
            (username, h_name, h_addr, p_name),
        )

    conn.commit()
  except Exception as e:
    print(Fore.RED + f"  Error parsing system configuration syntax: {e}\n")
  finally:
    conn.close()


def handle_display_system(username, cmd):
  cmd_lower = cmd.lower()
  conn = get_connection()
  cursor = conn.cursor()

  if "clock" in cmd_lower:
    cursor.execute(
        "SELECT value FROM olt_system_config WHERE username = ? AND key ="
        " 'timezone'",
        (username,),
    )
    row = cursor.fetchone()
    tz = row[0] if row else "WIB add 07:00:00"

    print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
    print(f"         {cmd}")
    print(" 2026-10-15 09:15:30+07:00")
    print(" Thursday")
    print(f" Time Zone : {tz}\n")

  elif "ntp-service status" in cmd_lower:
    cursor.execute(
        "SELECT value FROM olt_system_config WHERE username = ? AND key ="
        " 'ntp_server'",
        (username,),
    )
    row = cursor.fetchone()
    ntp = row[0] if row else "103.172.24.230"

    print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
    print(f"         {cmd}")
    print("  Clock status: synchronized")
    print("  Clock stratum: 3")
    print(f"  Reference clock ID: {ntp}\n")

  elif (
      "snmp-agent sys-info" in cmd_lower
      or cmd_lower == "display snmp-agent"
      or cmd_lower == "display snmp-agent community read"
      or cmd_lower == "display snmp-agent community write"
  ):
    cursor.execute(
        "SELECT value FROM olt_system_config WHERE username = ? AND key ="
        " 'snmp_version'",
        (username,),
    )
    row_ver = cursor.fetchone()
    ver = row_ver[0] if row_ver else "v2c"

    cursor.execute(
        "SELECT value FROM olt_system_config WHERE username = ? AND key ="
        " 'snmp_community_read'",
        (username,),
    )
    row_comm_read = cursor.fetchone()
    comm_read = row_comm_read[0] if row_comm_read else "cesn@"

    cursor.execute(
        "SELECT value FROM olt_system_config WHERE username = ? AND key ="
        " 'snmp_community_write'",
        (username,),
    )
    row_comm_write = cursor.fetchone()
    comm_write = row_comm_write[0] if row_comm_write else ""

    print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
    print(f"         {cmd}")
    print("  SNMP version running on the system:")
    print(f"            SNMP {ver}")
    print(f"  SNMP community read  : {comm_read}")
    if comm_write or "write" in cmd_lower:
      print(f"  SNMP community write : {comm_write}")
    print()

  elif "snmp-agent target-host" in cmd_lower:
    cursor.execute(
        "SELECT hostname, address, params_name, version FROM olt_snmp_targets"
        " WHERE username = ?",
        (username,),
    )
    rows = cursor.fetchall()

    print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
    print(f"         {cmd}")
    print(
        "-------------------------------------------------------------------------------"
    )
    if rows:
      for r in rows:
        print(f" Target-host name: {r[0]}")
        print(f" IP address      : {r[1]}")
        print(f" Params name     : {r[2]}")
        print(f" Version         : {r[3]}")
        print(" Security name   : ******\n")
    else:
      print(" Target-host name: NCE-FAN1")
      print(" IP address      : 10.96.6.35")
      print(" Params name     : ABC")
      print(" Version         : v2c")
      print(" Security name   : ******\n")
    print(
        "-------------------------------------------------------------------------------"
    )

  elif "sysman server" in cmd_lower:
    print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
    print(f"         {cmd}")
    print("---------------------------------------------------------")
    print(" Service      State         Port      Source-Interface")
    print("---------------------------------------------------------")
    print(" SNMP         enable        161       any-interface")
    print(" TELNET       enable        23        any-interface")
    print(" SSH          enable        22        any-interface")
    print("---------------------------------------------------------\n")

  conn.close()