import os
import sqlite3
import json
from datetime import datetime, timezone

# Bisa diarahkan ke database lain lewat variabel lingkungan (mis. untuk API/test)
DB_NAME = os.environ.get("OLT_DB_NAME", "olt_simulator.db")

# List 22 Serial Number ONT Alcatel-Lucent / Nokia Format
ONT_SN_POOL = [
    "414C434CB46E0A86", "414C434CB46C21FA", "414C434CB46C2142", "414C434CB46C3ECA",
    "414C434CB46C1F23", "414C434CB46C2E75", "414C434CB46C32DB", "414C434CB46C3085",
    "414C434CB46C2428", "414C434CB46C1FCF", "414C434CB46C308B", "414C434CB46C1FD1",
    "414C434CB46C1F22", "414C434CB46BEA8E", "414C434CB46BEB8C", "414C434CB46BEB50",
    "414C434CB46BEB6F", "414C434CB46BEAEE", "414C434CB46BEAA9", "414C434CB46BEA8A",
    "414C434CB46BEA6F", "414C434CB46BEBE9"
]


def get_connection():
  """Membuat koneksi ke database SQLite"""
  return sqlite3.connect(DB_NAME)


def _capture_user_configuration(cursor, username):
  """Capture every user-scoped OLT table so profiles and bindings travel together."""
  snapshot = {}
  tables = cursor.execute(
      "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'olt_%' ORDER BY name"
  ).fetchall()
  for (table_name,) in tables:
    columns = [row[1] for row in cursor.execute(f'PRAGMA table_info("{table_name}")')]
    if "username" not in columns:
      continue
    rows = cursor.execute(
        f'SELECT * FROM "{table_name}" WHERE username = ?', (username,)
    ).fetchall()
    snapshot[table_name] = [dict(zip(columns, row)) for row in rows]
  return snapshot


def save_user_configuration(username="admin"):
  """Persist a named save point for the user's current OLT configuration."""
  conn = get_connection()
  try:
    cursor = conn.cursor()
    snapshot = _capture_user_configuration(cursor, username)
    cursor.execute("""CREATE TABLE IF NOT EXISTS olt_saved_configurations (
        username TEXT PRIMARY KEY, configuration_json TEXT NOT NULL,
        saved_at TEXT NOT NULL)""")
    cursor.execute(
        "INSERT OR REPLACE INTO olt_saved_configurations VALUES (?, ?, ?)",
        (username, json.dumps(snapshot), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
  finally:
    conn.close()


def restore_user_configuration(cursor, username, snapshot):
  """Replace one user's OLT rows from a topology snapshot, preserving other users."""
  for table_name, rows in snapshot.items():
    if not table_name.startswith("olt_"):
      continue
    exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table_name,)
    ).fetchone()
    if not exists:
      continue
    columns = [row[1] for row in cursor.execute(f'PRAGMA table_info("{table_name}")')]
    if "username" not in columns:
      continue
    cursor.execute(f'DELETE FROM "{table_name}" WHERE username = ?', (username,))
    valid = [column for column in columns if any(column in row for row in rows)]
    for row in rows:
      row_columns = [column for column in valid if column in row]
      if row_columns:
        names = ", ".join(f'"{column}"' for column in row_columns)
        placeholders = ", ".join("?" for _ in row_columns)
        cursor.execute(
            f'INSERT OR REPLACE INTO "{table_name}" ({names}) VALUES ({placeholders})',
            [row[column] for column in row_columns],
        )


