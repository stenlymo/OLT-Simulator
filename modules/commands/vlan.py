import sqlite3
from colorama import Fore
from modules.database import get_connection


def handle_vlan_create(username, cmd, current_mode):
  if current_mode not in ["config", "interface", "eth_interface", "vlanif_interface"]:
    print(Fore.RED + "  ^\nError: Must be in config mode.")
    return
  try:
    parts = cmd.split()
    vlan_id = int(parts[1])
    vlan_type = parts[2] if len(parts) > 2 else "smart"

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
            INSERT OR REPLACE INTO olt_vlan (username, vlan_id, vlan_type, vlan_attr)
            VALUES (?, ?, ?, ?)
        """,
        (username, vlan_id, vlan_type, "common"),
    )
    conn.commit()
    conn.close()
  except Exception:
    print(Fore.RED + "  Error parsing vlan creation syntax.\n")


def handle_vlanif_interface(username, cmd, current_mode):
  if current_mode != "config":
    print(
        Fore.RED
        + "  ^\nError: Please enter 'config' mode first to enter vlanif."
    )
    return None, None
  try:
    parts = cmd.split()
    # Mendukung format "interface vlanif 3000" atau "interface vlanif3000"
    target_str = parts[2] if len(parts) > 2 else parts[1]
    vlan_str = target_str.replace("vlanif", "")
    if not vlan_str and len(parts) > 3:
      vlan_str = parts[3]
    vlan_id = int(vlan_str)

    # Pastikan data vlan terdaftar di tabel, jika belum buat otomatis
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
            INSERT OR IGNORE INTO olt_vlan (username, vlan_id, vlan_type, vlan_attr)
            VALUES (?, ?, 'smart', 'common')
        """,
        (username, vlan_id),
    )
    conn.commit()
    conn.close()

    return "vlanif_interface", vlan_id
  except Exception:
    print(Fore.RED + "  Error parsing interface vlanif syntax.")
  return None, None


def handle_vlanif_submode(username, cmd, active_vlan_id):
  cmd_lower = cmd.lower()
  if not cmd_lower:
    return

  conn = get_connection()
  cursor = conn.cursor()

  if cmd_lower.startswith("ip address"):
    try:
      parts = cmd.split()
      ip_addr = parts[2]
      mask = parts[3] if len(parts) > 3 else "255.255.255.0"
      
      # Hitung prefix mask (misal 255.255.255.0 jadi 24)
      mask_parts = mask.split(".")
      bits = sum(bin(int(p)).count("1") for p in mask_parts)
      ip_full = f"{ip_addr}/{bits}"

      cursor.execute(
          """
              UPDATE olt_vlan 
              SET ip_address = ? WHERE username = ? AND vlan_id = ?
          """,
          (ip_full, username, active_vlan_id),
      )
      conn.commit()
    except Exception:
      print(Fore.RED + "  Error parsing ip address syntax.")

  elif cmd_lower.startswith("desc") or cmd_lower.startswith("description"):
    try:
      desc_val = cmd.split(" ", 1)[1].strip('"')
      cursor.execute(
          """
              UPDATE olt_vlan 
              SET description = ? WHERE username = ? AND vlan_id = ?
          """,
          (desc_val, username, active_vlan_id),
      )
      conn.commit()
    except Exception:
      print(Fore.RED + "  Error parsing description syntax.")

  conn.close()


def handle_eth_interface(username, cmd, current_mode):
  if current_mode != "config":
    print(
        Fore.RED
        + "  ^\nError: Please enter 'config' mode first to enter interface."
    )
    return None, None
  try:
    parts = cmd.split()
    if len(parts) >= 3:
      eth_target = parts[2]
      return "eth_interface", eth_target
  except Exception:
    print(Fore.RED + "  Error parsing interface eth syntax.")
  return None, None


