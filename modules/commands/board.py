import sqlite3
from colorama import Fore
from modules.database import get_connection


def handle_display_board(username, cmd):
  conn = get_connection()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT slot_id, board_name, status, subtype0, subtype1, online_offline"
      " FROM olt_boards WHERE username = ? ORDER BY slot_id",
      (username,),
  )
  rows = cursor.fetchall()
  conn.close()

  print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
  print(f"         {cmd}")
  print(
      " -------------------------------------------------------------------------"
  )
  print(" SlotID  BoardName   Status         SubType0 SubType1    Online/Offline")
  print(
      " -------------------------------------------------------------------------"
  )
  for r in rows:
    slot_id, board_name, status, sub0, sub1, online = r
    print(
        f" {slot_id:<7} {board_name:<11} {status:<14} {sub0:<8} {sub1:<11}"
        f" {online}"
    )
  print(
      " -------------------------------------------------------------------------\n"
  )


def handle_board_confirm(username, cmd, current_mode):
  if current_mode not in ["config", "interface"]:
    print(Fore.RED + "  ^\nError: Must be in config or interface mode.")
    return

  parts = cmd.split()
  slot_target = int(parts[3]) if len(parts) >= 4 else None

  conn = get_connection()
  cursor = conn.cursor()
  if slot_target is not None:
    cursor.execute(
        "UPDATE olt_boards SET status = 'Normal' WHERE username = ? AND"
        " slot_id = ? AND status = 'config'",
        (username, slot_target),
    )
  else:
    cursor.execute(
        "UPDATE olt_boards SET status = 'Normal' WHERE username = ? AND"
        " status = 'config'",
        (username,),
    )
  conn.commit()
  conn.close()
  print(
      Fore.GREEN
      + "  ----------------------------------------------------\n"
      f"  Configuration successful! Board status updated to 'Normal'.\n"
      "  ----------------------------------------------------\n"
  )