def save_topology_record(name, topology, username="admin"):
  conn = get_connection()
  try:
    cursor = conn.cursor()
    snapshot = _capture_user_configuration(cursor, username)
    cursor.execute("""CREATE TABLE IF NOT EXISTS olt_topologies (
        topology_id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
        name TEXT NOT NULL, topology_json TEXT NOT NULL,
        configuration_json TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(username, name))""")
    cursor.execute(
        """INSERT INTO olt_topologies
        (username, name, topology_json, configuration_json, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(username, name) DO UPDATE SET
        topology_json=excluded.topology_json,
        configuration_json=excluded.configuration_json,
        updated_at=excluded.updated_at""",
        (username, name, json.dumps(topology), json.dumps(snapshot), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
  finally:
    conn.close()


def list_topology_records(username="admin"):
  conn = get_connection()
  try:
    rows = conn.execute(
        "SELECT name FROM olt_topologies WHERE username = ? ORDER BY name", (username,)
    ).fetchall()
    return [row[0] for row in rows]
  finally:
    conn.close()


def load_topology_record(name, username="admin"):
  conn = get_connection()
  try:
    row = conn.execute(
        "SELECT topology_json, configuration_json FROM olt_topologies WHERE username = ? AND name = ?",
        (username, name),
    ).fetchone()
    if not row:
      return None
    topology, configuration = json.loads(row[0]), json.loads(row[1])
    restore_user_configuration(conn.cursor(), username, configuration)
    conn.commit()
    return topology
  finally:
    conn.close()


def init_db():
  """Inisialisasi database SQLite dan seluruh tabel pendukung"""
  conn = get_connection()
  cursor = conn.cursor()

  # 1. Tabel User
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'technician'
        )
    """)

  cursor.executemany(
      "INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?,"
      " ?)",
      [
          ("admin", "admin123", "administrator"),
          ("user1", "password123", "technician"),
      ],
  )

  # 2. Tabel State Board OLT
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_boards (
            username TEXT,
            slot_id INTEGER,
            board_name TEXT,
            status TEXT,
            subtype0 TEXT DEFAULT '',
            subtype1 TEXT DEFAULT '',
            online_offline TEXT DEFAULT '',
            PRIMARY KEY (username, slot_id)
        )
    """)

  # 3. Tabel DBA Profile
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_dba_profiles (
            username TEXT,
            profile_id INTEGER,
            profile_name TEXT,
            type TEXT,
            assured INTEGER,
            max_bw INTEGER,
            bind_count INTEGER DEFAULT 0,
            PRIMARY KEY (username, profile_id)
        )
    """)
  dba_columns = {row[1] for row in cursor.execute("PRAGMA table_info(olt_dba_profiles)")}
  for column, declaration in (
      ("fix_bw", "INTEGER DEFAULT 0"),
      ("bandwidth_compensate", "TEXT DEFAULT 'no'"),
      ("fix_delay", "TEXT DEFAULT 'no'"),
      ("additional_bandwidth", "TEXT DEFAULT 'best-effort'"),
      ("priority", "INTEGER DEFAULT 0"),
      ("weight", "INTEGER DEFAULT 10"),
      ("assignment_frequency", "TEXT DEFAULT 'single-frame'"),
      ("assignment_period", "INTEGER"),
  ):
    if column not in dba_columns:
      cursor.execute(f"ALTER TABLE olt_dba_profiles ADD COLUMN {column} {declaration}")

  # 4. Tabel ONT Line Profile
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_line_profiles (
            username TEXT,
            profile_id INTEGER,
            profile_name TEXT,
            bind_count INTEGER DEFAULT 0,
            PRIMARY KEY (username, profile_id)
        )
    """)

  # 5. Tabel Detail ONT Line Profile
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_line_profile_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            profile_id INTEGER,
            tcont_id INTEGER,
            dba_profile_id INTEGER,
            gem_id INTEGER,
            vlan_id INTEGER,
            committed INTEGER DEFAULT 0
        )
    """)

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_line_profile_settings (
            username TEXT NOT NULL,
            profile_id INTEGER NOT NULL,
            omcc_encrypt TEXT DEFAULT 'off',
            tr069_enabled INTEGER DEFAULT 0,
            tr069_ip_index INTEGER DEFAULT 1,
            PRIMARY KEY (username, profile_id)
        )
    """)
  lp_setting_columns = {row[1] for row in cursor.execute("PRAGMA table_info(olt_line_profile_settings)")}
  for column, declaration in (
      ("fec_upstream", "TEXT DEFAULT 'disable'"),
      ("qos_mode", "TEXT DEFAULT 'priority-queue'"),
      ("mapping_mode", "TEXT DEFAULT 'vlan'"),
      ("gem_encrypt_mode", "TEXT DEFAULT 'aes-128'"),
  ):
    if column not in lp_setting_columns:
      cursor.execute(f"ALTER TABLE olt_line_profile_settings ADD COLUMN {column} {declaration}")
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_line_profile_tconts (
            username TEXT NOT NULL,
            profile_id INTEGER NOT NULL,
            tcont_id INTEGER NOT NULL,
            dba_profile_id INTEGER NOT NULL,
            PRIMARY KEY (username, profile_id, tcont_id)
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_line_profile_gems (
            username TEXT NOT NULL,
            profile_id INTEGER NOT NULL,
            gem_id INTEGER NOT NULL,
            service_type TEXT DEFAULT 'eth',
            tcont_id INTEGER NOT NULL,
            encrypt TEXT DEFAULT 'off',
            cascade TEXT DEFAULT 'off',
            priority_queue INTEGER DEFAULT 0,
            downstream_priority_queue TEXT DEFAULT 'adapt',
            gem_car_index INTEGER,
            PRIMARY KEY (username, profile_id, gem_id)
        )
    """)
  lp_gem_columns = {row[1] for row in cursor.execute("PRAGMA table_info(olt_line_profile_gems)")}
  for column, declaration in (
      ("cascade", "TEXT DEFAULT 'off'"),
      ("priority_queue", "INTEGER DEFAULT 0"),
      ("downstream_priority_queue", "TEXT DEFAULT 'adapt'"),
      ("gem_car_index", "INTEGER"),
  ):
    if column not in lp_gem_columns:
      cursor.execute(f"ALTER TABLE olt_line_profile_gems ADD COLUMN {column} {declaration}")
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_line_profile_mappings (
            username TEXT NOT NULL,
            profile_id INTEGER NOT NULL,
            gem_id INTEGER NOT NULL,
            mapping_id INTEGER NOT NULL,
            match_type TEXT NOT NULL,
            match_value INTEGER NOT NULL,
            mapping_extra TEXT DEFAULT '',
            PRIMARY KEY (username, profile_id, gem_id, mapping_id)
        )
    """)
  if "mapping_extra" not in {row[1] for row in cursor.execute("PRAGMA table_info(olt_line_profile_mappings)")}:
    cursor.execute("ALTER TABLE olt_line_profile_mappings ADD COLUMN mapping_extra TEXT DEFAULT ''")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_line_profile_vlan_bundles (
      username TEXT NOT NULL, profile_id INTEGER NOT NULL, bundle_id INTEGER NOT NULL,
      vlan_list TEXT NOT NULL, PRIMARY KEY (username, profile_id, bundle_id))""")

  # 6. Tabel ONT Service Profile
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_service_profiles (
            username TEXT,
            profile_id INTEGER,
            profile_name TEXT,
            eth INTEGER,
            pots INTEGER,
            catv INTEGER,
            PRIMARY KEY (username, profile_id)
        )
    """)

  # VLAN policy and multicast behavior configured in an ONT service profile.
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_service_profile_port_vlans (
            username TEXT NOT NULL,
            profile_id INTEGER NOT NULL,
            port_type TEXT NOT NULL DEFAULT 'eth',
            port_id INTEGER NOT NULL,
            vlan_mode TEXT NOT NULL DEFAULT 'access',
            vlan_id INTEGER NOT NULL,
            user_vlan INTEGER,
            PRIMARY KEY (username, profile_id, port_type, port_id)
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_service_profile_multicast (
            username TEXT NOT NULL,
            profile_id INTEGER NOT NULL,
            forward_mode TEXT NOT NULL,
            translation_vlan INTEGER,
            PRIMARY KEY (username, profile_id)
        )
    """)
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_service_profile_settings (
      username TEXT NOT NULL, profile_id INTEGER NOT NULL, settings_json TEXT NOT NULL,
      PRIMARY KEY (username, profile_id))""")

  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_tr069_server_profiles (
      username TEXT NOT NULL, profile_id INTEGER NOT NULL, profile_name TEXT NOT NULL,
      url TEXT NOT NULL, user_name TEXT DEFAULT '', user_password TEXT DEFAULT '',
      auth_realm TEXT DEFAULT '', PRIMARY KEY (username, profile_id),
      UNIQUE (username, profile_name COLLATE NOCASE))""")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_tr069_bindings (
      username TEXT NOT NULL, port TEXT NOT NULL, ont_id INTEGER NOT NULL,
      profile_id INTEGER NOT NULL, PRIMARY KEY (username, port, ont_id))""")

  # 7. Tabel Autofind ONT (Detection Pool)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_autofind (
            username TEXT,
            port TEXT,
            sn TEXT,
            device_type TEXT,
            find_time TEXT,
            PRIMARY KEY (username, port, sn)
        )
    """)

  # 8. Tabel Pool Serial Number ONT
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS ont_sn_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sn TEXT UNIQUE,
            is_used INTEGER DEFAULT 0
        )
    """)

  # 9. Tabel Registered ONTs
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_onts (
            username TEXT,
            port TEXT,
            ont_id INTEGER,
            sn TEXT PRIMARY KEY,
            control_flag TEXT DEFAULT 'active',
            run_state TEXT DEFAULT 'offline',
            config_state TEXT DEFAULT 'initial',
            match_state TEXT DEFAULT 'initial',
            description TEXT DEFAULT '',
            last_down_cause TEXT DEFAULT '',
            rx_power REAL DEFAULT 0.0,
            tx_power REAL DEFAULT 0.0,
            olt_rx_power REAL DEFAULT 0.0,
            line_profile_id INTEGER DEFAULT 1,
            service_profile_id INTEGER DEFAULT 1,
            ont_name TEXT,
            autofind_state INTEGER DEFAULT 0,
            auth_mode TEXT DEFAULT 'sn-auth',
            auth_value TEXT DEFAULT '',
            alarm_profile_id INTEGER DEFAULT 1
        )
    """)

  # Auto Migration: Tambahkan kolom ont_name dan autofind_state jika tabel sudah ada dari versi lama
  try:
    cursor.execute("ALTER TABLE olt_onts ADD COLUMN ont_name TEXT")
  except Exception:
    pass

  try:
    cursor.execute(
        "ALTER TABLE olt_onts ADD COLUMN autofind_state INTEGER DEFAULT 0"
    )
  except Exception:
    pass
  for column, definition in (
      ("auth_mode", "TEXT DEFAULT 'sn-auth'"),
      ("auth_value", "TEXT DEFAULT ''"),
      ("alarm_profile_id", "INTEGER DEFAULT 1"),
  ):
    try:
      cursor.execute(f"ALTER TABLE olt_onts ADD COLUMN {column} {definition}")
    except Exception:
      pass

  # 10. Tabel Service Port
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_service_ports (
            username TEXT,
            sp_id INTEGER,
            vlan INTEGER,
            port_type TEXT DEFAULT 'gpon',
            port TEXT,
            ont_id INTEGER,
            gemport INTEGER,
            user_vlan INTEGER,
            state TEXT DEFAULT 'up',
            inbound_traffic_index INTEGER,
            outbound_traffic_index INTEGER,
            PRIMARY KEY (username, sp_id)
        )
    """)
  for column in ("inbound_traffic_index", "outbound_traffic_index"):
    try:
      cursor.execute(
          f"ALTER TABLE olt_service_ports ADD COLUMN {column} INTEGER"
      )
    except Exception:
      pass
  for column, definition in (
      ("uplink_port", "TEXT DEFAULT ''"),
      ("dedicated_net_id", "INTEGER"),
      ("tag_transform", "TEXT DEFAULT 'default'"),
      ("bundle_id", "INTEGER"),
      ("cos", "INTEGER"),
      ("options_json", "TEXT DEFAULT '{}'"),
  ):
    try:
      cursor.execute(f"ALTER TABLE olt_service_ports ADD COLUMN {column} {definition}")
    except Exception:
      pass

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_traffic_tables (
            username TEXT NOT NULL,
            profile_id INTEGER NOT NULL,
            cir TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            priority_policy TEXT NOT NULL DEFAULT 'local-setting',
            profile_name TEXT DEFAULT '',
            options_json TEXT DEFAULT '{}',
            PRIMARY KEY (username, profile_id)
        )
    """)
  for column, definition in (
      ("profile_name", "TEXT DEFAULT ''"),
      ("options_json", "TEXT DEFAULT '{}'"),
  ):
    try:
      cursor.execute(f"ALTER TABLE olt_traffic_tables ADD COLUMN {column} {definition}")
    except Exception:
      pass
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_vlanif_settings (
            username TEXT NOT NULL,
            vlan_id INTEGER NOT NULL,
            arp_proxy INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (username, vlan_id)
        )
    """)
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_vlanifs (
      username TEXT NOT NULL, vlan_id INTEGER NOT NULL,
      PRIMARY KEY (username, vlan_id))""")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_vlanif_addresses (
      username TEXT NOT NULL, vlan_id INTEGER NOT NULL, address TEXT NOT NULL,
      mask TEXT NOT NULL, secondary INTEGER NOT NULL DEFAULT 0,
      tag INTEGER, description TEXT DEFAULT '',
      PRIMARY KEY (username, vlan_id, address))""")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_node_vlanifs (
      username TEXT NOT NULL, node_name TEXT NOT NULL, vlan_id INTEGER NOT NULL,
      PRIMARY KEY (username, node_name, vlan_id))""")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_node_vlanif_addresses (
      username TEXT NOT NULL, node_name TEXT NOT NULL, vlan_id INTEGER NOT NULL,
      address TEXT NOT NULL, mask TEXT NOT NULL, secondary INTEGER NOT NULL DEFAULT 0,
      tag INTEGER, description TEXT DEFAULT '',
      PRIMARY KEY (username, node_name, vlan_id, address))""")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_node_vlan_ports (
      username TEXT NOT NULL, node_name TEXT NOT NULL, vlan_id INTEGER NOT NULL,
      port TEXT NOT NULL, PRIMARY KEY (username, node_name, vlan_id, port))""")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_eth_native_vlans (
      username TEXT NOT NULL, node_name TEXT NOT NULL, board TEXT NOT NULL,
      port_id INTEGER NOT NULL, vlan_id INTEGER NOT NULL,
      dedicated_net_id INTEGER,
      PRIMARY KEY (username, node_name, board, port_id))""")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_eth_port_settings (
      username TEXT NOT NULL, node_name TEXT NOT NULL, board TEXT NOT NULL,
      port_id INTEGER NOT NULL, speed INTEGER,
      auto_neg INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY (username, node_name, board, port_id))""")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_link_aggregations (
      username TEXT NOT NULL, node_name TEXT NOT NULL, master_port TEXT NOT NULL,
      mode TEXT NOT NULL DEFAULT 'ingress', workmode TEXT NOT NULL DEFAULT 'manual',
      max_link_number INTEGER NOT NULL DEFAULT 2,
      least_link_number INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY (username, node_name, master_port))""")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_link_aggregation_members (
      username TEXT NOT NULL, node_name TEXT NOT NULL, master_port TEXT NOT NULL,
      board TEXT NOT NULL, port_id INTEGER NOT NULL, member_port TEXT NOT NULL,
      PRIMARY KEY (username, node_name, member_port))""")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_node_routes (
      username TEXT NOT NULL, node_name TEXT NOT NULL, dest TEXT NOT NULL,
      mask TEXT NOT NULL, gateway TEXT DEFAULT '', preference INTEGER DEFAULT 60,
      tag INTEGER DEFAULT 0, cost INTEGER DEFAULT 0, permanent INTEGER DEFAULT 0,
      outbound_interface TEXT DEFAULT '', description TEXT DEFAULT '',
      options_json TEXT DEFAULT '{}',
      PRIMARY KEY (username, node_name, dest, mask, gateway, outbound_interface))""")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_node_settings (
      username TEXT NOT NULL, node_name TEXT NOT NULL, sysname TEXT NOT NULL,
      PRIMARY KEY (username, node_name))""")
  cursor.execute("""CREATE TABLE IF NOT EXISTS olt_device_links (
      username TEXT NOT NULL, source_node TEXT NOT NULL, target_node TEXT NOT NULL,
      source_port TEXT NOT NULL, target_port TEXT NOT NULL,
      fiber_type TEXT DEFAULT 'SMF', sfp_module TEXT DEFAULT '', length_km REAL DEFAULT 0,
      is_up INTEGER DEFAULT 1,
      PRIMARY KEY (username, source_node, source_port, target_node, target_port))""")
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_multicast_vlans (
            username TEXT NOT NULL,
            vlan_id INTEGER NOT NULL,
            igmp_version TEXT DEFAULT 'v2',
            igmp_mode TEXT DEFAULT 'off',
            uplink_port TEXT DEFAULT '',
            uplink_port_mode TEXT DEFAULT 'default',
            PRIMARY KEY (username, vlan_id)
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_multicast_programs (
            username TEXT NOT NULL,
            vlan_id INTEGER NOT NULL,
            program_name TEXT NOT NULL,
            program_ip TEXT NOT NULL,
            source_ip TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (username, vlan_id, program_name)
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_multicast_profiles (
            username TEXT NOT NULL,
            vlan_id INTEGER NOT NULL,
            profile_name TEXT NOT NULL,
            PRIMARY KEY (username, vlan_id, profile_name)
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_multicast_profile_programs (
            username TEXT NOT NULL,
            vlan_id INTEGER NOT NULL,
            profile_name TEXT NOT NULL,
            program_name TEXT NOT NULL,
            permission TEXT NOT NULL DEFAULT 'watch',
            PRIMARY KEY (username, vlan_id, profile_name, program_name)
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_multicast_users (
            username TEXT NOT NULL,
            vlan_id INTEGER NOT NULL,
            service_port_id INTEGER NOT NULL,
            auth_mode TEXT NOT NULL DEFAULT 'auth',
            profile_name TEXT,
            PRIMARY KEY (username, vlan_id, service_port_id)
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_multicast_members (
            username TEXT NOT NULL,
            vlan_id INTEGER NOT NULL,
            service_port_id INTEGER NOT NULL,
            PRIMARY KEY (username, vlan_id, service_port_id)
        )
    """)

  # 11. Tabel VLAN
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_vlan (
            username TEXT,
            vlan_id INTEGER,
            vlan_type TEXT,
            vlan_attr TEXT DEFAULT 'common',
            bound_port TEXT DEFAULT '',
            bound_state TEXT DEFAULT '',
            bound_tag TEXT DEFAULT '',
            description TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            PRIMARY KEY (username, vlan_id)
        )
    """)
  for column, definition in (
      ("dedicated_net_id", "INTEGER DEFAULT 0"),
      ("vlan_name", "TEXT DEFAULT ''"),
  ):
    try:
      cursor.execute(f"ALTER TABLE olt_vlan ADD COLUMN {column} {definition}")
    except Exception:
      pass

  # 12. Tabel Static Route
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_routes (
            username TEXT,
            dest TEXT,
            mask TEXT,
            gateway TEXT,
            description TEXT DEFAULT '',
            PRIMARY KEY (username, dest, mask, gateway)
        )
    """)
  for column, definition in (
      ("preference", "INTEGER DEFAULT 60"),
      ("tag", "INTEGER DEFAULT 0"),
      ("cost", "INTEGER DEFAULT 0"),
      ("permanent", "INTEGER DEFAULT 0"),
      ("outbound_interface", "TEXT DEFAULT ''"),
      ("options_json", "TEXT DEFAULT '{}'"),
  ):
    try:
      cursor.execute(f"ALTER TABLE olt_routes ADD COLUMN {column} {definition}")
    except Exception:
      pass

  # 13. Tabel Sistem Konfigurasi & SNMP Target
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_system_config (
            username TEXT,
            key TEXT,
            value TEXT,
            PRIMARY KEY (username, key)
        )
    """)

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS olt_snmp_targets (
            username TEXT,
            hostname TEXT,
            address TEXT,
            params_name TEXT,
            version TEXT,
            PRIMARY KEY (username, hostname)
        )
    """)

  # Populate Pool Serial Number ONT jika belum ada
  for sn in ONT_SN_POOL:
    cursor.execute(
        "INSERT OR IGNORE INTO ont_sn_pool (sn, is_used) VALUES (?, 0)", (sn,)
    )

  conn.commit()
  conn.close()