def handle_port_vlan_bind(username, cmd, current_mode):
  if current_mode not in ["config", "interface", "eth_interface"]:
    print(Fore.RED + "  ^\nError: Must be in config mode.")
    return
  try:
    parts = cmd.split()
    vlan_id = int(parts[2])
    slot_port = parts[3]
    port_id = parts[4] if len(parts) > 4 else "0"
    bound_str = f"{slot_port}/{port_id}"

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
            UPDATE olt_vlan 
            SET bound_port = ?, bound_state = 'up', bound_tag = 'tag' 
            WHERE username = ? AND vlan_id = ?
        """,
        (bound_str, username, vlan_id),
    )
    conn.commit()
    conn.close()
  except Exception:
    print(Fore.RED + "  Error parsing port vlan syntax.\n")


def handle_display_vlan(username, cmd):
  cmd_lower = cmd.lower()
  conn = get_connection()
  cursor = conn.cursor()

  parts = cmd.split()
  if len(parts) >= 3 and parts[2].isdigit():
    vlan_id = int(parts[2])
    cursor.execute(
        "SELECT vlan_id, vlan_type, vlan_attr, bound_port, bound_state, bound_tag FROM olt_vlan WHERE username = ? AND vlan_id = ?",
        (username, vlan_id),
    )
    row = cursor.fetchone()
    conn.close()

    if row:
      v_id, v_type, v_attr, b_port, b_state, b_tag = row
      print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
      print(f"         {cmd}")
      print(f" VLAN ID: {v_id}")
      print(f" VLAN Type: {v_type}")
      print(f" VLAN Attribute: {v_attr}")
      print(
          " --------------------------------------------------------------------------"
      )
      print(" F /S /P       Type      Uplink/User    State        Untag/Tag")
      print(
          " --------------------------------------------------------------------------"
      )
      if b_port:
        print(f" {b_port:<11} eth       uplink         {b_state:<12} {b_tag}")
      else:
        print(" (No ports bound)")
      print(
          " --------------------------------------------------------------------------\n"
      )
    else:
      print(Fore.RED + "  Error: VLAN ID not found.\n")
  else:
    cursor.execute(
        "SELECT vlan_id, vlan_type, vlan_attr FROM olt_vlan WHERE username = ?",
        (username,),
    )
    rows = cursor.fetchall()
    conn.close()

    print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
    print(f"         {cmd}")
    print(
        " --------------------------------------------------------------------------"
    )
    print(" VLAN ID  VLAN Type  VLAN Attr  Broadcast Suppression  IGMP Snooping  STP")
    print(
        " --------------------------------------------------------------------------"
    )
    for r in rows:
      print(
          f" {r[0]:<8} {r[1]:<10} {r[2]:<10} disable                disable       "
          " disable"
      )
    print(
        " --------------------------------------------------------------------------"
    )
    print(f" Total number of VLANs is {len(rows)}\n")


def handle_display_interface_vlanif(username, cmd):
  parts = cmd.split()
  # Mendukung perintah 'display interface vlanif 3000' atau 'display interface vlanif3000'
  target_str = ""
  for p in parts:
    if "vlanif" in p.lower():
      target_str = p.lower().replace("vlanif", "")
  if not target_str and len(parts) >= 3:
    target_str = parts[3] if parts[2].lower() == "vlanif" else parts[2]
  
  try:
    vlan_id = int(target_str)
  except ValueError:
    print(Fore.RED + "  Error: Invalid vlanif ID format.")
    return

  conn = get_connection()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT vlan_id, description, ip_address FROM olt_vlan WHERE username = ? AND vlan_id = ?",
      (username, vlan_id),
  )
  row = cursor.fetchone()
  conn.close()

  if row:
    v_id, desc, ip_addr = row
    desc_str = desc if desc else f"INTERFACE_VLAN_{v_id}"
    ip_str = ip_addr if ip_addr else "192.168.30.1/24"

    print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
    print(f"         {cmd}")
    print(f"  vlanif{v_id} current state : UP")
    print("  Line protocol current state : UP")
    print(f"  Description : {desc_str}")
    print("  The Maximum Transmit Unit is 1500 bytes")
    print(f"  Internet Address is {ip_str}")
    print("  IP Sending Frames' Format is PKTFMT_ETHNT_2, Hardware address is 00e0-fc01-2345\n")
  else:
    print(Fore.RED + f"  Error: Vlanif{v_id} configuration not found. Please create the VLAN first.\n")


def handle_display_current_configuration_vlanif(username, cmd):
  parts = cmd.split()
  target_str = ""
  
  # Cari bagian teks yang mengandung angka atau persis setelah 'vlanif'
  for i, p in enumerate(parts):
    if "vlanif" in p.lower():
      # Jika formatnya 'vlanif3000'
      cleaned = p.lower().replace("vlanif", "")
      if cleaned.isdigit():
        target_str = cleaned
        break
      # Jika formatnya terpisah 'vlanif 3000' dan ada kata berikutnya
      elif i + 1 < len(parts) and parts[i + 1].isdigit():
        target_str = parts[i + 1]
        break

  # Fallback: ambil token terakhir jika berupa angka
  if not target_str and parts[-1].isdigit():
    target_str = parts[-1]

  try:
    vlan_id = int(target_str)
  except ValueError:
    print(Fore.RED + "  Error: Invalid vlanif ID format.")
    return

  conn = get_connection()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT vlan_id, ip_address, description FROM olt_vlan WHERE username = ? AND vlan_id = ?",
      (username, vlan_id),
  )
  row = cursor.fetchone()
  conn.close()

  if row:
    v_id, ip_addr, desc = row
    
    # Format IP Address (ubah kembali dari format CIDR ke format full subnet jika perlu)
    ip_conf = ""
    if ip_addr:
      if "/" in ip_addr:
        ip, prefix = ip_addr.split("/")
        # Konversi sederhana prefix ke netmask (misal /24 -> 255.255.255.0)
        prefix_int = int(prefix)
        mask_bits = [0, 0, 0, 0]
        for idx in range(prefix_int):
          mask_bits[idx // 8] |= 1 << (7 - (idx % 8))
        mask_str = ".".join(str(b) for b in mask_bits)
        ip_conf = f" ip address {ip} {mask_str}"
      else:
        ip_conf = f" ip address {ip_addr} 255.255.255.0"
    else:
      ip_conf = " ip address 192.168.30.1 255.255.255.0"

    desc_conf = f" description {desc}" if desc else ""

    print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
    print(f"         {cmd}")
    print(f"  [vlanif{v_id}]")
    print(f"  <vlanif{v_id}>")
    print(f"   interface vlanif {v_id}")
    if desc_conf:
      print(f"  {desc_conf}")
    print(f"  {ip_conf}")
    print("#")
    print("return\n")
  else:
    print(Fore.RED + f"  Error: Vlanif{v_id} configuration not found.\n")

def handle_ip_route_static(username, cmd, current_mode):
  if current_mode not in ["config", "interface", "eth_interface", "vlanif_interface"]:
    print(Fore.RED + "  ^\nError: Must be in config mode.")
    return
  try:
    parts = cmd.split()
    # Format: ip route-static 0.0.0.0 0.0.0.0 10.96.24.1 desc GATEWAY
    dest = parts[2]
    mask = parts[3]
    gateway = parts[4]
    
    desc = ""
    if "desc" in parts:
      desc_idx = parts.index("desc")
      if desc_idx + 1 < len(parts):
        desc = parts[desc_idx + 1].strip('"')

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
            INSERT OR REPLACE INTO olt_routes (username, dest, mask, gateway, description)
            VALUES (?, ?, ?, ?, ?)
        """,
        (username, dest, mask, gateway, desc),
    )
    conn.commit()
    conn.close()
  except Exception:
    print(Fore.RED + "  Error parsing ip route-static syntax.\n")


