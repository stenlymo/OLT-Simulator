import sqlite3
from colorama import Fore
from modules.database import get_connection


def handle_line_profile_creation(username, cmd, current_mode):
  if current_mode != "config":
    print(Fore.RED + "  ^\nError: Must be in config mode.")
    return None, None
  
  try:
    p_id = 1
    p_name = ""
    parts = cmd.split()

    # Parsing argumen aman menggunakan iterasi token
    i = 0
    while i < len(parts):
      token = parts[i].lower()
      if token == "profile-id" and i + 1 < len(parts):
        try:
          p_id = int(parts[i + 1])
        except ValueError:
          pass
        i += 1
      elif token == "profile-name" and i + 1 < len(parts):
        p_name = parts[i + 1].strip('"')
        i += 1
      i += 1

    # Jika nama profil tidak diisi, buatkan otomatis berdasarkan ID
    if not p_name:
      p_name = f"line_profile_{p_id}"

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
            INSERT OR REPLACE INTO olt_line_profiles (username, profile_id, profile_name, bind_count)
            VALUES (?, ?, ?, 0)
        """,
        (username, p_id, p_name),
    )
    conn.commit()
    conn.close()

    return "lineprofile", p_id
  except Exception as e:
    print(Fore.RED + f"  Error parsing ont-lineprofile syntax: {e}\n")
    return None, None


def handle_line_profile_submode(username, cmd, active_profile_id):
  cmd_lower = cmd.lower()
  if not cmd_lower:
    return

  conn = get_connection()
  cursor = conn.cursor()

  try:
    if cmd_lower.startswith("tcont") or cmd_lower.startswith("gem add") or cmd_lower.startswith("tr069-management") or cmd_lower == "commit" or cmd_lower == "omcc":
      pass
    elif cmd_lower.startswith("gem mapping"):
      parts = cmd.split()
      if len(parts) >= 4:
        gem_id = parts[2]
        mapping_val = parts[3]
        cursor.execute(
            """
                INSERT OR REPLACE INTO olt_line_profile_mappings (username, profile_id, gem_id, mapping_val)
                VALUES (?, ?, ?, ?)
            """,
            (username, active_profile_id, gem_id, mapping_val),
        )

    conn.commit()
  except Exception:
    print(Fore.RED + "  Error parsing line profile submode command.")
  finally:
    conn.close()


def handle_display_line_profile(username, cmd):
  conn = get_connection()
  cursor = conn.cursor()

  parts = cmd.split()
  if "profile-id" in cmd:
    p_id = 1
    for i, p in enumerate(parts):
      if p.lower() == "profile-id" and i + 1 < len(parts):
        try:
          p_id = int(parts[i + 1])
        except ValueError:
          pass

    cursor.execute(
        "SELECT profile_id, profile_name, bind_count FROM olt_line_profiles WHERE username = ? AND profile_id = ?",
        (username, p_id),
    )
    row = cursor.fetchone()
    conn.close()

    if row:
      print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
      print(f"         {cmd}")
      print(" ------------------------------------------------------------------------------")
      print(f" Profile-ID          :{row[0]}")
      print(f" Profile-name        :{row[1]}")
      print(" Access-type         :GPON")
      print(" ------------------------------------------------------------------------------")
      print(f" Binding times       :{row[2]}")
      print(" ------------------------------------------------------------------------------\n")
    else:
      print(Fore.RED + "  Error: Line profile ID not found.\n")
  else:
    cursor.execute(
        "SELECT profile_id, profile_name, bind_count FROM olt_line_profiles WHERE username = ?",
        (username,),
    )
    rows = cursor.fetchall()
    conn.close()

    print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
    print(f"         {cmd}")
    print(" --------------------------------------------------------------------------")
    print(" Profile-ID  Profile-name                     Bind-count")
    print(" --------------------------------------------------------------------------")
    for r in rows:
      print(f" {r[0]:<11} {r[1]:<32} {r[2]}")
    print(" --------------------------------------------------------------------------")
    print(f" Total: {len(rows)}\n")


def handle_undo_line_profile(username, cmd, current_mode):
  if current_mode != "config":
    print(Fore.RED + "  ^\nError: Must be in config mode.")
    return
  try:
    parts = cmd.split()
    target_id = None
    target_name = None

    for i, p in enumerate(parts):
      if p.lower() == "profile-id" and i + 1 < len(parts):
        try:
          target_id = int(parts[i + 1])
        except ValueError:
          pass
      elif p.lower() == "profile-name" and i + 1 < len(parts):
        target_name = parts[i + 1].strip('"')

    conn = get_connection()
    cursor = conn.cursor()

    if target_id is not None:
      cursor.execute(
          "SELECT bind_count FROM olt_line_profiles WHERE username = ? AND profile_id = ?",
          (username, target_id),
      )
    elif target_name is not None:
      cursor.execute(
          "SELECT bind_count FROM olt_line_profiles WHERE username = ? AND profile_name = ?",
          (username, target_name),
      )
    else:
      conn.close()
      print(Fore.RED + "  Error: Missing profile identifier.")
      return

    row = cursor.fetchone()
    if row:
      bind_count = row[0]
      if bind_count > 0:
        print(Fore.RED + "  Failure: The profile is being used.")
      else:
        if target_id is not None:
          cursor.execute(
              "DELETE FROM olt_line_profiles WHERE username = ? AND profile_id = ?",
              (username, target_id),
          )
        else:
          cursor.execute(
              "DELETE FROM olt_line_profiles WHERE username = ? AND profile_name = ?",
              (username, target_name),
          )
        conn.commit()
    else:
      print(Fore.RED + "  Error: Line profile not found.")

    conn.close()
  except Exception:
    print(Fore.RED + "  Error parsing undo ont-lineprofile syntax.\n")