def get_next_available_sn():
  """Mengambil Serial Number berikutnya yang masih bebas dari database pool"""
  conn = get_connection()
  cursor = conn.cursor()
  cursor.execute("SELECT sn FROM ont_sn_pool WHERE is_used = 0 LIMIT 1")
  row = cursor.fetchone()

  if row:
    sn = row[0]
    cursor.execute("UPDATE ont_sn_pool SET is_used = 1 WHERE sn = ?", (sn,))
    conn.commit()
    conn.close()
    return sn

  conn.close()
  return "414C434C99999999"  # Fallback default


def release_sn(sn):
  """Mengembalikan Serial Number ke pool jika node/kabel dihapus"""
  conn = get_connection()
  cursor = conn.cursor()
  cursor.execute("UPDATE ont_sn_pool SET is_used = 0 WHERE sn = ?", (sn,))
  cursor.execute("DELETE FROM olt_onts WHERE sn = ?", (sn,))
  conn.commit()
  conn.close()


def reset_user_data(username="admin"):
  """Clear one user's simulator state and reseed the initial device data."""
  init_db()
  conn = get_connection()
  try:
    cursor = conn.cursor()
    registered_sns = [
        row[0] for row in cursor.execute(
            "SELECT sn FROM olt_onts WHERE username = ?", (username,)
        ).fetchall()
    ]
    if registered_sns:
      placeholders = ",".join("?" for _ in registered_sns)
      cursor.execute(
          f"UPDATE ont_sn_pool SET is_used = 0 WHERE sn IN ({placeholders})",
          registered_sns,
      )

    tables = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'olt_%'"
    ).fetchall()
    for (table_name,) in tables:
      columns = [
          row[1] for row in cursor.execute(f'PRAGMA table_info("{table_name}")')
      ]
      if "username" in columns:
        cursor.execute(f'DELETE FROM "{table_name}" WHERE username = ?', (username,))
    conn.commit()
  finally:
    conn.close()

  # Recreate the factory/default profiles, VLANs, boards, and autofind entries.
  init_user_data(username)