def handle_display_ip_routing_table(username, cmd):
  conn = get_connection()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT dest, mask, gateway, description FROM olt_routes WHERE username = ?",
      (username,),
  )
  rows = cursor.fetchall()
  conn.close()

  print(Fore.GREEN + "{ <cr>||<K> }: \n Command:")
  print(f"         {cmd}")
  print("Route Flags: R - relay, D - download to fib, T - to vpn-instance, B - black hole route")
  print("------------------------------------------------------------------------------")
  print("Routing Table : _public_")
  print(f"         Destinations : {len(rows) + 3}        Routes : {len(rows) + 3}         \n")
  print("Destination/Mask    Proto   Pre  Cost        Flags NextHop         Interface")
  print("------------------------------------------------------------------------------")
  print("        0.0.0.0/0   Static  60   0             RD  172.19.20.249   Vlanif13")
  
  for r in rows:
    dest, mask, gw, desc = r
    # Konversi mask sederhana ke CIDR (misal 0.0.0.0 / 0.0.0.0 jadi 0.0.0.0/0)
    prefix = "0" if mask == "0.0.0.0" else "24"
    dest_mask = f"{dest}/{prefix}"
    print(f"        {dest_mask:<12} Static  60   0             RD  {gw:<15} Vlanif39")
    
  print("------------------------------------------------------------------------------\n")

def handle_undo_vlan(username, cmd, current_mode):
  if current_mode not in ["config", "interface", "eth_interface"]:
    print(Fore.RED + "  ^\nError: Must be in config mode.")
    return
  try:
    parts = cmd.split()
    if "port" in cmd.lower():
      vlan_id = int(parts[3])
      conn = get_connection()
      cursor = conn.cursor()
      cursor.execute(
          """
                UPDATE olt_vlan 
                SET bound_port = '', bound_state = '', bound_tag = '' 
                WHERE username = ? AND vlan_id = ?
            """,
          (username, vlan_id),
      )
      conn.commit()
      conn.close()
    else:
      vlan_id = int(parts[2])
      conn = get_connection()
      cursor = conn.cursor()
      cursor.execute(
          "SELECT bound_port FROM olt_vlan WHERE username = ? AND vlan_id = ?",
          (username, vlan_id),
      )
      row = cursor.fetchone()
      if row and row[0]:
        print(
            Fore.RED
            + "  Failure: The VLAN has ports configured. Please use 'undo"
            " port vlan' first."
        )
      else:
        cursor.execute(
            "DELETE FROM olt_vlan WHERE username = ? AND vlan_id = ?",
            (username, vlan_id),
        )
        conn.commit()
      conn.close()
  except Exception:
    print(Fore.RED + "  Error parsing undo vlan syntax.\n")