def init_user_data(username):
  """Membuat data default awal khusus untuk user yang baru login"""
  init_db()

  conn = get_connection()
  cursor = conn.cursor()

  cursor.executemany(
      "INSERT OR IGNORE INTO olt_traffic_tables "
      "(username,profile_id,cir,priority,priority_policy,profile_name,options_json) "
      "VALUES (?, ?, 'off', 0, 'local-setting', ?, '{}')",
      [(username, profile_id, f"ip-traffic-table_{profile_id}") for profile_id in range(7)],
  )

  # 1. Seed Boards
  # Keep all factory board rows present even if a previous database only
  # contained a partial inventory (for example, slot 0 only).
  initial_boards = [
      (username, 0, "V921CIUA", "Normal", "", "", ""),
      (username, 1, "V921GPHF", "Normal", "", "", ""),
      (username, 2, "V923NXHC", "config", "", "", ""),
      (username, 3, "V921MPCC", "Active_normal", "", "", ""),
      (username, 4, "PDC300S12", "Normal", "", "", ""),
      (username, 5, "PDC300S12", "Normal", "", "", ""),
  ]
  cursor.executemany(
      """
          INSERT OR IGNORE INTO olt_boards
          (username, slot_id, board_name, status, subtype0, subtype1, online_offline)
          VALUES (?, ?, ?, ?, ?, ?, ?)
      """,
      initial_boards,
  )

  # 2. Seed Autofind ONT
  cursor.execute(
      "SELECT COUNT(*) FROM olt_autofind WHERE username = ?", (username,)
  )
  if cursor.fetchone()[0] == 0:
    cursor.execute(
        """
            INSERT OR REPLACE INTO olt_autofind (username, port, sn, device_type, find_time)
            VALUES (?, ?, ?, ?, ?)
        """,
        (
            username,
            "0/1/0",
            "414C434CB46E0A86",
            "G-240W-F",
            "2026-10-16 17:00:00",
        ),
    )
  # Example ONTs from the simplified triple-play configuration guide.
  cursor.executemany(
      "INSERT OR IGNORE INTO olt_autofind "
      "(username, port, sn, device_type, find_time) VALUES (?, ?, ?, ?, ?)",
      [
          (username, "0/1/1", "3230313126595540", "EchoLife:HG8245", "2009-10-24 14:59:10"),
          (username, "0/1/1", "6877687714852901", "EchoLife:HG8245", "2009-10-24 14:59:12"),
      ],
  )

  # 3. Seed Default DBA Profiles
  cursor.execute(
      "SELECT COUNT(*) FROM olt_dba_profiles WHERE username = ?", (username,)
  )
  if cursor.fetchone()[0] == 0:
    dba_list = [
        (username, 0, "dba-profile_0", "type3", 0, 8192, 20480, 1),
        (username, 1, "dba-profile_1", "type1", 5120, 0, 0, 3),
        (username, 2, "dba-profile_2", "type1", 1024, 0, 0, 0),
        (username, 3, "dba-profile_3", "type4", 0, 0, 32768, 0),
        (username, 4, "dba-profile_4", "type1", 1024000, 0, 0, 0),
        (username, 5, "dba-profile_5", "type1", 32768, 0, 0, 0),
        (username, 6, "dba-profile_6", "type1", 102400, 0, 0, 0),
        (username, 7, "dba-profile_7", "type2", 0, 32768, 0, 0),
        (username, 8, "dba-profile_8", "type2", 0, 102400, 0, 0),
        (username, 9, "dba-profile_9", "type3", 0, 32768, 65536, 0),
        (username, 10, "ICONNET.PPPOE", "type4", 0, 0, 204800, 25),
        (username, 11, "Bandwidth_Home_20M", "type2", 0, 20480, 0, 8),
    ]
    cursor.executemany(
        """
            INSERT OR REPLACE INTO olt_dba_profiles 
            (username, profile_id, profile_name, type, fix_bw, assured, max_bw, bind_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        dba_list,
    )
  # Fill any missing vendor defaults without overwriting profiles already configured.
  cursor.executemany(
      "INSERT OR IGNORE INTO olt_dba_profiles "
      "(username, profile_id, profile_name, type, fix_bw, assured, max_bw, bind_count) "
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
      [
          (username, 0, "dba-profile_0", "type3", 0, 8192, 20480, 1),
          (username, 1, "dba-profile_1", "type1", 5120, 0, 0, 3),
          (username, 2, "dba-profile_2", "type1", 1024, 0, 0, 0),
          (username, 3, "dba-profile_3", "type4", 0, 0, 32768, 0),
          (username, 4, "dba-profile_4", "type1", 1024000, 0, 0, 0),
          (username, 5, "dba-profile_5", "type1", 32768, 0, 0, 0),
          (username, 6, "dba-profile_6", "type1", 102400, 0, 0, 1),
          (username, 7, "dba-profile_7", "type2", 0, 32768, 0, 0),
          (username, 8, "dba-profile_8", "type2", 0, 102400, 0, 0),
          (username, 9, "dba-profile_9", "type3", 0, 32768, 65536, 0),
      ],
  )

  # 4. Seed Line Profiles
  cursor.execute(
      "SELECT COUNT(*) FROM olt_line_profiles WHERE username = ?", (username,)
  )
  if cursor.fetchone()[0] == 0:
    cursor.execute(
        """
            INSERT OR REPLACE INTO olt_line_profiles 
            (username, profile_id, profile_name, bind_count) 
            VALUES (?, ?, ?, ?)
        """,
        (username, 1, "AUTOPROV.PPPOE", 1),
    )
    cursor.execute(
        """
            INSERT OR REPLACE INTO olt_line_profile_details 
            (username, profile_id, tcont_id, dba_profile_id, gem_id, vlan_id, committed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (username, 1, 1, 10, 1, 2921, 1),
    )
  cursor.execute(
      "INSERT OR IGNORE INTO olt_line_profiles "
      "(username, profile_id, profile_name, bind_count) VALUES (?, 0, ?, 0)",
      (username, "line-profile_default_0"),
  )
  cursor.execute(
      "INSERT OR IGNORE INTO olt_line_profile_settings "
      "(username, profile_id, omcc_encrypt, tr069_enabled, tr069_ip_index, "
      "fec_upstream, qos_mode, mapping_mode, gem_encrypt_mode) "
      "VALUES (?, 0, 'off', 0, 0, 'disable', 'gem-car', 'vlan', 'aes-128')",
      (username,),
  )
  cursor.executemany(
      "INSERT OR IGNORE INTO olt_line_profile_tconts "
      "(username, profile_id, tcont_id, dba_profile_id) VALUES (?, 0, ?, ?)",
      [(username, 0, 2), (username, 1, 0)],
  )
  cursor.execute(
      "INSERT OR IGNORE INTO olt_line_profile_settings "
      "(username, profile_id, omcc_encrypt, tr069_enabled, tr069_ip_index) "
      "SELECT username, profile_id, 'on', 0, 0 FROM olt_line_profiles "
      "WHERE username = ? AND profile_id = 1",
      (username,),
  )
  cursor.execute(
      "INSERT OR IGNORE INTO olt_line_profile_tconts "
      "(username, profile_id, tcont_id, dba_profile_id) "
      "SELECT username, profile_id, tcont_id, dba_profile_id "
      "FROM olt_line_profile_details WHERE username = ? AND profile_id = 1",
      (username,),
  )
  cursor.execute(
      "INSERT OR IGNORE INTO olt_line_profile_gems "
      "(username, profile_id, gem_id, service_type, tcont_id, encrypt) "
      "SELECT username, profile_id, gem_id, 'eth', tcont_id, 'on' "
      "FROM olt_line_profile_details WHERE username = ? AND profile_id = 1",
      (username,),
  )
  cursor.execute(
      "INSERT OR IGNORE INTO olt_line_profile_mappings "
      "(username, profile_id, gem_id, mapping_id, match_type, match_value) "
      "SELECT username, profile_id, gem_id, 0, 'vlan', vlan_id "
      "FROM olt_line_profile_details WHERE username = ? AND profile_id = 1",
      (username,),
  )

  # 5. Seed Service Profiles
  cursor.execute(
      "INSERT OR IGNORE INTO olt_service_profiles "
      "(username, profile_id, profile_name, eth, pots, catv) VALUES (?, 0, 'Default', 0, 0, 0)",
      (username,),
  )
  cursor.execute(
      "SELECT COUNT(*) FROM olt_service_profiles WHERE username = ?",
      (username,),
  )
  if cursor.fetchone()[0] == 0:
    cursor.execute(
        """
            INSERT OR REPLACE INTO olt_service_profiles 
            (username, profile_id, profile_name, eth, pots, catv) 
            VALUES (?, ?, ?, ?, ?, ?)
        """,
        (username, 1, "AUTOPROV.PPPOE", 8, 32, 0),
    )

  # 6. Seed Default VLANs
  cursor.execute(
      "INSERT OR IGNORE INTO olt_vlan (username, vlan_id, vlan_type, vlan_attr, description) "
      "VALUES (?, 1, 'standard', 'common', 'Default VLAN')",
      (username,),
  )
  cursor.execute(
      "SELECT COUNT(*) FROM olt_vlan WHERE username = ?", (username,)
  )
  if cursor.fetchone()[0] == 0:
    cursor.execute(
        """
            INSERT OR REPLACE INTO olt_vlan (username, vlan_id, vlan_type, vlan_attr, bound_port, bound_state, bound_tag, description, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            username,
            13,
            "standard",
            "common",
            "0/2/0",
            "up",
            "tag",
            "=VLAN_NMS_U2000=",
            "",
        ),
    )
    cursor.execute(
        """
            INSERT OR REPLACE INTO olt_vlan (username, vlan_id, vlan_type, vlan_attr, bound_port, bound_state, bound_tag, description, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            username,
            2921,
            "smart",
            "common",
            "0/2/0",
            "up",
            "tag",
            "=VLAN_RADIUS",
            "",
        ),
    )

  conn.commit()
  conn.close()
