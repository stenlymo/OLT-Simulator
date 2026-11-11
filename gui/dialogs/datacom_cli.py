"""Huawei Datacom router/switch CLI for lab-guide configuration labs."""
import hashlib
import hmac
import json
import re
import secrets
import ipaddress
import copy

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from modules.database import get_connection
from gui.dialogs.cli_terminal import AuthenticCLITerminal


def ipaddress_prefix(mask):
  if not mask:
    return ""
  try:
    return str(ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen)
  except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
    return str(mask)


class DatacomTerminal(QWidget):
  def __init__(self, node_name, device_type, parent=None):
    super().__init__(parent)
    self.node_name = node_name
    self.device_type = device_type
    self.username = "admin"
    self.is_switch = "switch" in device_type.lower()
    self.is_router = not self.is_switch
    self.sysname = "HUAWEI"
    self.mode = "USER"
    self.interface = None
    self.active_vlan = None
    self.active_dhcp_pool = None
    self.active_ospf_process = None
    self.active_ospfv3_process = None
    self.active_ospf_area = None
    self.active_isis_process = None
    self.active_bgp_as = None
    self.active_bgp_peer = None
    self.active_route_policy = None
    self.active_vpn_instance = None
    self.active_bridge_domain = None
    self.active_evpn_instance = None
    self.active_explicit_path = None
    self.active_segment_list = None
    self.active_sr_policy = None
    self.active_sr_candidate = None
    self.active_tunnel_policy = None
    self.interface_ports = []
    self.pending = None
    self.suppress_command_history = True
    self.authenticated = False
    self.password_salt = ""
    self.password_hash = ""
    self.running = self._default_config()
    self.saved = None
    self.files = {}
    self.startup_file = ""
    self._storage_dirty = False
    self.boot_count = 0
    self._load_state()
    layout = QVBoxLayout(self)
    layout.setContentsMargins(0, 0, 0, 0)
    self.terminal = AuthenticCLITerminal(self)
    layout.addWidget(self.terminal)
    self._start_session()

  def _default_config(self):
    ports = range(1, 25) if self.is_switch else range(0, 8)
    prefix = "GE1/0/" if self.is_switch else "GE0/0/"
    interface_names = [f"{prefix}{i}" for i in ports]
    if not self.is_switch:
      interface_names.extend(f"GE0/5/{i}" for i in range(0, 8))
    return {"sysname": "HUAWEI", "vlans": [1],
            "stp": {"enabled": True, "mode": "mstp", "priority": 32768, "root_role": ""},
            "lacp_priority": 32768, "eth_trunks": {}, "static_routes": [],
            "dhcp_enabled": False, "dhcp_pools": {},
            "ospf_processes": {},
            "ospfv3_processes": {},
            "isis_processes": {}, "bgp": None,
            "vpn_instances": {}, "mpls_enabled": False, "mpls_ldp_enabled": False,
            "mpls_lsr_id": "",
            "mpls_te_enabled": False,
            "segment_routing": {"enabled": False, "adjacency_sids": []},
            "sr_mpls": {"explicit_paths": {}, "segment_lists": {}, "policies": {}},
            "tunnel_policies": {},
            "ip_prefix_lists": {}, "community_filters": {}, "as_path_filters": {},
            "route_policies": {},
            "bridge_domains": {}, "nve": {"source": "", "vnis": {}},
            "evpn_instances": {}, "vcmp_role": "",
            "campus": {"fabric": "", "role": "", "as_number": None, "vns": {}},
            "ftp_server_enabled": False, "ftp_server_source": "all-interface", "aaa_users": {},
            "telnet_server_enabled": False, "telnet_server_source": "all-interface",
            "stelnet_server_enabled": False, "ssh_server_source": "all-interface",
            "ssh_users": {}, "ssh_client_first_time_enabled": False, "ssh_known_hosts": {},
            "authentication_schemes": {}, "authorization_schemes": {}, "accounting_schemes": {},
            "aaa_domains": {}, "vty_interfaces": {},
            "nat_address_groups": {}, "nat_policy_rules": {}, "nat_servers": {},
            "wlan": {"capwap_source_interface": "", "ap_auth_mode": "mac-auth", "aps": {},
                     "ap_groups": {}, "regulatory_profiles": {}, "security_profiles": {},
                     "ssid_profiles": {}, "vap_profiles": {}},
            "acls": {}, "traffic_classifiers": {}, "traffic_behaviors": {}, "traffic_policies": {},
            "ipv6_static_routes": [], "ipv6_neighbors": {},
            "interfaces": {
        name: {"ip": "", "mask": "", "description": "", "shutdown": False,
                          "link_type": "access", "pvid": 1,
                          "ipv6_enabled": False, "ipv6_addresses": [],
                          "ipv6_link_local_auto": False, "ipv6_auto_global": False,
                          "ipv6_auto_global_default": False, "ipv6_ra_halt": True,
                          "ipv6_ra_prefix": None,
                          "dhcp_server_gateway": "", "dhcp_server_dns": "", "dhcp_option_43": "",
                          "traffic_policies": {},
                          "ospf_cost": 1, "ospf_auth": None, "ospf_enabled": False,
                          "ospf_process": "1", "ospf_area": "0",
                          "ospf_bfd": False, "isis_process": None, "isis_level": "level-1-2",
                          "ospfv3_process": None, "ospfv3_area": "0", "ospfv3_instance": 1,
                          "isis_ipv6_process": None,
                          "allowed_vlans": [1], "tagged_vlans": [], "untagged_vlans": [1],
                          "port_link_type_removed": False,
                          "eth_trunk": None, "lacp_priority": 32768,
                          "stp_enabled": True, "stp_cost": 20000,
                          "stp_port_priority": 128, "stp_edge": False}
        for name in interface_names
    }}

  def _ensure_table(self, conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS olt_datacom_devices (
      username TEXT NOT NULL, node_name TEXT NOT NULL, device_type TEXT NOT NULL,
      password_salt TEXT NOT NULL DEFAULT '', password_hash TEXT NOT NULL DEFAULT '',
      sysname TEXT NOT NULL DEFAULT 'HUAWEI', running_json TEXT NOT NULL,
      saved_json TEXT NOT NULL DEFAULT '', files_json TEXT NOT NULL DEFAULT '{}',
      startup_file TEXT NOT NULL DEFAULT '', boot_count INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(username,node_name))""")

  def _load_state(self):
    conn = get_connection()
    try:
      self._ensure_table(conn)
      row = conn.execute("SELECT device_type,password_salt,password_hash,sysname,running_json,saved_json,files_json,startup_file,boot_count FROM olt_datacom_devices WHERE username=? AND node_name=?", (self.username, self.node_name)).fetchone()
      if row:
        self.password_salt, self.password_hash = row[1], row[2]
        self.sysname = row[3] or "HUAWEI"
        self.running = json.loads(row[4])
        self.saved = json.loads(row[5]) if row[5] else None
        self.files = json.loads(row[6] or "{}")
        self.startup_file, self.boot_count = row[7], row[8]
        self.running.setdefault("interfaces", self._default_config()["interfaces"])
        if not self.is_switch:
          for name, iface in self._default_config()["interfaces"].items():
            self.running["interfaces"].setdefault(name, iface.copy())
        self.running["sysname"] = self.sysname
        self.running.setdefault("stp", self._default_config()["stp"])
        self.running.setdefault("eth_trunks", {})
        self.running.setdefault("lacp_priority", 32768)
        self.running.setdefault("static_routes", [])
        self.running.setdefault("dhcp_enabled", False)
        self.running.setdefault("dhcp_pools", {})
        self.running.setdefault("ospf_processes", {})
        self.running.setdefault("ospfv3_processes", {})
        self.running.setdefault("isis_processes", {})
        self.running.setdefault("vpn_instances", {})
        self.running.setdefault("mpls_enabled", False)
        self.running.setdefault("mpls_ldp_enabled", False)
        self.running.setdefault("mpls_lsr_id", "")
        self.running.setdefault("mpls_te_enabled", False)
        self.running.setdefault("segment_routing", {"enabled": False, "adjacency_sids": []})
        self.running["segment_routing"].setdefault("adjacency_sids", [])
        self.running.setdefault("sr_mpls", {"explicit_paths": {}, "segment_lists": {}, "policies": {}})
        for key in ("explicit_paths", "segment_lists", "policies"):
          self.running["sr_mpls"].setdefault(key, {})
        self.running.setdefault("tunnel_policies", {})
        self.running.setdefault("bgp", None)
        self.running.setdefault("ip_prefix_lists", {})
        self.running.setdefault("community_filters", {})
        self.running.setdefault("as_path_filters", {})
        self.running.setdefault("route_policies", {})
        self.running.setdefault("bridge_domains", {})
        self.running.setdefault("nve", {"source": "", "vnis": {}})
        self.running["nve"].setdefault("vnis", {})
        self.running.setdefault("evpn_instances", {})
        self.running.setdefault("vcmp_role", "")
        self.running.setdefault("campus", {"fabric": "", "role": "", "as_number": None, "vns": {}})
        self.running.setdefault("ftp_server_enabled", False)
        self.running.setdefault("ftp_server_source", "all-interface")
        self.running.setdefault("ftp_server_acl", "")
        self.running.setdefault("aaa_users", {})
        self.running.setdefault("telnet_server_enabled", False)
        self.running.setdefault("telnet_server_source", "all-interface")
        self.running.setdefault("stelnet_server_enabled", False)
        self.running.setdefault("ssh_server_source", "all-interface")
        self.running.setdefault("ssh_server_port", 22)
        self.running.setdefault("lldp_enabled", True)
        self.running.setdefault("ztp_enabled", True)
        self.running.setdefault("ssh_users", {})
        self.running.setdefault("ssh_client_first_time_enabled", False)
        self.running.setdefault("ssh_known_hosts", {})
        self.running.setdefault("authentication_schemes", {})
        self.running.setdefault("authorization_schemes", {})
        self.running.setdefault("accounting_schemes", {})
        self.running.setdefault("aaa_domains", {})
        self.running.setdefault("vty_interfaces", {})
        self.running.setdefault("nat_address_groups", {})
        self.running.setdefault("nat_policy_rules", {})
        self.running.setdefault("nat_servers", {})
        self.running.setdefault("wlan", {"capwap_source_interface": "", "ap_auth_mode": "mac-auth", "aps": {},
                                           "ap_groups": {}, "regulatory_profiles": {}, "security_profiles": {},
                                           "ssid_profiles": {}, "vap_profiles": {}})
        for key in ("aps", "ap_groups", "regulatory_profiles", "security_profiles", "ssid_profiles", "vap_profiles"):
          self.running["wlan"].setdefault(key, {})
        for user in self.running["aaa_users"].values():
          user.setdefault("password_force_change", False)
        self.running.setdefault("acls", {})
        self.running.setdefault("traffic_classifiers", {})
        self.running.setdefault("traffic_behaviors", {})
        self.running.setdefault("traffic_policies", {})
        self.running.setdefault("ipv6_static_routes", [])
        self.running.setdefault("ipv6_neighbors", {})
        for iface in self.running["interfaces"].values():
          iface.setdefault("dhcp_mode", "")
          iface.setdefault("dhcp_select", "")
          iface.setdefault("dhcp_server_gateway", "")
          iface.setdefault("dhcp_server_dns", "")
          iface.setdefault("dhcp_option_43", "")
          iface.setdefault("port_link_type_removed", False)
          iface.setdefault("ospf_cost", 1)
          iface.setdefault("ospf_auth", None)
          iface.setdefault("ospf_enabled", False)
          iface.setdefault("ospf_process", "1")
          iface.setdefault("ospf_area", "0")
          iface.setdefault("ospf_bfd", False)
          iface.setdefault("isis_process", None)
          iface.setdefault("isis_level", "level-1-2")
          iface.setdefault("ospfv3_process", None)
          iface.setdefault("ospfv3_area", "0")
          iface.setdefault("ospfv3_instance", 1)
          iface.setdefault("isis_ipv6_process", None)
          iface.setdefault("ipv6_enabled", False)
          iface.setdefault("ipv6_addresses", [])
          iface.setdefault("ipv6_link_local_auto", False)
          iface.setdefault("ipv6_auto_global", False)
          iface.setdefault("ipv6_auto_global_default", False)
          iface.setdefault("ipv6_ra_halt", True)
          iface.setdefault("ipv6_ra_prefix", None)
          iface.setdefault("traffic_policies", {})
          iface.setdefault("eth_trunk", None)
          iface.setdefault("lacp_priority", 32768)
          iface.setdefault("shutdown", False)
          iface.setdefault("stp_enabled", True)
          iface.setdefault("stp_cost", 20000)
          iface.setdefault("stp_port_priority", 128)
          iface.setdefault("stp_edge", False)
      else:
        self._persist(conn)
      conn.commit()
    finally:
      conn.close()

  def _persist(self, conn=None):
    own = conn is None
    conn = conn or get_connection()
    try:
      self._ensure_table(conn)
      existing = conn.execute("SELECT saved_json,files_json,startup_file FROM olt_datacom_devices WHERE username=? AND node_name=?",
                              (self.username, self.node_name)).fetchone()
      if existing and not self._storage_dirty:
        self.saved = json.loads(existing[0]) if existing[0] else None
        self.files = json.loads(existing[1] or "{}")
        self.startup_file = existing[2] or ""
      conn.execute("""INSERT OR REPLACE INTO olt_datacom_devices
        (username,node_name,device_type,password_salt,password_hash,sysname,running_json,saved_json,files_json,startup_file,boot_count)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (self.username, self.node_name, self.device_type,
        self.password_salt, self.password_hash, self.sysname, json.dumps(self.running),
        json.dumps(self.saved) if self.saved is not None else "", json.dumps(self.files), self.startup_file, self.boot_count))
      self._storage_dirty = False
      if own:
        conn.commit()
    finally:
      if own:
        conn.close()

  def _refresh_storage(self):
    conn = get_connection()
    try:
      row = conn.execute("SELECT saved_json,files_json,startup_file FROM olt_datacom_devices WHERE username=? AND node_name=?",
                         (self.username, self.node_name)).fetchone()
      if row:
        self.saved = json.loads(row[0]) if row[0] else None
        self.files = json.loads(row[1] or "{}")
        self.startup_file = row[2] or ""
        self._storage_dirty = False
    finally:
      conn.close()

  def _start_session(self):
    self.terminal.clear()
    self.prompt_position = 0
    if not self.password_hash:
      self.pending = "setup_password"
      self._write("Please configure the login password (8-16)\nEnter Password:")
      self.prompt_position = len(self.terminal.toPlainText())
      self.suppress_command_history = True
    else:
      self.pending = "login_password"
      self._write("Password:")
      self.prompt_position = len(self.terminal.toPlainText())
      self.suppress_command_history = True

  def _write(self, text):
    cursor = self.terminal.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(text)
    self.terminal.setTextCursor(cursor)

  def append_plain_text(self, text):
    self._write(text + "\n")

  def _prompt(self):
    if self.mode == "USER":
      prompt = f"<{self.sysname}>"
    elif self.mode == "SYSTEM":
      prompt = f"[{self.sysname}]"
    elif self.mode == "VLAN":
      prompt = f"[{self.sysname}-vlan{self.active_vlan}]"
    elif self.mode == "ETH_TRUNK":
      prompt = f"[{self.sysname}-Eth-Trunk{self.active_trunk}]"
    elif self.mode == "DHCP_POOL":
      prompt = f"[{self.sysname}-ip-pool-{self.active_dhcp_pool}]"
    elif self.mode == "OSPF":
      prompt = f"[{self.sysname}-ospf-{self.active_ospf_process}]"
    elif self.mode == "OSPFV3":
      prompt = f"[{self.sysname}-ospfv3-{self.active_ospfv3_process}]"
    elif self.mode == "OSPF_AREA":
      prompt = f"[{self.sysname}-ospf-{self.active_ospf_process}-area-{self.active_ospf_area}]"
    elif self.mode == "OSPF_FRR":
      prompt = f"[{self.sysname}-ospf-{self.active_ospf_process}-frr]"
    elif self.mode == "ISIS":
      prompt = f"[{self.sysname}-isis-{self.active_isis_process}]"
    elif self.mode == "BGP":
      prompt = f"[{self.sysname}-bgp]"
    elif self.mode == "BGP_AF":
      prompt = f"[{self.sysname}-bgp-af-ipv4]"
    elif self.mode == "BGP_IPV6_AF":
      prompt = f"[{self.sysname}-bgp-af-ipv6]"
    elif self.mode == "BFD":
      prompt = f"[{self.sysname}-bfd]"
    elif self.mode == "VPN_INSTANCE":
      prompt = f"[{self.sysname}-vpn-instance-{self.active_vpn_instance}]"
    elif self.mode == "MPLS":
      prompt = f"[{self.sysname}-mpls]"
    elif self.mode == "MPLS_LDP":
      prompt = f"[{self.sysname}-mpls-ldp]"
    elif self.mode == "SEGMENT_ROUTING":
      prompt = f"[{self.sysname}-segment-routing]"
    elif self.mode == "SRV6_LOCATOR":
      prompt = f"[{self.sysname}-locator-{self.active_srv6_locator}]"
    elif self.mode == "SRV6_POLICY":
      prompt = f"[{self.sysname}-srv6-policy-{self.active_srv6_policy}]"
    elif self.mode == "SRV6_POLICY_PATH":
      prompt = f"[{self.sysname}-srv6-policy-{self.active_srv6_policy}-candidate-{self.active_srv6_candidate}]"
    elif self.mode == "SRV6_SEGMENT_LIST":
      prompt = f"[{self.sysname}-srv6-segment-list-{self.active_srv6_segment_list}]"
    elif self.mode == "EXPLICIT_PATH":
      prompt = f"[{self.sysname}-explicit-path-{self.active_explicit_path}]"
    elif self.mode == "SEGMENT_LIST":
      prompt = f"[{self.sysname}-segment-list-{self.active_segment_list}]"
    elif self.mode == "SR_POLICY":
      prompt = f"[{self.sysname}-sr-te-policy-{self.active_sr_policy}]"
    elif self.mode == "SR_POLICY_PATH":
      prompt = f"[{self.sysname}-sr-te-policy-{self.active_sr_policy}-candidate-{self.active_sr_candidate}]"
    elif self.mode == "TUNNEL_POLICY":
      prompt = f"[{self.sysname}-tunnel-policy-{self.active_tunnel_policy}]"
    elif self.mode == "BGP_VPN_AF":
      prompt = f"[{self.sysname}-bgp-af-vpn-instance-{self.active_vpn_instance}]"
    elif self.mode == "BGP_EVPN_AF":
      prompt = f"[{self.sysname}-bgp-evpn]"
    elif self.mode == "BRIDGE_DOMAIN":
      prompt = f"[{self.sysname}-bd{self.active_bridge_domain}]"
    elif self.mode == "NVE":
      prompt = f"[{self.sysname}-Nve1]"
    elif self.mode == "EVPN_INSTANCE":
      prompt = f"[{self.sysname}-evpn-instance-{self.active_evpn_instance}]"
    elif self.mode == "ROUTE_POLICY":
      policy = self.running.get("route_policies", {}).get(self.active_route_policy, {})
      prompt = f"[{self.sysname}-route-policy-{self.active_route_policy}-{policy.get('action', 'permit')}-node-{policy.get('node', 10)}]"
    elif self.mode == "ACL":
      acl_type = self.running.get("acls", {}).get(self.active_acl, {}).get("type", "advance")
      prompt = f"[{self.sysname}-acl4-{acl_type}-{self.active_acl}]"
    elif self.mode == "CLASSIFIER":
      prompt = f"[{self.sysname}-classifier-{self.active_classifier}]"
    elif self.mode == "BEHAVIOR":
      prompt = f"[{self.sysname}-behavior-{self.active_behavior}]"
    elif self.mode == "POLICY":
      prompt = f"[{self.sysname}-trafficpolicy-{self.active_policy}]"
    elif self.mode == "AAA":
      prompt = f"[{self.sysname}-aaa]"
    elif self.mode == "AUTH_SCHEME":
      prompt = f"[{self.sysname}-aaa-authen-{self.active_auth_scheme}]"
    elif self.mode == "AUTHZ_SCHEME":
      prompt = f"[{self.sysname}-aaa-author-{self.active_authz_scheme}]"
    elif self.mode == "ACCOUNTING_SCHEME":
      prompt = f"[{self.sysname}-aaa-accounting-{self.active_accounting_scheme}]"
    elif self.mode == "AAA_DOMAIN":
      prompt = f"[{self.sysname}-aaa-domain-{self.active_aaa_domain}]"
    elif self.mode == "VTY":
      prompt = f"[{self.sysname}-ui-vty{self.active_vty}]"
    elif self.mode == "NAT_ADDRESS_GROUP":
      prompt = f"[{self.sysname}-address-group-{self.active_nat_group}]"
    elif self.mode == "NAT_POLICY":
      prompt = f"[{self.sysname}-policy-nat]"
    elif self.mode == "NAT_RULE":
      prompt = f"[{self.sysname}-policy-nat-rule-{self.active_nat_rule}]"
    elif self.mode == "WLAN":
      prompt = f"[{self.sysname}-wlan]"
    elif self.mode == "WLAN_PROFILE":
      prompt = f"[{self.sysname}-wlan-{self.active_wlan_profile_kind}-{self.active_wlan_profile}]"
    elif self.mode == "WLAN_AP":
      ap = (self.running.get("wlan", {}).get("aps", {}).get(self.active_wlan_ap, {}))
      prompt = f"[{self.sysname}-wlan-ap-{ap.get('ap_name') or self.active_wlan_ap}]"
    elif self.mode == "FTP":
      prompt = "[ftp]"
    else:
      suffix = "port-group" if self.interface == "port-group" else self.interface
      prompt = f"[{self.sysname}-{suffix}]"
    self._write(prompt)
    self.prompt_position = len(self.terminal.toPlainText())
    self.suppress_command_history = False

  def print_prompt(self):
    self._prompt()

  def _password_matches(self, password):
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(self.password_salt), 180000).hex()
    return hmac.compare_digest(actual, self.password_hash)

  def handle_terminal_key(self, event):
    if self.pending not in ("setup_password", "setup_confirm", "login_password", "file_name", "confirm_save", "confirm_file_save", "saved_file_name", "confirm_reset", "reboot_save", "reboot_confirm", "ospf_routerid_reset", "ftp_login_user", "ftp_login_password", "ftp_source_confirm", "aaa_service_confirm", "aaa_privilege_confirm", "ftp_delete_confirm", "more", "remote_user", "remote_password", "remote_key_trust", "remote_key_save", "remote_change_confirm", "remote_old_password", "remote_new_password", "remote_new_confirm", "telnet_source_confirm", "ssh_source_confirm", "dcn_confirm"):
      return False
    if event.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
      return False
    text = self.terminal.toPlainText()
    value = text[self.prompt_position:].strip()
    self._write("\n")
    self._pending_input(value)
    return True

  def _pending_input(self, value):
    action, self.pending = self.pending, None
    if action == "dcn_confirm":
      if value.lower() in ("y", "yes"):
        self.running["dcn_enabled"] = False
        self._persist()
        self._write("Info: DCN has been disabled.\n")
      else:
        self._write("Info: DCN configuration remains unchanged.\n")
      self._prompt()
    if action == "setup_password":
      if not 8 <= len(value) <= 16:
        self._write("Error: Password length must be 8-16 characters.\nEnter Password:")
        self.pending, self.prompt_position = action, len(self.terminal.toPlainText())
        return
      self.new_password = value
      self.pending = "setup_confirm"
      self._write("Confirm Password:")
      self.prompt_position = len(self.terminal.toPlainText())
    elif action == "setup_confirm":
      if value != self.new_password:
        self._write("Error: The passwords do not match.\nEnter Password:")
        self.pending, self.prompt_position = "setup_password", len(self.terminal.toPlainText())
        return
      self.password_salt = secrets.token_bytes(16).hex()
      self.password_hash = hashlib.pbkdf2_hmac("sha256", value.encode(), bytes.fromhex(self.password_salt), 180000).hex()
      self.mode = "USER"
      self._persist()
      self._write("Info: Save the password now. Please wait for a moment.\n\nThe maximum number of VTY users is 21, and the number of current VTY users on line is 1.\n\n")
      self._prompt()
    elif action == "login_password":
      if not self._password_matches(value):
        self._write("Error: Incorrect password.\nPassword:")
        self.pending, self.prompt_position = action, len(self.terminal.toPlainText())
      else:
        self.mode = "USER"
        self._prompt()
    elif action == "remote_key_trust":
      if value.lower() in ("y", "yes"):
        self.pending = "remote_key_save"
        self._write("Save the server's public key? [Y/N]:")
        self.prompt_position = len(self.terminal.toPlainText())
      else:
        self._write("Error: SSH host key was not trusted; connection closed.\n")
        self._prompt()
    elif action == "remote_key_save":
      if value.lower() in ("y", "yes"):
        self.running.setdefault("ssh_known_hosts", {})[self.remote_address] = self.remote_fingerprint
        self._persist()
        self._write("Info: The server's public key has been saved.\n")
      self.pending = "remote_user"
      self._write("Please input the username:")
      self.prompt_position = len(self.terminal.toPlainText())
    elif action == "remote_user":
      self.remote_login_name = value.strip()
      if not self.remote_login_name:
        self._write("Error: Username cannot be empty.\nPlease input the username:")
        self.pending, self.prompt_position = action, len(self.terminal.toPlainText())
      else:
        self.pending = "remote_password"
        self._write("Please input the password:")
        self.prompt_position = len(self.terminal.toPlainText())
    elif action == "remote_password":
      if not self._remote_credentials_valid(value):
        self._write("\nError: Authentication failed.\n")
        self._prompt()
      else:
        account = self.remote_account
        if account.get("password_force_change"):
          self.pending = "remote_change_confirm"
          self._write("\nThe password must be changed at the first login. Change now? (y/n)[n]:")
          self.prompt_position = len(self.terminal.toPlainText())
        else:
          self._enter_remote_session()
    elif action == "remote_change_confirm":
      if value.lower() in ("y", "yes"):
        self.pending = "remote_old_password"
        self._write("Old password:")
        self.prompt_position = len(self.terminal.toPlainText())
      else:
        self._write("Error: Password change is required; connection closed.\n")
        self._prompt()
    elif action == "remote_old_password":
      if not self._verify_remote_password(self.remote_account, value):
        self._write("Error: Incorrect old password.\n")
        self._prompt()
      else:
        self.pending = "remote_new_password"
        self._write("New password:")
        self.prompt_position = len(self.terminal.toPlainText())
    elif action == "remote_new_password":
      if len(value) < 8 or len(value) > 64:
        self._write("Error: Password length must be 8-64 characters.\nNew password:")
        self.pending, self.prompt_position = action, len(self.terminal.toPlainText())
      else:
        self.remote_new_password = value
        self.pending = "remote_new_confirm"
        self._write("Confirm new password:")
        self.prompt_position = len(self.terminal.toPlainText())
    elif action == "remote_new_confirm":
      if value != self.remote_new_password:
        self._write("Error: The passwords do not match.\nNew password:")
        self.pending, self.prompt_position = "remote_new_password", len(self.terminal.toPlainText())
      else:
        self._set_remote_password(self.remote_account, value)
        self._persist_remote_target()
        self._write("Info: Password changed successfully.\n")
        self._enter_remote_session()
    elif action in ("telnet_source_confirm", "ssh_source_confirm"):
      field = "telnet_server_source" if action == "telnet_source_confirm" else "ssh_server_source"
      if value.lower() in ("y", "yes"):
        self.running[field] = getattr(self, "pending_remote_source", "all-interface")
        self._persist()
        self._write("Info: Server source is set to all-interface.\n")
      else:
        self._write("Info: Server source configuration remains unchanged.\n")
      self._prompt()
    elif action == "ftp_login_user":
      if not value or len(value) > 64:
        self._write("530 Invalid user name.\n")
        self._prompt()
      else:
        self.ftp_login_name = value
        self.pending = "ftp_login_password"
        self._write(f"331 Password required for {value}.\nEnter password:")
        self.prompt_position = len(self.terminal.toPlainText())
    elif action == "ftp_login_password":
      message = self._complete_ftp_login(value)
      self._write(message + "\n")
      self._prompt()
    elif action == "ftp_source_confirm":
      if value.lower() in ("y", "yes"):
        self.running["ftp_server_source"] = self.pending_ftp_source
        self._persist()
        self._write("Warning: It expands the range of accessed IP.\n")
      else:
        self._write("Info: FTP server source remains unchanged.\n")
      self._prompt()
    elif action in ("aaa_service_confirm", "aaa_privilege_confirm"):
      if value.lower() in ("y", "yes"):
        name, setting = self.pending_aaa_change
        user = self.running.setdefault("aaa_users", {}).setdefault(name, {"service_types": [], "privilege": 0,
                                                                            "ftp_directory": "flash:/", "ftp_permissions": ["read", "write", "execute"]})
        if action == "aaa_service_confirm":
          user["service_types"] = list(setting) if isinstance(setting, (list, tuple)) else [setting]
        else:
          user["privilege"] = int(setting)
        self._persist()
      else:
        self._write("Info: The local-user setting remains unchanged.\n")
      self._prompt()
    elif action == "ftp_delete_confirm":
      self._ftp_delete_remote(self.pending_ftp_delete, value.lower() in ("y", "yes"))
      self._prompt()
    elif action == "file_name":
      if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}\.cfg", value, re.I):
        self._write("Error: Invalid file name. Use a name ending in .cfg.\n")
        self._prompt()
      else:
        self.pending, self.pending_file = "confirm_save", value
        self._write(f"Are you sure to save the configuration to flash:/{value}? (y/n)[n]:")
        self.prompt_position = len(self.terminal.toPlainText())
    elif action == "confirm_save":
      if value.lower() in ("y", "yes"):
        self._refresh_storage()
        self.saved = json.loads(json.dumps(self.running))
        self._storage_dirty = True
        self._write("Info: The configuration is saved successfully.\n")
      self._persist()
      self._prompt()
    elif action == "confirm_file_save":
      if value.lower() in ("y", "yes"):
        self._refresh_storage()
        self.files[self.pending_file] = json.loads(json.dumps(self.running))
        self._storage_dirty = True
        self._write(f"Info: Save the configuration successfully to flash:/{self.pending_file}.\n")
      self._persist()
      self._prompt()
    elif action == "saved_file_name":
      name = value.strip()
      self._refresh_storage()
      if name not in self.files:
        self._write("Error: The specified configuration file does not exist.\n")
      else:
        self.startup_file = name
        self._storage_dirty = True
        self._write("Info: Succeeded in setting the startup saved-configuration file.\n")
      self._persist()
      self._prompt()
    elif action == "confirm_reset":
      if value.lower() in ("y", "yes"):
        self._refresh_storage()
        self.saved = None
        self.startup_file = ""
        self._storage_dirty = True
        self._write("Info: Succeeded in clearing the saved-configuration file.\n")
      self._persist()
      self._prompt()
    elif action == "reboot_save":
      self.reboot_save_choice = value.lower() in ("y", "yes")
      self.pending = "reboot_confirm"
      self._write("System will reboot, continue? (y/n)[n]:")
      self.prompt_position = len(self.terminal.toPlainText())
    elif action == "reboot_confirm":
      if value.lower() in ("y", "yes"):
        self._refresh_storage()
        if getattr(self, "reboot_save_choice", False):
          self.saved = json.loads(json.dumps(self.running))
          self._storage_dirty = True
        if self.startup_file in self.files:
          self.running = json.loads(json.dumps(self.files[self.startup_file]))
        elif self.saved is not None:
          self.running = json.loads(json.dumps(self.saved))
        self.sysname = self.running.get("sysname", "HUAWEI")
        self.boot_count += 1
        self._persist()
        self._write("\nInfo: The system is restarting...\nPlease Press ENTER.")
        self.pending = "reboot_enter"
        self.prompt_position = len(self.terminal.toPlainText())
      else:
        self._prompt()
    elif action == "reboot_enter":
      self._start_session()
    elif action == "ospf_routerid_reset":
      process_id, router_id = self.pending_ospf_router_id
      if value.lower() in ("y", "yes"):
        self.running["ospf_processes"][process_id]["router_id"] = router_id
        self._persist()
        self._write("Info: The OSPF process is reset and the router ID is updated.\n")
      else:
        self._write("Info: The router ID remains unchanged.\n")
      self.active_ospf_process, self.mode = process_id, "OSPF"
      self._prompt()

  def _handle_wlan_command(self, cmd):
    """Small but stateful Huawei WLAN CLI used by the WAC/Fit AP lab."""
    low = cmd.lower().strip()
    wlan = self.running.setdefault("wlan", {"capwap_source_interface": "", "ap_auth_mode": "mac-auth",
        "aps": {}, "ap_groups": {}, "regulatory_profiles": {}, "security_profiles": {},
        "ssid_profiles": {}, "vap_profiles": {}})
    if low.startswith("display ap all"):
      from modules.datacom_wlan import access_point_status
      conn = get_connection()
      try:
        nodes = [item for item in self.window().scene.items() if getattr(item, "device_type", "").startswith("Huawei Fit AP")]
        lines = ["Info: This operation may take a few seconds. Please wait...", "AP ID  MAC address       Name     Group      IP address      State"]
        seen = set()
        for node in nodes:
          result = access_point_status(conn, self.username, node, self.node_name)
          if result.get("ap_id", "-") == "-":
            continue
          seen.add(result["mac"])
          lines.append(f"{result['ap_id']:<6} {result['mac']:<17} {node.display_name:<8} {result.get('group','-'):<10} {result.get('ip') or '-':<15} {result['state']}")
        for ap_id, entry in wlan.get("aps", {}).items():
          if entry.get("mac_address") not in seen:
            lines.append(f"{ap_id:<6} {entry.get('mac_address','-'):<17} {entry.get('ap_name','-'):<8} {entry.get('ap_group','default'):<10} -               fault")
        lines.append(f"Total: {len(lines)-2}")
      finally:
        conn.close()
      self.append_plain_text("\n".join(lines)); self._prompt(); return True
    if low.startswith("display vap all"):
      lines = ["AP ID  AP name  Profile name  SSID         VLAN  Forward mode"]
      for ap_id, ap in wlan.get("aps", {}).items():
        group = wlan.get("ap_groups", {}).get(ap.get("ap_group", "default"), {})
        for binding in group.get("vap_bindings", []):
          vap_name = binding.get("vap_profile", "-")
          vap = wlan.get("vap_profiles", {}).get(vap_name, {})
          ssid_profile = wlan.get("ssid_profiles", {}).get(vap.get("ssid_profile"), {})
          lines.append(f"{ap_id:<6} {ap.get('ap_name','-'):<8} {vap_name:<13} {ssid_profile.get('ssid','-'):<12} {vap.get('service_vlan','-'):<5} {vap.get('forward_mode','direct-forward')}")
      if len(lines) == 1: lines.append("No VAP is bound to an AP group.")
      self.append_plain_text("\n".join(lines)); self._prompt(); return True
    if low.startswith("display station all"):
      from modules.datacom_wlan import station_rows
      conn = get_connection()
      try: rows = station_rows(conn, self.username, self.node_name)
      finally: conn.close()
      lines = ["STA      MAC address       IP address      AP       SSID"]
      lines.extend(f"{row[0]:<8} {row[1]:<17} {row[2]:<15} {row[3]:<8} {row[4]}" for row in rows)
      if not rows: lines.append("No station is connected.")
      self.append_plain_text("\n".join(lines)); self._prompt(); return True

    if self.mode == "SYSTEM" and low == "wlan":
      self.mode = "WLAN"; self._prompt(); return True
    if self.mode not in ("WLAN", "WLAN_PROFILE", "WLAN_AP"):
      return False
    if low in ("quit", "exit", "return"):
      if self.mode == "WLAN_PROFILE": self.mode = "WLAN"
      elif self.mode == "WLAN_AP": self.mode = "WLAN"
      else: self.mode = "SYSTEM" if low == "quit" else "USER"
      self._persist(); self._prompt(); return True
    if self.mode == "WLAN":
      match = re.fullmatch(r"(ap-group|security-profile|ssid-profile|vap-profile|regulatory-domain-profile)\s+name\s+(\S+)", cmd, re.I)
      if match:
        kind, name = match.group(1).lower(), match.group(2)
        keys = {"ap-group":"ap_groups", "security-profile":"security_profiles", "ssid-profile":"ssid_profiles", "vap-profile":"vap_profiles", "regulatory-domain-profile":"regulatory_profiles"}
        key = keys[kind]; data = wlan.setdefault(key, {})
        defaults = {"ap-group":{"vap_bindings":[], "regulatory_profile":"default"}, "security-profile":{"auth":"wpa-wpa2", "cipher":"aes", "passphrase":""}, "ssid-profile":{"ssid":""}, "vap-profile":{"forward_mode":"direct-forward", "service_vlan":None, "security_profile":"", "ssid_profile":""}, "regulatory-domain-profile":{"country_code":"cn"}}
        data.setdefault(name, defaults[kind].copy())
        self.active_wlan_profile, self.active_wlan_profile_kind = name, kind
        self.mode = "WLAN_PROFILE"; self._persist(); self._prompt(); return True
      if low == "ap auth-mode mac-auth":
        wlan["ap_auth_mode"] = "mac-auth"; self._persist(); self._prompt(); return True
      match = re.fullmatch(r"capwap source interface\s+(.+)", cmd, re.I)
      if match:
        source = re.sub(r"\s+", "", match.group(1))
        if source.lower() not in {name.lower() for name in self.running.get("interfaces", {})}:
          self.append_plain_text("Error: The CAPWAP source interface does not exist.")
        else:
          wlan["capwap_source_interface"] = source; self._persist()
        self._prompt(); return True
      match = re.fullmatch(r"ap-id\s+(\d+)\s+ap-mac\s+([0-9a-f:-]{12,17})", cmd, re.I)
      if match:
        ap_id, mac = match.groups()
        compact = re.sub(r"[^0-9a-f]", "", mac.lower())
        if len(compact) != 12: self.append_plain_text("Error: Enter a valid AP MAC address.")
        else:
          mac = f"{compact[:4]}-{compact[4:8]}-{compact[8:]}"
          wlan.setdefault("aps", {}).setdefault(ap_id, {"mac_address":mac,"ap_name":"","ap_group":"default"})
          self.active_wlan_ap = ap_id; self.mode = "WLAN_AP"; self._persist()
        self._prompt(); return True
      self.append_plain_text("Error: Unrecognized WLAN command."); self._prompt(); return True
    if self.mode == "WLAN_AP":
      ap = wlan.get("aps", {}).get(str(self.active_wlan_ap), {})
      match = re.fullmatch(r"ap-name\s+(.+)", cmd, re.I)
      if match: ap["ap_name"] = match.group(1).strip()
      elif low.startswith("ap-group "):
        group = cmd.split(None,1)[1].strip()
        if group not in wlan.get("ap_groups", {}): self.append_plain_text("Error: Create the AP group first.")
        else: ap["ap_group"] = group
      elif low == "provision-ap": ap["provisioned"] = True
      elif low.startswith("ap-id "):
        self.append_plain_text("Error: AP ID is configured from WLAN view.")
      else: self.append_plain_text("Error: Unrecognized AP command.")
      self._persist(); self._prompt(); return True

    kind = self.active_wlan_profile_kind
    profile = wlan.get({"ap-group":"ap_groups", "security-profile":"security_profiles", "ssid-profile":"ssid_profiles", "vap-profile":"vap_profiles", "regulatory-domain-profile":"regulatory_profiles"}[kind], {}).get(self.active_wlan_profile, {})
    if kind == "security-profile" and low.startswith("security "):
      match = re.fullmatch(r"security\s+(wpa|wpa2|wpa-wpa2)\s+psk\s+pass-phrase\s+(\S+)\s+(aes|tkip)", cmd, re.I)
      if match and 8 <= len(match.group(2)) <= 63: profile.update({"auth":match.group(1).lower(),"passphrase":match.group(2),"cipher":match.group(3).lower()})
      else: self.append_plain_text("Error: Use security wpa-wpa2 psk pass-phrase <password> aes.")
    elif kind == "ssid-profile" and low.startswith("ssid "):
      profile["ssid"] = cmd.split(None,1)[1].strip()
    elif kind == "regulatory-domain-profile" and low.startswith("country-code "):
      profile["country_code"] = cmd.split(None,1)[1].strip().lower()
    elif kind == "vap-profile":
      if low.startswith("forward-mode "):
        value = cmd.split(None,1)[1].lower()
        if value in ("direct-forward", "tunnel"): profile["forward_mode"] = value
        else: self.append_plain_text("Error: Forward mode must be direct-forward or tunnel.")
      elif low.startswith("service-vlan vlan-id "):
        try: profile["service_vlan"] = int(cmd.split()[-1])
        except ValueError: self.append_plain_text("Error: Invalid service VLAN.")
      elif low.startswith("security-profile "):
        name = cmd.split(None,1)[1].strip()
        if name not in wlan.get("security_profiles", {}): self.append_plain_text("Error: Create the security profile first.")
        else: profile["security_profile"] = name
      elif low.startswith("ssid-profile "):
        name = cmd.split(None,1)[1].strip()
        if name not in wlan.get("ssid_profiles", {}): self.append_plain_text("Error: Create the SSID profile first.")
        else: profile["ssid_profile"] = name
      else: self.append_plain_text("Error: Unrecognized VAP profile command.")
    elif kind == "ap-group":
      match = re.fullmatch(r"regulatory-domain-profile\s+(\S+)", cmd, re.I)
      vap_match = re.fullmatch(r"vap-profile\s+(\S+)\s+wlan\s+(\d+)\s+radio\s+(all|\d+)", cmd, re.I)
      if match:
        name = match.group(1)
        if name not in wlan.get("regulatory_profiles", {}): self.append_plain_text("Error: Create the regulatory-domain profile first.")
        else: profile["regulatory_profile"] = name
      elif vap_match:
        vap = vap_match.group(1)
        if vap not in wlan.get("vap_profiles", {}): self.append_plain_text("Error: Create the VAP profile first.")
        else:
          binding = {"vap_profile":vap,"wlan_id":int(vap_match.group(2)),"radio":vap_match.group(3)}
          if binding not in profile.setdefault("vap_bindings", []): profile["vap_bindings"].append(binding)
      else: self.append_plain_text("Error: Unrecognized AP group command.")
    else:
      self.append_plain_text("Error: Unrecognized WLAN profile command.")
    self._persist(); self._prompt(); return True

  def _handle_advanced_routing_command(self, command):
    """Configure the HCIE IPv4 routing features modeled by this simulator."""
    cmd = command.strip()
    low = cmd.lower()
    parts = cmd.split()

    if low in ("quit", "return", "exit") and self.mode in (
        "OSPF_FRR", "OSPFV3", "ISIS", "BGP_AF", "BGP_IPV6_AF", "BGP_VPN_AF", "BGP_EVPN_AF", "BGP", "ROUTE_POLICY", "VPN_INSTANCE", "MPLS", "MPLS_LDP"):
      if self.mode == "OSPF_FRR":
        self.mode = "OSPF"
      elif self.mode in ("BGP_AF", "BGP_IPV6_AF", "BGP_VPN_AF", "BGP_EVPN_AF"):
        self.mode = "BGP"
      elif self.mode in ("BGP", "ISIS", "OSPFV3", "ROUTE_POLICY", "VPN_INSTANCE", "MPLS", "MPLS_LDP"):
        self.mode = "SYSTEM"
        self.active_isis_process = self.active_bgp_as = self.active_route_policy = None
        self.active_vpn_instance = None
      self._persist(); self._prompt(); return True

    def error(message):
      self.append_plain_text(message); self._prompt(); return True

    if low.startswith("display bgp peer"):
      cfg = self.running.get("bgp") or {}
      peers = cfg.get("peers", {})
      orf_match = re.fullmatch(r"display bgp peer\s+(\S+)\s+orf\s+ip-prefix", cmd, re.I)
      if orf_match:
        peer = orf_match.group(1)
        info = peers.get(peer, {})
        prefix_name = info.get("import_prefix")
        entries = self.running.get("ip_prefix_lists", {}).get(prefix_name, []) if prefix_name else []
        lines = [f"Peer: {peer}", "ORF type: IP-prefix", f"Import policy: {prefix_name or '-'}", "Index  Permit/Deny  Prefix             GE  LE"]
        lines.extend(f"{entry['index']:<6} {entry['action']:<12} {entry['network']:<18} {entry.get('ge', 0):<3} {entry.get('le', 32)}" for entry in entries)
        self.append_plain_text("\n".join(lines) if info else f"Error: BGP peer {peer} does not exist.")
        self._prompt(); return True
      self.append_plain_text(
          f"BGP local router ID: {cfg.get('router_id') or self._router_id_for_display()}\n"
          f"Local AS number: {cfg.get('as_number', '-') }\nTotal number of peers: {len(peers)}\n"
          f"Peer                 AS        State        Type")
      for peer, info in sorted(peers.items()):
        self.append_plain_text(f"{peer:<20} {info.get('as_number', '-'):<9} "
                               f"{'Established' if self._bgp_peer_established(peer, info) else 'Idle':<12} "
                               f"{'iBGP' if str(info.get('as_number')) == str(cfg.get('as_number')) else 'eBGP'}")
      self._prompt(); return True
    if low.startswith("display ospf routing"):
      self._display_ospf_routing(cmd); return True
    if low == "display bgp routing-table" or low.startswith("display bgp routing-table "):
      self._display_bgp_routes(cmd); return True
    if low == "display isis peer" or low.startswith("display isis peer "):
      from modules.datacom_isis import isis_state
      from modules.datacom_ipv6_routing import ipv6_routing_state
      conn = get_connection()
      try:
        peers = isis_state(conn, self.username).get("neighbors", {}).get(self.node_name, [])
        ipv6_peers = ipv6_routing_state(conn, self.username)["isis_peers"].get(self.node_name, [])
      finally: conn.close()
      rows = [f"{p['system_id']:<18} {p['interface']:<15} Up      {p['level']}" for p in peers]
      rows.extend(f"{p['peer_id']:<18} {p['interface']:<15} Up      L2" for p in ipv6_peers)
      self.append_plain_text("Peer information for IS-IS\nSystem ID          Interface       State   Type\n" + ("\n".join(rows) or "No IS-IS peers."))
      self._prompt(); return True
    if low.startswith("display isis route"):
      if low.startswith("display isis route ipv6") or any(iface.get("isis_ipv6_process") is not None for iface in self.running.get("interfaces", {}).values()):
        from modules.datacom_ipv6_routing import ipv6_routing_state
        conn = get_connection()
        try: routes = ipv6_routing_state(conn, self.username)["routes"].get(self.node_name, [])
        finally: conn.close()
        routes = [row for row in routes if row["protocol"] == "ISIS" or
                  (row["protocol"] == "Direct" and (self.running.get("interfaces", {}).get(row["interface"], {}).get("isis_ipv6_process") is not None))]
        target = parts[4] if len(parts) > 4 and parts[3].lower() == "ipv6" else None
        if target:
          try:
            wanted = ipaddress.IPv6Network(target, strict=False)
            routes = [row for row in routes if row["destination"] == wanted]
          except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
            routes = []
        self.append_plain_text("IPv6 IS-IS routing table\nIPv6 Dest.                    ExitInterface    NextHop                  Cost\n" +
            ("\n".join(f"{str(row['destination']):<29} {row['interface']:<16} {row.get('next_hop','-'):<25} {row['cost']}" for row in routes) or "No IPv6 IS-IS routes."))
        self._prompt(); return True
      self._display_isis_routes(cmd); return True
    if low == "display ospfv3 peer":
      from modules.datacom_ipv6_routing import ipv6_routing_state
      conn = get_connection()
      try: peers = ipv6_routing_state(conn, self.username)["ospfv3_peers"].get(self.node_name, [])
      finally: conn.close()
      self.append_plain_text("OSPFv3  Process  (1)\nNeighbor ID       PriState    Interface        Instance ID\n" +
          ("\n".join(f"{row['router_id']:<17} Full/DR      {row['interface']:<16} {row['instance']}" for row in peers) or "No OSPFv3 peers."))
      self._prompt(); return True
    if low == "display isis ipv6 peer":
      from modules.datacom_ipv6_routing import ipv6_routing_state
      conn = get_connection()
      try: peers = ipv6_routing_state(conn, self.username)["isis_peers"].get(self.node_name, [])
      finally: conn.close()
      self.append_plain_text("IPv6 IS-IS peer information\nSystem ID          Interface       State   Type\n" +
          ("\n".join(f"{row['peer_id']:<18} {row['interface']:<15} Up      L2" for row in peers) or "No IPv6 IS-IS peers."))
      self._prompt(); return True
    if low == "display bgp ipv6 peer":
      from modules.datacom_ipv6_routing import ipv6_routing_state
      conn = get_connection()
      try: peers = ipv6_routing_state(conn, self.username)["bgp6_peers"].get(self.node_name, [])
      finally: conn.close()
      self.append_plain_text(f"BGP local router ID: {(self.running.get('bgp') or {}).get('router_id') or self._router_id_for_display()}\nIPv6 address family peers\nPeer                 AS        State\n" +
          ("\n".join(f"{row['address']:<20} {row['as_number']:<9} Established" for row in peers) or "No established IPv6 BGP peers."))
      self._prompt(); return True
    if low == "display bgp ipv6 routing-table":
      from modules.datacom_ipv6_routing import ipv6_routing_state
      conn = get_connection()
      try: routes = ipv6_routing_state(conn, self.username)["bgp6_routes"].get(self.node_name, [])
      finally: conn.close()
      self.append_plain_text("BGP IPv6 routing table\nNetwork                    NextHop                  Source\n" +
          ("\n".join(f"{str(row['destination']):<27} {row.get('next_hop','-'):<24} {row.get('source','-')}" for row in routes) or "No IPv6 BGP routes."))
      self._prompt(); return True
    if low == "display ospfv3 routing" or low.startswith("display ospfv3 routing "):
      from modules.datacom_ipv6_routing import ipv6_routing_state
      conn = get_connection()
      try: routes = ipv6_routing_state(conn, self.username)["routes"].get(self.node_name, [])
      finally: conn.close()
      routes = [row for row in routes if row["protocol"] == "OSPFv3" or
                (row["protocol"] == "Direct" and self.running.get("interfaces", {}).get(row["interface"], {}).get("ospfv3_process") is not None)]
      self.append_plain_text("OSPFv3 Process (1)\nDestination                    Next-hop                Interface        Metric\n" +
          ("\n".join(f"{str(row['destination']):<30} {row.get('next_hop','direct'):<23} {row['interface']:<16} {row['cost']}" for row in routes) or "No OSPFv3 routes."))
      self._prompt(); return True
    if low.startswith("display ospf bfd session"):
      self._display_ospf_bfd(cmd); return True
    if low in ("display bgp evpn peer", "display bgp evpn all peer"):
      cfg = self.running.get("bgp") or {}
      peers = [(peer, info) for peer, info in sorted(cfg.get("peers", {}).items()) if info.get("evpn_enabled")]
      lines = ["BGP EVPN peer information", "Peer                 AS        State"]
      lines.extend(f"{peer:<20} {info.get('as_number','-'):<9} {'Established' if self._bgp_peer_established(peer, info) else 'Idle'}" for peer, info in peers)
      self.append_plain_text("\n".join(lines) if peers else "No EVPN peers."); self._prompt(); return True
    vpn_peer_display = re.fullmatch(r"display\s+bgp\s+vpnv4\s+vpn-instance\s+(\S+)\s+peer", cmd, re.I)
    if vpn_peer_display:
      vpn_name = vpn_peer_display.group(1)
      vpn = self.running.get("vpn_instances", {}).get(vpn_name)
      if not vpn:
        self.append_plain_text(f"Error: VPN instance {vpn_name} does not exist.")
      else:
        peers = vpn.get("bgp_peers", {})
        self.append_plain_text(f"VPN-Instance {vpn_name} BGP peer information\nPeer                 AS        State\n" +
          ("\n".join(f"{peer:<20} {info.get('as_number','-'):<9} {'Established' if info.get('as_number') and info.get('connect_interface') else 'Idle'}"
                      for peer, info in sorted(peers.items())) or "No VPN BGP peers."))
      self._prompt(); return True
    if low == "display bgp evpn all routing-table":
      from modules.datacom_mpls import mpls_state
      conn = get_connection()
      try: routes = mpls_state(conn, self.username).get("evpn_routes", {}).get(self.node_name, [])
      finally: conn.close()
      lines = ["EVPN address family routing table", "Route Type  RD         Network             NextHop"]
      lines.extend(f"Type-5      {row.get('rd','-'):<10} {str(row['network']):<19} {row.get('next_hop','-')}" for row in routes)
      self.append_plain_text("\n".join(lines) if routes else "No EVPN Type-5 routes."); self._prompt(); return True
    if low == "display mpls ldp adjacency all" or low.startswith("display mpls ldp adjacency"):
      from modules.datacom_mpls import mpls_state
      conn = get_connection()
      try: peers = mpls_state(conn, self.username)["ldp_neighbors"].get(self.node_name, [])
      finally: conn.close()
      lines = ["LDP Adjacency Information", "Peer LSR ID       Interface   State"]
      lines.extend(f"{row['peer_id']:<18} {row['interface']:<11} {row['state']}" for row in peers)
      self.append_plain_text("\n".join(lines) if peers else "No LDP adjacency."); self._prompt(); return True
    vpn_display = re.fullmatch(r"display\s+bgp\s+vpnv4\s+vpn-instance\s+(\S+)\s+routing-table(?:\s+(\S+))?", cmd, re.I)
    if vpn_display:
      from modules.datacom_mpls import mpls_state
      conn = get_connection()
      try: routes = mpls_state(conn, self.username)["vpn_routes"].get((self.node_name, vpn_display.group(1)), [])
      finally: conn.close()
      target = vpn_display.group(2)
      if target:
        try:
          address = ipaddress.IPv4Address(target); routes = [row for row in routes if address in row["network"]]
        except ipaddress.AddressValueError: routes = []
      lines = [f"VPN-Instance {vpn_display.group(1)} BGP routing table", "Network             NextHop          RD        Source"]
      lines.extend(f"{str(row['network']):<20} {row.get('next_hop','-'):<16} {row.get('rd','-'):<9} {row.get('source','-')}" for row in routes)
      self.append_plain_text("\n".join(lines)); self._prompt(); return True
    if low == "display bgp vpnv4 all peer":
      cfg = self.running.get("bgp") or {}
      self.append_plain_text("VPNv4 peer information\nPeer                 AS        State\n" +
          ("\n".join(f"{peer:<20} {info.get('as_number','-'):<9} {'Established' if self._bgp_peer_established(peer, info) else 'Idle'}"
                      for peer, info in sorted(cfg.get("peers", {}).items()) if info.get("vpnv4_enabled")) or "No VPNv4 peers."))
      self._prompt(); return True

    if low in ("system-view", "system-view immediately", "sys"):
      return False
    if self.mode == "SYSTEM":
      if low == "bfd":
        self.running["bfd_enabled"] = True
        self.mode = "BFD"
        self._persist(); self._prompt(); return True
      if low == "undo dcn":
        self.pending = "dcn_confirm"
        self._write("Warning: This operation will disable the DCN function. Continue? (y/n)[n]:")
        self.prompt_position = len(self.terminal.toPlainText()); return True
      if low == "undo pnp enable":
        self.running["pnp_enabled"] = False
        self._persist(); self._prompt(); return True
      if low == "undo crypto weak-algorithm disable":
        self.running["crypto_weak_algorithm_disabled"] = True
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"mpls\s+lsr-id\s+(\S+)", cmd, re.I)
      if match:
        try: self.running["mpls_lsr_id"] = str(ipaddress.IPv4Address(match.group(1)))
        except ipaddress.AddressValueError: return error("Error: Enter a valid MPLS LSR ID.")
        self._persist(); self._prompt(); return True
      if low == "mpls":
        self.running["mpls_enabled"] = True
        self.mode = "MPLS"; self._persist(); self._prompt(); return True
      if low == "mpls ldp":
        if not self.running.get("mpls_enabled"): return error("Error: Enable MPLS first.")
        self.running["mpls_ldp_enabled"] = True
        self.mode = "MPLS_LDP"; self._persist(); self._prompt(); return True
      match = re.fullmatch(r"ip\s+vpn-instance\s+(\S+)", cmd, re.I)
      if match:
        name = match.group(1)
        self.running.setdefault("vpn_instances", {}).setdefault(name, {"rd": "", "import_rts": [], "export_rts": [], "apply_label_per_instance": False, "interfaces": [], "import_ospf": [], "bgp_peers": {}, "allow_as_loop": 0, "substitute_as": False, "evpn_rts": False})
        self.active_vpn_instance, self.mode = name, "VPN_INSTANCE"
        self._persist(); self._prompt(); return True
      if low == "undo mpls":
        self.running["mpls_enabled"] = self.running["mpls_ldp_enabled"] = False
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"isis\s+(\d+)", cmd, re.I)
      if match:
        process_id = match.group(1)
        self.running.setdefault("isis_processes", {}).setdefault(process_id,
            {"level": "level-1-2", "networks": [], "default_route_advertise": False})
        self.active_isis_process, self.mode = process_id, "ISIS"
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"ospfv3\s+(\d+)", cmd, re.I)
      if match:
        process_id = match.group(1)
        self.running.setdefault("ospfv3_processes", {}).setdefault(process_id,
            {"router_id": "", "imports": [], "areas": {}})
        self.active_ospfv3_process, self.mode = process_id, "OSPFV3"
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"bgp\s+(\d+)", cmd, re.I)
      if match:
        asn = int(match.group(1))
        if not 1 <= asn <= 4294967295: return error("Error: BGP AS number must be from 1 to 4294967295.")
        if self.running.get("bgp") and int(self.running["bgp"].get("as_number", 0)) != asn:
          return error("Error: Remove the existing BGP process before changing its AS number.")
        self.running["bgp"] = self.running.get("bgp") or {
            "as_number": asn, "router_id": "", "peers": {}, "groups": {},
            "networks": [], "address_family": {"ipv4": True}}
        self.active_bgp_as, self.mode = asn, "BGP"
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"ip\s+ip-prefix\s+(\S+)\s+index\s+(\d+)\s+(permit|deny)\s+(\S+)\s+(\d+)(?:\s+greater-equal\s+(\d+))?(?:\s+less-equal\s+(\d+))?", cmd, re.I)
      if match:
        name, seq, action, prefix, length, ge, le = match.groups()
        try:
          network = ipaddress.IPv4Network(f"{prefix}/{int(length)}", strict=False)
          minimum, maximum = int(ge or length), int(le or (ge or length))
          if not int(length) <= minimum <= maximum <= 32: raise ValueError
        except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
          return error("Error: Invalid IP prefix or prefix-length range.")
        entries = self.running.setdefault("ip_prefix_lists", {}).setdefault(name, [])
        entries[:] = [entry for entry in entries if int(entry["index"]) != int(seq)]
        entries.append({"index": int(seq), "action": action.lower(), "network": str(network), "ge": minimum, "le": maximum})
        entries.sort(key=lambda entry: entry["index"])
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"ip\s+community-filter\s+basic\s+(\S+)\s+index\s+(\d+)\s+(permit|deny)\s+(\S+)", cmd, re.I)
      if match:
        name, seq, action, community = match.groups()
        values = self.running.setdefault("community_filters", {}).setdefault(name, [])
        values[:] = [entry for entry in values if int(entry["index"]) != int(seq)]
        values.append({"index": int(seq), "action": action.lower(), "value": community})
        values.sort(key=lambda entry: entry["index"])
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"ip\s+as-path-filter\s+(\S+)\s+(?:index\s+\d+\s+)?(permit|deny)\s+(.+)", cmd, re.I)
      if match:
        name, action, expression = match.groups()
        self.running.setdefault("as_path_filters", {}).setdefault(name, []).append({"action": action.lower(), "expression": expression.strip()})
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"route-policy\s+(\S+)\s+(permit|deny)\s+node\s+(\d+)", cmd, re.I)
      if match:
        name, action, node = match.groups()
        policy = self.running.setdefault("route_policies", {}).setdefault(name, {})
        policy[str(node)] = {"action": action.lower(), "ip_prefix": None, "community_filter": None, "as_path_filter": None, "community": None}
        self.active_route_policy, self.mode = name, "ROUTE_POLICY"
        self._active_route_policy_node = str(node)
        self._persist(); self._prompt(); return True
      if low.startswith("undo ") and any(low.startswith(prefix) for prefix in ("undo ip ip-prefix ", "undo ip community-filter ", "undo ip as-path-filter ")):
        bits = cmd.split()
        section = "ip_prefix_lists" if bits[1:3] == ["ip", "ip-prefix"] else "community_filters" if bits[1:3] == ["ip", "community-filter"] else "as_path_filters"
        self.running.setdefault(section, {}).pop(bits[3], None)
        self._persist(); self._prompt(); return True

    if self.mode == "OSPF":
      if low == "frr":
        process = self.running["ospf_processes"][self.active_ospf_process]
        process.setdefault("frr", {})
        self.mode = "OSPF_FRR"; self._persist(); self._prompt(); return True
      if low == "bfd all-interfaces enable":
        self.running["ospf_processes"][self.active_ospf_process]["bfd_all_interfaces"] = True
        self._persist(); self._prompt(); return True
      if low == "undo bfd all-interfaces enable":
        self.running["ospf_processes"][self.active_ospf_process]["bfd_all_interfaces"] = False
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"maximum load-balancing\s+(\d+)", cmd, re.I)
      if match:
        count = int(match.group(1))
        if not 1 <= count <= 32: return error("Error: Maximum load-balancing must be from 1 to 32.")
        self.running["ospf_processes"][self.active_ospf_process]["maximum_load_balancing"] = count
        self._persist(); self._prompt(); return True
    if self.mode == "BFD":
      if low in ("quit", "return", "exit"):
        self.mode = "SYSTEM"; self._persist(); self._prompt(); return True
      if low in ("undo bfd", "undo bfd enable"):
        self.running["bfd_enabled"] = False
        self.mode = "SYSTEM"; self._persist(); self._prompt(); return True
      return False
    if self.mode == "MPLS":
      if low == "mpls ldp":
        self.running["mpls_ldp_enabled"] = True
        self.mode = "MPLS_LDP"; self._persist(); self._prompt(); return True
      if low == "mpls te":
        self.running["mpls_te_enabled"] = True
        self._persist(); self._prompt(); return True
      return False
    if self.mode == "MPLS_LDP":
      if low in ("mpls ldp", "undo mpls ldp"):
        self.running["mpls_ldp_enabled"] = low == "mpls ldp"
        self._persist(); self._prompt(); return True
      return False
    if self.mode == "OSPF_FRR":
      if low in ("loop-free-alternate", "undo loop-free-alternate"):
        process = self.running["ospf_processes"][self.active_ospf_process]
        process.setdefault("frr", {})["loop_free_alternate"] = low == "loop-free-alternate"
        self._persist(); self._prompt(); return True
    if self.mode == "OSPF_AREA":
      match = re.fullmatch(r"filter\s+ip-prefix\s+(\S+)\s+export", cmd, re.I)
      if match:
        self.running["ospf_processes"][self.active_ospf_process]["areas"][self.active_ospf_area]["export_ip_prefix"] = match.group(1)
        self._persist(); self._prompt(); return True
    if self.mode == "ISIS":
      process = self.running["isis_processes"][self.active_isis_process]
      match = re.fullmatch(r"is-level\s+(level-1|level-1-2|level-2)", cmd, re.I)
      if match: process["level"] = match.group(1).lower()
      elif low == "cost-style wide": process["cost_style"] = "wide"
      elif low == "cost-style narrow": process["cost_style"] = "narrow"
      elif low == "segment-routing mpls": process["segment_routing_mpls"] = True
      elif low == "traffic-eng level-2": process["traffic_eng_level"] = "level-2"
      elif low.startswith("segment-routing global-block "):
        match = re.fullmatch(r"segment-routing\s+global-block\s+(\d+)\s+(\d+)", cmd, re.I)
        if not match: return error("Error: Use segment-routing global-block <start-label> <end-label>.")
        low_label, high_label = map(int, match.groups())
        if not 16 <= low_label < high_label <= 1048575: return error("Error: SRGB range must be valid labels from 16 to 1048575.")
        process["srgb"] = [low_label, high_label]
      elif low.startswith("is-name "): process["is_name"] = cmd.split(None, 1)[1].strip()
      elif low.startswith("network-entity "):
        net = parts[-1].upper()
        if not re.fullmatch(r"[0-9A-F]{2}(?:\.[0-9A-F]{4}){4}\.[0-9A-F]{2}\.[0-9A-F]{2}", net):
          return error("Error: Use a valid IS-IS NET, for example 49.0001.0000.0000.0003.00.")
        process["networks"] = [value for value in process.get("networks", []) if value[:2] != net[:7]] + [net]
      elif low == "default-route-advertise": process["default_route_advertise"] = True
      elif low == "undo default-route-advertise": process["default_route_advertise"] = False
      elif low == "ipv6 enable topology ipv6": process["ipv6_enabled"] = True
      elif low == "undo ipv6 enable topology ipv6": process["ipv6_enabled"] = False
      elif low.startswith("ipv6 import-route "):
        match = re.fullmatch(r"ipv6\s+import-route\s+(ospfv3|bgp|isis)(?:\s+(\d+))?", cmd, re.I)
        if not match: return error("Error: Use ipv6 import-route {ospfv3|isis} <process-id> or ipv6 import-route bgp.")
        protocol, process_id = match.groups()
        if protocol.lower() != "bgp" and not process_id:
          return error("Error: Specify a process ID for the selected routing protocol.")
        entry = {"protocol": protocol.lower(), "process": process_id or ""}
        imports = process.setdefault("ipv6_imports", [])
        if entry not in imports: imports.append(entry)
      else: return False
      self._persist(); self._prompt(); return True

    if self.mode == "OSPFV3":
      process = self.running["ospfv3_processes"][self.active_ospfv3_process]
      match = re.fullmatch(r"router-id\s+(\S+)", cmd, re.I)
      if match:
        try: process["router_id"] = str(ipaddress.IPv4Address(match.group(1)))
        except ipaddress.AddressValueError: return error("Error: Enter a valid OSPFv3 router ID.")
      elif low.startswith("import-route "):
        match = re.fullmatch(r"import-route\s+(isis|bgp)\s+(\d+)", cmd, re.I)
        if not match: return error("Error: Use import-route {isis|bgp} <process-id>.")
        protocol, process_id = match.groups()
        entry = {"protocol": protocol.lower(), "process": process_id}
        imports = process.setdefault("imports", [])
        if entry not in imports: imports.append(entry)
      else: return False
      self._persist(); self._prompt(); return True

    if self.mode == "VPN_INSTANCE":
      vpn = self.running["vpn_instances"][self.active_vpn_instance]
      if low == "ipv4-family": return True
      match = re.fullmatch(r"route-distinguisher\s+(\S+)", cmd, re.I)
      if match: vpn["rd"] = match.group(1)
      elif low == "apply-label per-instance": vpn["apply_label_per_instance"] = True
      elif low == "evpn mpls routing-enable": vpn["evpn_mpls_routing_enable"] = True
      elif low == "undo evpn mpls routing-enable": vpn["evpn_mpls_routing_enable"] = False
      elif low.startswith("vxlan vni "):
        match = re.fullmatch(r"vxlan\s+vni\s+(\d+)", cmd, re.I)
        if not match: return error("Error: Use vxlan vni <1-16777215>.")
        vni = int(match.group(1))
        if not 1 <= vni <= 16777215: return error("Error: VXLAN VNI must be from 1 to 16777215.")
        vpn["vxlan_vni"] = vni
      elif low.startswith("vpn-target "):
        match = re.fullmatch(r"vpn-target\s+(\S+)\s+(import-extcommunity|export-extcommunity)(?:\s+(evpn))?", cmd, re.I)
        if not match: return error("Error: Use vpn-target <ASN:NN> {import-extcommunity|export-extcommunity}.")
        rt, direction, evpn = match.groups(); key = "import_rts" if direction.lower().startswith("import") else "export_rts"
        if evpn: vpn["evpn_rts"] = True
        if rt not in vpn[key]: vpn[key].append(rt)
      elif low == "undo policy vpn-target": vpn["disable_rt_filter"] = True
      elif low.startswith("import-route ospf "):
        match = re.fullmatch(r"import-route\s+ospf\s+(\d+)(?:\s+route-policy\s+(\S+))?", cmd, re.I)
        if not match: return error("Error: Use import-route ospf <process-id> [route-policy <name>].")
        entry = {"process": match.group(1), "route_policy": match.group(2) or ""}
        if entry not in vpn["import_ospf"]: vpn["import_ospf"].append(entry)
      else: return False
      self._persist(); self._prompt(); return True
    if self.mode in ("BGP", "BGP_AF", "BGP_IPV6_AF", "BGP_VPN_AF", "BGP_EVPN_AF"):
      bgp = self.running.get("bgp") or {}
      if low == "ipv6-family unicast" and self.mode == "BGP":
        bgp.setdefault("address_family", {})["ipv6"] = True
        self.mode = "BGP_IPV6_AF"; self._persist(); self._prompt(); return True
      if self.mode == "BGP_IPV6_AF":
        match = re.fullmatch(r"peer\s+(\S+)\s+(as-number|connect-interface|enable|next-hop-local|reflect-client)", cmd, re.I)
        if match:
          peer, action = match.groups(); action = action.lower()
          data = bgp.setdefault("peers", {}).setdefault(peer, {"as_number": None, "connect_interface": "", "password": "", "valid_ttl_hops": None, "next_hop_local": False, "advertise_community": False, "route_policy": {}, "group": None, "import_prefix": None, "orf": ""})
          if action == "as-number":
            return error("Error: Configure peer AS number in BGP view before entering the address family.")
          if action == "connect-interface": return error("Error: Configure peer connect-interface in BGP view.")
          data.setdefault("address_families", {})["ipv6"] = True
          if action == "reflect-client": data["ipv6_reflect_client"] = True
          if action == "next-hop-local": data["ipv6_next_hop_local"] = True
          self._persist(); self._prompt(); return True
        match = re.fullmatch(r"network\s+(\S+)(?:\s+(\d+))?", cmd, re.I)
        if match:
          try:
            value = match.group(1)
            if "/" not in value and match.group(2): value = f"{value}/{match.group(2)}"
            network = ipaddress.IPv6Network(value, strict=False)
          except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError): return error("Error: Invalid IPv6 BGP network prefix.")
          networks = bgp.setdefault("ipv6_networks", [])
          if str(network) not in networks: networks.append(str(network))
          self._persist(); self._prompt(); return True
        match = re.fullmatch(r"import-route\s+(isis|ospfv3)\s+(\d+)", cmd, re.I)
        if match:
          protocol, process_id = match.groups(); entry = {"protocol": protocol.lower(), "process": process_id}
          imports = bgp.setdefault("ipv6_imports", [])
          if entry not in imports: imports.append(entry)
          self._persist(); self._prompt(); return True
        if low.startswith("peer ") and low.endswith(" enable"):
          peer = parts[1]
          bgp.setdefault("peers", {}).setdefault(peer, {}).setdefault("address_families", {})["ipv6"] = True
          self._persist(); self._prompt(); return True
        return False
      if low == "ipv4-family unicast" and self.mode == "BGP":
        self.mode = "BGP_AF"; self._prompt(); return True
      if low == "ipv4-family vpnv4" and self.mode == "BGP":
        bgp["vpnv4_enabled"] = True; self.mode = "BGP_AF"; self._persist(); self._prompt(); return True
      if low == "l2vpn-family evpn" and self.mode == "BGP":
        bgp["evpn_enabled"] = True; self.mode = "BGP_EVPN_AF"; self._persist(); self._prompt(); return True
      if self.mode == "BGP_EVPN_AF":
        match = re.fullmatch(r"peer\s+(\S+)\s+(enable|reflect-client)", cmd, re.I)
        if match:
          peer, action = match.groups()
          data = bgp.setdefault("peers", {}).setdefault(peer, {"as_number": None, "connect_interface": "", "password": "", "valid_ttl_hops": None, "next_hop_local": False, "advertise_community": False, "route_policy": {}, "group": None, "import_prefix": None, "orf": ""})
          data["evpn_enabled" if action.lower() == "enable" else "evpn_reflect_client"] = True
          self._persist(); self._prompt(); return True
        return False
      if low == "undo policy vpn-target" and self.mode == "BGP_AF":
        bgp["vpnv4_rt_filter_disabled"] = True; self._persist(); self._prompt(); return True
      match = re.fullmatch(r"ipv4-family\s+vpn-instance\s+(\S+)", cmd, re.I)
      if match and self.mode == "BGP":
        name = match.group(1)
        if name not in self.running.get("vpn_instances", {}): return error(f"Error: VPN instance {name} does not exist.")
        self.active_vpn_instance, self.mode = name, "BGP_VPN_AF"
        self.running["vpn_instances"][name].setdefault("bgp_peers", {})
        self._persist(); self._prompt(); return True
      if self.mode == "BGP_VPN_AF":
        vpn = self.running["vpn_instances"][self.active_vpn_instance]
        if low == "advertise l2vpn evpn":
          vpn["advertise_l2vpn_evpn"] = True; self._persist(); self._prompt(); return True
        match = re.fullmatch(r"peer\s+(\S+)\s+(as-number|connect-interface|substitute-as|allow-as-loop|enable)(?:\s+(\S+))?", cmd, re.I)
        if match:
          peer, option, value = match.groups(); option = option.lower()
          data = vpn["bgp_peers"].setdefault(peer, {"as_number": None, "connect_interface": "", "substitute_as": False, "allow_as_loop": 0, "enabled": False})
          if option == "as-number":
            try: data["as_number"] = int(value)
            except (TypeError, ValueError): return error("Error: Enter a valid peer AS number.")
          elif option == "connect-interface": data["connect_interface"] = (value or "").replace(" ", "")
          elif option == "substitute-as": data["substitute_as"] = True
          elif option == "allow-as-loop":
            try: data["allow_as_loop"] = max(1, int(value or 1))
            except ValueError: return error("Error: allow-as-loop must be a positive integer.")
          elif option == "enable": data["enabled"] = True
          self._persist(); self._prompt(); return True
        if low.startswith("import-route ospf "):
          match = re.fullmatch(r"import-route\s+ospf\s+(\d+)\s+route-policy\s+(\S+)", cmd, re.I)
          if not match: return error("Error: Use import-route ospf <process-id> route-policy <name>.")
          entry = {"process": match.group(1), "route_policy": match.group(2)}
          if entry not in vpn.setdefault("import_ospf", []): vpn["import_ospf"].append(entry)
          self._persist(); self._prompt(); return True
        return False
      match = re.fullmatch(r"router-id\s+(\S+)", cmd, re.I)
      if match and self.mode == "BGP":
        try: bgp["router_id"] = str(ipaddress.IPv4Address(match.group(1)))
        except ipaddress.AddressValueError: return error("Error: Enter a valid BGP router ID.")
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"group\s+(\S+)\s+(internal|external)", cmd, re.I)
      if match and self.mode == "BGP":
        bgp.setdefault("groups", {}).setdefault(match.group(1), {"type": match.group(2).lower(), "peers": [], "connect_interface": "", "next_hop_local": False, "reflect_client": False, "advertise_community": False, "route_policy": {}, "import_prefix": None, "orf": ""})
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"peer\s+(\S+)\s+(as-number|connect-interface|password|valid-ttl-hops|next-hop-local|advertise-community|route-policy|group|ip-prefix|capability-advertise|enable)\b(.*)", cmd, re.I)
      if match:
        peer, option, tail = match.groups(); option = option.lower(); tail = tail.strip()
        group_target = peer in bgp.setdefault("groups", {}) and option != "group"
        peer_data = (bgp["groups"][peer] if group_target else bgp.setdefault("peers", {}).setdefault(peer, {"as_number": None, "connect_interface": "", "password": "", "valid_ttl_hops": None, "next_hop_local": False, "advertise_community": False, "route_policy": {}, "group": None, "import_prefix": None, "orf": ""}))
        try:
          if option == "as-number":
            value = int(tail)
            if not 1 <= value <= 4294967295: raise ValueError
            peer_data["as_number"] = value
          elif option == "connect-interface": peer_data["connect_interface"] = tail.replace(" ", "")
          elif option == "password": peer_data["password"] = tail.removeprefix("cipher ")
          elif option == "valid-ttl-hops":
            value = int(tail)
            if not 1 <= value <= 255: raise ValueError
            peer_data["valid_ttl_hops"] = value
          elif option == "next-hop-local": peer_data["next_hop_local"] = True
          elif option == "advertise-community": peer_data["advertise_community"] = True
          elif option == "route-policy":
            words = tail.split()
            if len(words) != 2 or words[1].lower() not in ("import", "export"): raise ValueError
            peer_data.setdefault("route_policy", {})[words[1].lower()] = words[0]
          elif option == "group":
            if group_target: raise ValueError
            group_name = tail
            if group_name not in bgp.setdefault("groups", {}): raise ValueError
            peer_data["group"] = group_name
            group = bgp["groups"][group_name]
            if peer not in group.setdefault("peers", []): group["peers"].append(peer)
          elif option == "ip-prefix":
            words = tail.split()
            if len(words) != 2 or words[1].lower() != "import" or words[0] not in self.running.get("ip_prefix_lists", {}): raise ValueError
            peer_data["import_prefix"] = words[0]
          elif option == "capability-advertise":
            if tail.lower() != "orf ip-prefix send" and tail.lower() != "orf ip-prefix receive": raise ValueError
            peer_data["orf"] = tail.lower().split()[-1]
          elif option == "enable": peer_data["vpnv4_enabled"] = True
        except (ValueError, IndexError):
          return error(f"Error: Invalid BGP peer {option} configuration.")
        self._persist(); self._prompt(); return True
      if low.startswith("peer ") and low.endswith(" next-hop-local"):
        peer = parts[1]; bgp.setdefault("groups", {}).setdefault(peer, {})["next_hop_local"] = True
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"peer\s+(\S+)\s+reflect-client", cmd, re.I)
      if match and self.mode == "BGP_AF":
        bgp.setdefault("groups", {}).setdefault(match.group(1), {})["reflect_client"] = True
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"network\s+(\S+)\s+(\S+)", cmd, re.I)
      if match:
        try:
          network = ipaddress.IPv4Network(f"{match.group(1)}/{match.group(2)}", strict=False)
        except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
          return error("Error: Invalid BGP network prefix.")
        entry = {"network": str(network.network_address), "mask": str(network.netmask)}
        if entry not in bgp.setdefault("networks", []): bgp["networks"].append(entry)
        self._persist(); self._prompt(); return True
      if low.startswith("undo network "):
        try: network = ipaddress.IPv4Network(f"{parts[2]}/{parts[3]}", strict=False)
        except (ValueError, IndexError): return error("Error: Use undo network <prefix> <prefix-length>.")
        bgp["networks"] = [item for item in bgp.get("networks", []) if item.get("network") != str(network)]
        self._persist(); self._prompt(); return True
      return False

    if self.mode == "ROUTE_POLICY":
      policy = self.running.get("route_policies", {}).get(self.active_route_policy, {})
      node = policy.get(getattr(self, "_active_route_policy_node", "10"), {})
      match = re.fullmatch(r"if-match\s+(ip-prefix|community-filter|as-path-filter)\s+(\S+)", cmd, re.I)
      if match:
        field = {"ip-prefix": "ip_prefix", "community-filter": "community_filter", "as-path-filter": "as_path_filter"}[match.group(1).lower()]
        node[field] = match.group(2); self._persist(); self._prompt(); return True
      match = re.fullmatch(r"apply\s+community\s+(\d+:\d+)(?:\s+additive)?", cmd, re.I)
      if match:
        node["community"] = match.group(1); self._persist(); self._prompt(); return True
      if low == "quit":
        self.mode = "SYSTEM"; self.active_route_policy = None; self._persist(); self._prompt(); return True

    if self.mode == "INTERFACE":
      match = re.fullmatch(r"ospfv3\s+(\d+)\s+area\s+(\S+)\s+instance\s+(\d+)", cmd, re.I)
      if match:
        process_id, area, instance = match.groups()
        try: area = str(int(area, 0)) if re.fullmatch(r"(?:0x[0-9a-f]+|\d+)", area, re.I) else str(ipaddress.IPv4Address(area))
        except ipaddress.AddressValueError: return error("Error: Enter a valid OSPFv3 area ID.")
        self.running.setdefault("ospfv3_processes", {}).setdefault(process_id,
            {"router_id": "", "imports": [], "areas": {}}).setdefault("areas", {}).setdefault(area, {})
        for port in self.interface_ports:
          iface = self.running["interfaces"][port]
          iface.update(ipv6_enabled=True, ospfv3_process=process_id, ospfv3_area=area, ospfv3_instance=int(instance))
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"isis\s+ipv6\s+enable\s+(\d+)", cmd, re.I)
      if match:
        process_id = match.group(1)
        process = self.running.get("isis_processes", {}).get(process_id)
        if not process or not process.get("ipv6_enabled"): return error("Error: Enable IPv6 topology in the IS-IS process first.")
        for port in self.interface_ports:
          self.running["interfaces"][port].update(ipv6_enabled=True, isis_ipv6_process=process_id)
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"ip\s+binding\s+vpn-instance\s+(\S+)", cmd, re.I)
      if match:
        name = match.group(1)
        if name not in self.running.get("vpn_instances", {}): self.append_plain_text(f"Error: VPN instance {name} does not exist.")
        else:
          config["vpn_instance"] = name
          vpn = self.running["vpn_instances"][name]
          if self.interface not in vpn["interfaces"]: vpn["interfaces"].append(self.interface)
          self._persist()
        self._prompt(); return True
      if low == "isis circuit-type p2p":
        for port in active_ports: self.running["interfaces"][port]["isis_circuit_type"] = "p2p"
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"ospf\s+enable\s+(\d+)\s+area\s+(\S+)", cmd, re.I)
      if match:
        process_id, area_id = match.groups()
        try: int(area_id, 0)
        except ValueError: return error("Error: Enter a numeric OSPF area ID.")
        self.running.setdefault("ospf_processes", {}).setdefault(process_id,
            {"router_id": "", "areas": {}, "default_route_advertise": {"enabled": False, "always": False}}).setdefault("areas", {}).setdefault(area_id, {"networks": [], "authentication": None})
        for port in self.interface_ports:
          iface = self.running["interfaces"][port]; iface.update(ospf_enabled=True, ospf_process=process_id, ospf_area=area_id)
        self._persist(); self._prompt(); return True
      if low == "ospf bfd enable" or low == "undo ospf bfd enable":
        for port in self.interface_ports: self.running["interfaces"][port]["ospf_bfd"] = low == "ospf bfd enable"
        self._persist(); self._prompt(); return True
      if low.startswith("ospf bfd min-tx-interval "):
        match = re.fullmatch(r"ospf bfd min-tx-interval\s+(\d+)\s+min-rx-interval\s+(\d+)\s+detect-multiplier\s+(\d+)", cmd, re.I)
        if not match: return error("Error: Use ospf bfd min-tx-interval <ms> min-rx-interval <ms> detect-multiplier <1-255>.")
        tx, rx, multiplier = map(int, match.groups())
        if not 3 <= tx <= 10000 or not 3 <= rx <= 10000 or not 1 <= multiplier <= 255: return error("Error: Invalid OSPF BFD interval or detection multiplier.")
        for port in self.interface_ports: self.running["interfaces"][port]["ospf_bfd_params"] = {"tx": tx, "rx": rx, "multiplier": multiplier}
        self._persist(); self._prompt(); return True
      match = re.fullmatch(r"isis\s+enable\s+(\d+)", cmd, re.I)
      if match:
        process_id = match.group(1)
        if process_id not in self.running.get("isis_processes", {}): return error("Error: Configure the IS-IS process and NET first.")
        for port in self.interface_ports: self.running["interfaces"][port]["isis_process"] = process_id
        self._persist(); self._prompt(); return True
      return False
    return False

  def _handle_vxlan_command(self, command):
    """Configure the static and EVPN VXLAN behaviors used by HCIE Chapter 5."""
    cmd, low = command.strip(), command.strip().lower()
    def finish():
      self._persist(); self._prompt(); return True
    def error(message):
      self.append_plain_text(message); self._prompt(); return True

    if low in ("quit", "return", "exit") and self.mode in ("BRIDGE_DOMAIN", "NVE", "EVPN_INSTANCE"):
      self.mode = "SYSTEM"; self.active_bridge_domain = self.active_evpn_instance = None
      return finish()

    if low == "display vxlan tunnel" or low == "display vxlan peer":
      from modules.datacom_vxlan import vxlan_state
      conn = get_connection()
      try: state = vxlan_state(conn, self.username)
      finally: conn.close()
      if low.endswith("tunnel"):
        rows = state["tunnels"].get(self.node_name, [])
        output = ["Tunnel ID       Source          Destination     State  Type",
                  "-------------------------------------------------------------"]
        output.extend(f"{row['vni']:<15} {row['source']:<15} {row['destination']:<15} {row['state']:<6} {row['type']}" for row in rows)
        output.extend(["-------------------------------------------------------------",
                       f"Number of vxlan tunnel: {len(rows)}",
                       f"Total: {len(rows)}  Static: {sum(row['type']=='static' for row in rows)}  L2 dynamic: {sum(row['type']=='L2 dynamic' for row in rows)}  L3 dynamic: {sum(row['type']=='L3 dynamic' for row in rows)}"])
      else:
        rows = state["peers"].get(self.node_name, [])
        output = ["Vni ID          Source          Destination     Type", "----------------------------------------------------"]
        output.extend(f"{row['vni']:<15} {row['source']:<15} {row['destination']:<15} {row['type']}" for row in rows)
        output.append(f"Number of peers: {len(rows)}")
      self.append_plain_text("\n".join(output)); self._prompt(); return True
    if low.startswith("display bgp evpn all routing-table"):
      from modules.datacom_vxlan import vxlan_state
      conn = get_connection()
      try: routes = vxlan_state(conn, self.username)["evpn_routes"].get(self.node_name, [])
      finally: conn.close()
      if not routes and low == "display bgp evpn all routing-table":
        return False
      route_type = "inclusive-route" if "inclusive-route" in low else "mac-route" if "mac-route" in low else "all"
      routes = [row for row in routes if route_type == "all" or row["route_type"] == route_type]
      lines = ["EVPN address family", "Route Type  RD         VNI     MAC/IP or VTEP"]
      lines.extend(f"{row['route_type']:<11} {row['rd']:<10} {row['vni']:<7} {row.get('value','-')}" for row in routes)
      self.append_plain_text("\n".join(lines) if routes else "No matching BGP EVPN routes."); self._prompt(); return True

    if self.mode == "SYSTEM":
      match = re.fullmatch(r"bridge-domain\s+(\d+)", cmd, re.I)
      if match:
        bd = int(match.group(1))
        if not 1 <= bd <= 16777215: return error("Error: Bridge-domain ID must be from 1 to 16777215.")
        self.running.setdefault("bridge_domains", {}).setdefault(str(bd), {"vxlan_vni": None, "evpn_instance": ""})
        self.active_bridge_domain, self.mode = str(bd), "BRIDGE_DOMAIN"
        return finish()
      if low == "interface nve1":
        self.running.setdefault("nve", {"source": "", "vnis": {}})
        self.running["nve"].setdefault("vnis", {})
        self.mode = "NVE"; return finish()
      match = re.fullmatch(r"interface\s+vbdif\s*(\d+)", cmd, re.I)
      if match:
        bd_id = int(match.group(1)); name = f"Vbdif{bd_id}"
        self.running.setdefault("interfaces", {}).setdefault(name, {"ip": "", "mask": "", "description": "", "shutdown": False,
            "link_type": "route", "is_vbdif": True, "bridge_domain": bd_id, "arp_collect_host": False,
            "mac_address": "0000-5e00-0110", "ospf_enabled": False, "ospf_process": "1", "ospf_area": "0"})
        self.interface, self.interface_ports, self.mode = name, [name], "INTERFACE"
        return finish()
      match = re.fullmatch(r"evpn\s+vpn-instance\s+(\S+)\s+bd-mode", cmd, re.I)
      if match:
        name = match.group(1)
        self.running.setdefault("evpn_instances", {}).setdefault(name, {"mode": "bd", "rd": "", "import_rts": [], "export_rts": []})
        self.active_evpn_instance, self.mode = name, "EVPN_INSTANCE"
        return finish()
      if low == "vcmp role silent":
        self.running["vcmp_role"] = "silent"; return finish()
      return False

    if self.mode == "BRIDGE_DOMAIN":
      bd = self.running.setdefault("bridge_domains", {}).setdefault(str(self.active_bridge_domain), {"vxlan_vni": None, "evpn_instance": ""})
      match = re.fullmatch(r"vxlan\s+vni\s+(\d+)", cmd, re.I)
      if match:
        vni = int(match.group(1))
        if not 1 <= vni <= 16777215: return error("Error: VXLAN VNI must be from 1 to 16777215.")
        bd["vxlan_vni"] = vni; return finish()
      match = re.fullmatch(r"evpn\s+binding\s+vpn-instance\s+(\S+)", cmd, re.I)
      if match:
        if match.group(1) not in self.running.get("evpn_instances", {}): return error("Error: Create the EVPN VPN instance first.")
        bd["evpn_instance"] = match.group(1); return finish()
      return False

    if self.mode == "NVE":
      nve = self.running.setdefault("nve", {"source": "", "vnis": {}})
      match = re.fullmatch(r"source\s+(\S+)", cmd, re.I)
      if match:
        try: nve["source"] = str(ipaddress.IPv4Address(match.group(1)))
        except ipaddress.AddressValueError: return error("Error: Enter a valid IPv4 NVE source address.")
        return finish()
      match = re.fullmatch(r"vni\s+(\d+)\s+head-end\s+peer-list\s+(?:(\S+)(?:\s+protocol\s+(bgp))?|protocol\s+(bgp))", cmd, re.I)
      if match:
        vni, peer, protocol, bgp_protocol = match.groups()
        try: vni = int(vni)
        except ValueError: return error("Error: Use vni <1-16777215> head-end peer-list <IPv4> [protocol bgp].")
        if peer:
          try: peer = str(ipaddress.IPv4Address(peer))
          except ipaddress.AddressValueError: return error("Error: Enter a valid peer IPv4 address.")
        if not 1 <= vni <= 16777215: return error("Error: VXLAN VNI must be from 1 to 16777215.")
        entry = nve.setdefault("vnis", {}).setdefault(str(vni), {"peers": [], "protocol": "static"})
        if peer and peer not in entry["peers"]: entry["peers"].append(peer)
        if protocol or bgp_protocol: entry["protocol"] = "bgp"
        return finish()
      return False

    if self.mode == "EVPN_INSTANCE":
      evpn = self.running.setdefault("evpn_instances", {}).setdefault(self.active_evpn_instance, {"mode": "bd", "rd": "", "import_rts": [], "export_rts": []})
      match = re.fullmatch(r"route-distinguisher\s+(\S+)", cmd, re.I)
      if match: evpn["rd"] = match.group(1); return finish()
      match = re.fullmatch(r"vpn-target\s+(\S+)\s+(export-extcommunity|import-extcommunity)", cmd, re.I)
      if match:
        rt, direction = match.groups(); key = "export_rts" if direction.lower().startswith("export") else "import_rts"
        if rt not in evpn[key]: evpn[key].append(rt)
        return finish()
      return False

    if self.mode == "INTERFACE":
      iface = self.running.get("interfaces", {}).get(self.interface, {})
      if iface.get("subinterface"):
        if low == "mode l2": iface["mode"] = "l2"; return finish()
        match = re.fullmatch(r"encapsulation\s+dot1q\s+vid\s+(\d+)", cmd, re.I)
        if match:
          vlan = int(match.group(1))
          if not 1 <= vlan <= 4094: return error("Error: VLAN ID must be from 1 to 4094.")
          iface.update(vlan_type="dot1q", vlan_id=vlan); return finish()
        match = re.fullmatch(r"bridge-domain\s+(\d+)", cmd, re.I)
        if match:
          bd_id = match.group(1)
          if bd_id not in self.running.get("bridge_domains", {}): return error(f"Error: Bridge-domain {bd_id} does not exist.")
          iface["bridge_domain"] = int(bd_id); return finish()
      if iface.get("is_vbdif") and low == "arp collect host enable":
        iface["arp_collect_host"] = True; return finish()
      if iface.get("is_vbdif"):
        match = re.fullmatch(r"mac-address\s+([0-9a-f.:-]{12,17})", cmd, re.I)
        if match:
          iface["mac_address"] = match.group(1); return finish()
      return False
    return False

  def _handle_campus_command(self, command):
    """Store an NCE-style campus deployment plan and validate its VXLAN state."""
    cmd, low = command.strip(), command.strip().lower()
    def finish():
      self._persist(); self._prompt(); return True
    def error(message):
      self.append_plain_text(message); self._prompt(); return True

    if low == "display campus" or low == "display campus fabric":
      from modules.datacom_campus import campus_state
      conn = get_connection()
      try: state = campus_state(conn, self.username)
      finally: conn.close()
      lines = ["Campus VXLAN deployment", "Fabric       Device       Role       Status",
               "------------------------------------------------"]
      devices = [(name, row) for name, row in state["devices"].items() if row["fabric"] or row["role"]]
      for name, row in devices:
        vns = [vn for vn in row["vns"].values()]
        status = "Ready" if vns and all(not vn.get("issues") for vn in vns) else "Incomplete"
        lines.append(f"{row['fabric'] or '-':<12} {name:<12} {row['role'] or '-':<10} {status}")
      if not devices: lines.append("No campus devices have been assigned.")
      for fabric_name, fabric in sorted(state["fabrics"].items()):
        lines.extend(["", f"Fabric {fabric_name}: {len(fabric['devices'])} device(s), {len(fabric['vns'])} VN(s)"])
        for vn_name, vn in sorted(fabric["vns"].items()):
          lines.append(f"  VN {vn_name}: BD {vn['bd']} / VNI {vn['vni']} / VLAN {vn['vlan']} / gateway {vn['gateway'] or '-'} - {'Ready' if vn['ready'] else 'Incomplete'}")
          lines.extend(f"    ! {issue}" for issue in vn["issues"])
      self.append_plain_text("\n".join(lines)); self._prompt(); return True

    match = re.fullmatch(r"display campus vn\s+(\S+)", cmd, re.I)
    if match:
      from modules.datacom_campus import campus_state
      conn = get_connection()
      try: state = campus_state(conn, self.username)
      finally: conn.close()
      name = match.group(1)
      rows = [(fabric, vn) for fabric, data in state["fabrics"].items() for vn_name, vn in data["vns"].items() if vn_name.lower() == name.lower()]
      if not rows: return error(f"Error: Campus VN {name} is not configured.")
      lines = [f"Campus VN: {name}"]
      for fabric, vn in rows:
        lines.extend([f"Fabric: {fabric}", f"Bridge-domain: {vn['bd']}", f"VNI: {vn['vni']}",
                      f"Service VLAN: {vn['vlan']}", f"Gateway: {vn['gateway'] or '-'}",
                      f"Members: {', '.join(vn['members'])}", f"Status: {'Ready' if vn['ready'] else 'Incomplete'}"])
        lines.extend(f"  ! {issue}" for issue in vn["issues"])
      self.append_plain_text("\n".join(lines)); self._prompt(); return True

    if self.mode != "SYSTEM": return False
    match = re.fullmatch(r"campus\s+fabric\s+(\S+)\s+role\s+(border|edge|extended|access|wac|egress)(?:\s+as\s+(\d+))?", cmd, re.I)
    if match:
      fabric, role, as_number = match.groups()
      campus = self.running.setdefault("campus", {"fabric": "", "role": "", "as_number": None, "vns": {}})
      campus.update(fabric=fabric, role=role.lower(), as_number=int(as_number) if as_number else campus.get("as_number"))
      campus.setdefault("vns", {})
      return finish()
    match = re.fullmatch(r"campus\s+vn\s+(\S+)\s+bd\s+(\d+)\s+vni\s+(\d+)\s+vlan\s+(\d+)(?:\s+gateway\s+(\S+))?", cmd, re.I)
    if match:
      name, bd, vni, vlan, gateway = match.groups()
      bd, vni, vlan = int(bd), int(vni), int(vlan)
      if not 1 <= bd <= 16777215 or not 1 <= vni <= 16777215 or not 1 <= vlan <= 4094:
        return error("Error: Use valid BD/VNI IDs (1-16777215) and VLAN ID (1-4094).")
      if gateway:
        try: gateway = str(ipaddress.ip_interface(gateway))
        except ValueError: return error("Error: Gateway must use IPv4/prefix format, for example 172.17.10.254/24.")
        if not isinstance(ipaddress.ip_interface(gateway).ip, ipaddress.IPv4Address): return error("Error: Campus VN gateway must be IPv4.")
      campus = self.running.setdefault("campus", {"fabric": "", "role": "", "as_number": None, "vns": {}})
      campus.setdefault("vns", {})[name] = {"bd": bd, "vni": vni, "vlan": vlan, "gateway": gateway or ""}
      return finish()
    match = re.fullmatch(r"undo\s+campus\s+vn\s+(\S+)", cmd, re.I)
    if match:
      campus = self.running.get("campus", {})
      if match.group(1) not in campus.get("vns", {}): return error(f"Error: Campus VN {match.group(1)} is not configured.")
      del campus["vns"][match.group(1)]; return finish()
    if low == "undo campus fabric":
      self.running.pop("campus", None); return finish()
    return False

  def _handle_srv6_command(self, command):
    """Configure the SRv6 BE, VPN service-SID, and SRv6 policy lab model."""
    cmd, low = command.strip(), command.strip().lower()
    def finish(): self._persist(); self._prompt(); return True
    def error(message): self.append_plain_text(message); self._prompt(); return True
    sr = self.running.setdefault("srv6", {"enabled": False, "locators": {}, "policies": {}, "segment_lists": {}})

    if low in ("display segment-routing ipv6 locator", "display segment-routing ipv6 locator all"):
      from modules.datacom_srv6 import srv6_state
      conn = get_connection()
      try: rows = srv6_state(conn, self.username)["locators"].get(self.node_name, [])
      finally: conn.close()
      lines = ["Locator Name       IPv6 Prefix                         Status", "--------------------------------------------------------------"]
      lines.extend(f"{row['name']:<18} {row['prefix']:<36} {row['state']}" for row in rows)
      lines.append(f"Total: {len(rows)}")
      self.append_plain_text("\n".join(lines)); self._prompt(); return True
    match = re.fullmatch(r"display\s+segment-routing\s+ipv6\s+locator\s+(\S+)", cmd, re.I)
    if match:
      name = match.group(1)
      from modules.datacom_srv6 import srv6_state
      conn = get_connection()
      try: rows = srv6_state(conn, self.username)["locators"].get(self.node_name, [])
      finally: conn.close()
      row = next((item for item in rows if item["name"].lower() == name.lower()), None)
      if not row: return error(f"Error: SRv6 locator {name} does not exist.")
      self.append_plain_text(f"Locator name: {row['name']}\nIPv6 prefix: {row['prefix']}\nStatus: {row['state']}")
      self._prompt(); return True
    if low in ("display segment-routing ipv6 local-sid", "display segment-routing ipv6 local-sid forwarding"):
      from modules.datacom_srv6 import srv6_state
      conn = get_connection()
      try: rows = srv6_state(conn, self.username)["local_sids"].get(self.node_name, [])
      finally: conn.close()
      lines = ["SID                                      Behavior  Locator  VPN instance  Status", "-------------------------------------------------------------------------------"]
      lines.extend(f"{row['sid']:<40} {row['type']:<9} {row['locator']:<8} {row.get('vpn_instance') or '-':<13} {row['state']}" for row in rows)
      lines.append(f"Total: {len(rows)}")
      self.append_plain_text("\n".join(lines)); self._prompt(); return True
    if low in ("display segment-routing ipv6 forwarding", "display segment-routing ipv6 forwarding-table"):
      from modules.datacom_srv6 import srv6_state
      conn = get_connection()
      try: rows = srv6_state(conn, self.username)["forwarding"].get(self.node_name, [])
      finally: conn.close()
      lines = ["Prefix                               SID                                      Behavior State", "------------------------------------------------------------------------------------------"]
      lines.extend(f"{row['prefix']:<36} {row['sid']:<40} {row['type']:<8} {row['state']}" for row in rows)
      self.append_plain_text("\n".join(lines + [f"Total: {len(rows)}"]))
      self._prompt(); return True
    if low in ("display srv6-te policy", "display srv6-te policy all") or low.startswith("display srv6-te policy "):
      from modules.datacom_srv6 import srv6_state
      conn = get_connection()
      try: rows = srv6_state(conn, self.username)["policies"].get(self.node_name, [])
      finally: conn.close()
      name_match = re.fullmatch(r"display\s+srv6-te\s+policy\s+(\S+)", cmd, re.I)
      if name_match:
        rows = [row for row in rows if row["name"].lower() == name_match.group(1).lower()]
      lines = ["Policy Name       Endpoint                         Color  State", "---------------------------------------------------------------"]
      lines.extend(f"{row['name']:<18} {row['endpoint']:<32} {row['color']:<6} {'Up' if row['active'] else 'Down'}" for row in rows)
      for row in rows:
        for candidate in row["candidates"]:
          lines.append(f"  Candidate preference {candidate['preference']}: {candidate.get('segment_list','-')} SIDs {candidate.get('segments', [])} {candidate['state']}")
      self.append_plain_text("\n".join(lines + [f"Total: {len(rows)}"]))
      self._prompt(); return True

    if low in ("quit", "return", "exit") and self.mode in ("SRV6_LOCATOR", "SRV6_POLICY", "SRV6_POLICY_PATH", "SRV6_SEGMENT_LIST"):
      if self.mode == "SRV6_POLICY_PATH": self.mode = "SRV6_POLICY"
      elif self.mode in ("SRV6_POLICY", "SRV6_SEGMENT_LIST"): self.mode = "SYSTEM"
      else: self.mode = "SYSTEM"
      return finish()

    if self.mode == "SYSTEM":
      if low == "segment-routing ipv6":
        sr["enabled"] = True; return finish()
      match = re.fullmatch(r"locator\s+(\S+)", cmd, re.I)
      if match:
        name = match.group(1)
        sr.setdefault("locators", {}).setdefault(name, {"prefix": "", "prefix_length": 64})
        self.active_srv6_locator, self.mode = name, "SRV6_LOCATOR"
        return finish()
      match = re.fullmatch(r"srv6-te\s+policy\s+(\S+)\s+endpoint\s+(\S+)\s+color\s+(\d+)", cmd, re.I)
      if match:
        name, endpoint, color = match.groups()
        try: endpoint = str(ipaddress.IPv6Address(endpoint))
        except (ValueError, ipaddress.AddressValueError): return error("Error: SRv6 policy endpoint must be a valid IPv6 address.")
        sr.setdefault("policies", {}).setdefault(name, {"candidates": []}).update(endpoint=endpoint, color=int(color))
        self.active_srv6_policy, self.mode = name, "SRV6_POLICY"
        return finish()
      match = re.fullmatch(r"segment-list\s+(?:name\s+)?(\S+)", cmd, re.I)
      if match:
        name = match.group(1)
        sr.setdefault("segment_lists", {}).setdefault(name, [])
        self.active_srv6_segment_list, self.mode = name, "SRV6_SEGMENT_LIST"
        return finish()
    if self.mode == "SRV6_LOCATOR":
      match = re.fullmatch(r"ipv6-prefix\s+(\S+)\s+(\d+)(?:\s+.*)?", cmd, re.I)
      if match:
        try: prefix = ipaddress.IPv6Network(f"{match.group(1)}/{match.group(2)}", strict=False)
        except (ValueError, ipaddress.AddressValueError): return error("Error: Enter a valid IPv6 locator prefix and prefix length.")
        locator = sr["locators"][self.active_srv6_locator]
        locator.update(prefix=str(prefix.network_address), prefix_length=prefix.prefixlen)
        return finish()
    if self.mode == "ISIS":
      match = re.fullmatch(r"segment-routing\s+ipv6\s+locator\s+(\S+)", cmd, re.I)
      if match:
        name = match.group(1)
        if name not in sr.get("locators", {}): return error(f"Error: SRv6 locator {name} does not exist.")
        process = self.running.setdefault("isis_processes", {}).setdefault(str(self.active_isis_process), {})
        process["srv6_locator"] = name
        process["ipv6_enabled"] = True
        sr["enabled"] = True
        return finish()
    if self.mode in ("VPN_INSTANCE", "BGP_VPN_AF"):
      match = re.fullmatch(r"segment-routing\s+ipv6\s+locator\s+(\S+)", cmd, re.I)
      if match:
        name = match.group(1)
        if name not in sr.get("locators", {}): return error(f"Error: SRv6 locator {name} does not exist.")
        vpn = self.running.get("vpn_instances", {}).get(self.active_vpn_instance, {})
        vpn["srv6_locator"] = name
        vpn["srv6_service"] = "vpnv4"
        sr["enabled"] = True
        return finish()
      if low in ("srv6 service-type vpnv4", "srv6 service-type vpnv6"):
        vpn = self.running.get("vpn_instances", {}).get(self.active_vpn_instance, {})
        vpn["srv6_service"] = low.rsplit(" ", 1)[-1]
        return finish()
      if low == "segment-routing ipv6 best-effort":
        vpn = self.running.get("vpn_instances", {}).get(self.active_vpn_instance, {})
        vpn["srv6_transport"] = "best-effort"
        return finish()
      match = re.fullmatch(r"segment-routing\s+ipv6\s+policy\s+(\S+)", cmd, re.I)
      if match:
        vpn = self.running.get("vpn_instances", {}).get(self.active_vpn_instance, {})
        vpn["srv6_policy"] = match.group(1)
        return finish()
    if self.mode == "SRV6_POLICY":
      match = re.fullmatch(r"candidate-path\s+preference\s+(\d+)", cmd, re.I)
      if match:
        preference = int(match.group(1)); policy = sr["policies"][self.active_srv6_policy]
        candidate = next((row for row in policy["candidates"] if int(row["preference"]) == preference), None)
        if not candidate:
          candidate = {"preference": preference, "segment_list": "", "segments": []}; policy["candidates"].append(candidate)
        self.active_srv6_candidate, self.mode = preference, "SRV6_POLICY_PATH"
        return finish()
    if self.mode == "SRV6_POLICY_PATH":
      match = re.fullmatch(r"segment-list\s+(\S+)", cmd, re.I)
      if match:
        name = match.group(1)
        if name not in sr.get("segment_lists", {}): return error(f"Error: SRv6 segment-list {name} does not exist.")
        candidate = next(row for row in sr["policies"][self.active_srv6_policy]["candidates"]
                         if int(row["preference"]) == int(self.active_srv6_candidate))
        candidate["segment_list"] = name
        candidate["segments"] = [row["sid"] for row in sr["segment_lists"][name]]
        return finish()
    if self.mode == "SRV6_SEGMENT_LIST":
      match = re.fullmatch(r"index\s+(\d+)\s+sid\s+ipv6\s+(\S+)", cmd, re.I)
      if match:
        index, sid = int(match.group(1)), match.group(2)
        try: sid = str(ipaddress.IPv6Address(sid))
        except (ValueError, ipaddress.AddressValueError): return error("Error: Segment SID must be a valid IPv6 address.")
        if not 1 <= index <= 65535: return error("Error: SRv6 segment-list index must be 1-65535.")
        rows = sr["segment_lists"][self.active_srv6_segment_list]
        rows[:] = [row for row in rows if int(row["index"]) != index]
        rows.append({"index": index, "sid": sid}); rows.sort(key=lambda row: row["index"])
        return finish()
    if self.mode == "BGP_VPN_AF":
      if low == "segment-routing ipv6 best-effort":
        vpn = self.running.get("vpn_instances", {}).get(self.active_vpn_instance, {})
        vpn["srv6_transport"] = "best-effort"
        return finish()
      if low.startswith("segment-routing ipv6 policy"):
        match = re.fullmatch(r"segment-routing\s+ipv6\s+policy\s+(\S+)", cmd, re.I)
        if not match: return error("Error: Use segment-routing ipv6 policy <policy-name>.")
        vpn = self.running.get("vpn_instances", {}).get(self.active_vpn_instance, {})
        vpn["srv6_policy"] = match.group(1)
        return finish()
    return False

  def _handle_srmpls_command(self, command):
    """Configure the SR-MPLS lab functions described in HCIE Chapter 8."""
    cmd, low = command.strip(), command.strip().lower()
    def finish():
      self._persist(); self._prompt(); return True
    def error(message):
      self.append_plain_text(message); self._prompt(); return True

    if low == "display tunnel-info all" or low.startswith("display tunnel-info all "):
      from modules.datacom_srmpls import sr_state
      conn = get_connection()
      try: state = sr_state(conn, self.username)
      finally: conn.close()
      rows = []
      rows.extend({"id": f"SR-{row['label']}", "type": "srbe-lsp", "dest": row["destination"], "state": row["state"]}
                  for row in state["lsps"].get(self.node_name, []))
      rows.extend({"id": row["name"], "type": "sr-te" if row.get("sr_te") else "sr-te-tunnel",
                   "dest": row["destination"], "state": row["state"]}
                  for row in state["te_tunnels"].get(self.node_name, []))
      rows.extend({"id": f"Policy-{row['name']}", "type": "sr-te-policy",
                   "dest": row["endpoint"], "state": "UP" if row["active"] else "DOWN"}
                  for row in state["policies"].get(self.node_name, []))
      lines = ["Tunnel ID             Type       Destination    Status", "-------------------------------------------------------"]
      lines.extend(f"{row['id']:<21} {row['type']:<10} {row['dest']:<14} {row['state']}" for row in rows)
      lines.append(f"Total: {len(rows)}")
      self.append_plain_text("\n".join(lines)); self._prompt(); return True

    if low == "display segment-routing prefix mpls forwarding":
      from modules.datacom_srmpls import sr_state
      conn = get_connection()
      try: rows = sr_state(conn, self.username)["forwarding"].get(self.node_name, [])
      finally: conn.close()
      lines = ["Segment Routing Prefix MPLS Forwarding Information",
               "Prefix             Label  OutLabel  Interface    NextHop      State",
               "-----------------------------------------------------------------------"]
      lines.extend(f"{row['prefix']:<18} {row['label']:<6} {row['out_label']:<9} {row.get('interface','-'):<12} {row.get('next_hop','-'):<12} {row['state']}" for row in rows)
      lines.append(f"Total information(s): {len(rows)}")
      self.append_plain_text("\n".join(lines)); self._prompt(); return True

    match = re.fullmatch(r"display\s+sr-te\s+policy(?:-name)?\s+(\S+)", cmd, re.I)
    if match:
      from modules.datacom_srmpls import sr_state
      conn = get_connection()
      try: policies = sr_state(conn, self.username)["policies"].get(self.node_name, [])
      finally: conn.close()
      policy = next((row for row in policies if row["name"].lower() == match.group(1).lower()), None)
      if not policy: return error(f"Error: SR-TE policy {match.group(1)} does not exist.")
      lines = [f"SR-TE Policy: {policy['name']}", f"Endpoint: {policy['endpoint']}", f"Color: {policy['color']}",
               f"Policy State: {'Up' if policy['active'] else 'Down'}"]
      for candidate in sorted(policy["candidates"], key=lambda row: int(row.get("preference", 0)), reverse=True):
        lines.append(f"Candidate preference {candidate['preference']}: segment-list {candidate.get('segment_list','-')} labels {candidate['labels']} state {candidate['state']}" +
                     (" (active)" if candidate is policy["active"] else ""))
      self.append_plain_text("\n".join(lines)); self._prompt(); return True

    if low.startswith("ping lsp segment-routing ip "):
      match = re.fullmatch(r"ping\s+lsp\s+segment-routing\s+ip\s+(\S+)(?:\s+\d+)?(?:\s+version\s+\S+)?", cmd, re.I)
      if not match: return error("Error: Use ping lsp segment-routing ip <destination> [prefix-length] [version draft2].")
      destination = match.group(1)
      from modules.datacom_srmpls import sr_state
      conn = get_connection()
      try: rows = sr_state(conn, self.username)["lsps"].get(self.node_name, [])
      finally: conn.close()
      row = next((item for item in rows if item["destination"] == destination), None)
      if row and row["state"] == "UP":
        output = f"LSP PING FEC: SEGMENT ROUTING IPV4 PREFIX {destination} : 100 data bytes, press CTRL_C to break\n" + "\n".join(f"Reply from {destination}: bytes=100 Sequence={i} time=1 ms" for i in range(1, 6)) + f"\n\n--- {destination} ping statistics ---\n5 packet(s) transmitted\n5 packet(s) received\n0.00% packet loss"
      else:
        output = f"LSP PING FEC: SEGMENT ROUTING IPV4 PREFIX {destination} : 100 data bytes\nDestination unreachable: no SR-MPLS LSP is available."
      self.append_plain_text(output); self._prompt(); return True

    if low.startswith("ping lsp te tunnel "):
      match = re.fullmatch(r"ping\s+lsp\s+te\s+tunnel\s+(\d+)", cmd, re.I)
      if not match: return error("Error: Use ping lsp te tunnel <tunnel-id>.")
      tunnel_name = f"Tunnel{match.group(1)}"
      from modules.datacom_srmpls import sr_state
      conn = get_connection()
      try: rows = sr_state(conn, self.username)["te_tunnels"].get(self.node_name, [])
      finally: conn.close()
      row = next((item for item in rows if item["name"].lower() == tunnel_name.lower()), None)
      text = (f"SR-TE tunnel {tunnel_name} to {row['destination']} is UP.\n5 packet(s) transmitted\n5 packet(s) received\n0.00% packet loss"
              if row and row["state"] == "UP" else f"SR-TE tunnel {tunnel_name} is DOWN.")
      self.append_plain_text(text); self._prompt(); return True

    if low in ("quit", "return", "exit") and self.mode in ("SEGMENT_ROUTING", "EXPLICIT_PATH", "SEGMENT_LIST", "SR_POLICY", "SR_POLICY_PATH", "TUNNEL_POLICY"):
      if self.mode == "SR_POLICY_PATH": self.mode = "SR_POLICY"
      elif self.mode in ("SEGMENT_LIST", "SR_POLICY"): self.mode = "SEGMENT_ROUTING"
      else: self.mode = "SYSTEM"
      return finish()

    if self.mode == "SYSTEM":
      if low == "segment-routing":
        self.running.setdefault("segment_routing", {"enabled": False, "adjacency_sids": []})["enabled"] = True
        self.mode = "SEGMENT_ROUTING"; return finish()
      match = re.fullmatch(r"explicit-path\s+(\S+)", cmd, re.I)
      if match:
        name = match.group(1)
        self.running.setdefault("sr_mpls", {"explicit_paths": {}, "segment_lists": {}, "policies": {}}).setdefault("explicit_paths", {}).setdefault(name, [])
        self.active_explicit_path, self.mode = name, "EXPLICIT_PATH"
        return finish()
      match = re.fullmatch(r"tunnel-policy\s+(\S+)", cmd, re.I)
      if match:
        name = match.group(1)
        self.running.setdefault("tunnel_policies", {}).setdefault(name, {"protocols": [], "load_balance": 1, "unmix": False})
        self.active_tunnel_policy, self.mode = name, "TUNNEL_POLICY"
        return finish()
    if self.mode == "SEGMENT_ROUTING":
      match = re.fullmatch(r"ipv4\s+adjacency\s+local-ip-addr\s+(\S+)\s+remote-ip-addr\s+(\S+)\s+sid\s+(\d+)", cmd, re.I)
      if match:
        local, remote, sid = match.groups()
        try: local, remote = str(ipaddress.IPv4Address(local)), str(ipaddress.IPv4Address(remote))
        except ipaddress.AddressValueError: return error("Error: Adjacency SID addresses must be valid IPv4 addresses.")
        self.running.setdefault("segment_routing", {}).setdefault("adjacency_sids", []).append({"local": local, "remote": remote, "sid": int(sid)})
        return finish()
      match = re.fullmatch(r"segment-list\s+name\s+(\S+)", cmd, re.I)
      if match:
        name = match.group(1)
        self.running.setdefault("sr_mpls", {"explicit_paths": {}, "segment_lists": {}, "policies": {}}).setdefault("segment_lists", {}).setdefault(name, [])
        self.active_segment_list, self.mode = name, "SEGMENT_LIST"
        return finish()
      match = re.fullmatch(r"sr-te\s+policy\s+(\S+)\s+endpoint\s+(\S+)\s+color\s+(\d+)", cmd, re.I)
      if match:
        name, endpoint, color = match.groups()
        try: endpoint = str(ipaddress.IPv4Address(endpoint))
        except ipaddress.AddressValueError: return error("Error: SR-TE endpoint must be a valid IPv4 address.")
        policy = self.running.setdefault("sr_mpls", {"explicit_paths": {}, "segment_lists": {}, "policies": {}}).setdefault("policies", {}).setdefault(name, {"candidates": []})
        policy.update(endpoint=endpoint, color=int(color))
        self.active_sr_policy, self.mode = name, "SR_POLICY"
        return finish()
    if self.mode == "EXPLICIT_PATH":
      match = re.fullmatch(r"next\s+sid\s+label\s+(\d+)\s+type\s+(prefix|adjacency)", cmd, re.I)
      if match:
        label, kind = match.groups(); self.running["sr_mpls"]["explicit_paths"][self.active_explicit_path].append({"label": int(label), "type": kind.lower()})
        return finish()
    if self.mode == "SEGMENT_LIST":
      match = re.fullmatch(r"index\s+(\d+)\s+mpls\s+label\s+(\d+)", cmd, re.I)
      if match:
        index, label = map(int, match.groups())
        if not 1 <= index <= 65535 or not 16 <= label <= 1048575:
          return error("Error: Segment-list index must be 1-65535 and MPLS label 16-1048575.")
        rows = self.running["sr_mpls"]["segment_lists"][self.active_segment_list]
        rows[:] = [row for row in rows if int(row["index"]) != index]
        rows.append({"index": index, "label": label}); rows.sort(key=lambda row: row["index"])
        return finish()
    if self.mode == "SR_POLICY":
      match = re.fullmatch(r"candidate-path\s+preference\s+(\d+)", cmd, re.I)
      if match:
        preference = int(match.group(1)); policy = self.running["sr_mpls"]["policies"][self.active_sr_policy]
        candidate = next((item for item in policy["candidates"] if int(item["preference"]) == preference), None)
        if not candidate:
          candidate = {"preference": preference, "segment_list": ""}; policy["candidates"].append(candidate)
        self.active_sr_candidate, self.mode = preference, "SR_POLICY_PATH"
        return finish()
    if self.mode == "SR_POLICY_PATH":
      match = re.fullmatch(r"segment-list\s+(\S+)", cmd, re.I)
      if match:
        name = match.group(1)
        if name not in self.running["sr_mpls"]["segment_lists"]: return error(f"Error: Segment-list {name} does not exist.")
        policy = self.running["sr_mpls"]["policies"][self.active_sr_policy]
        candidate = next(row for row in policy["candidates"] if int(row["preference"]) == int(self.active_sr_candidate))
        candidate["segment_list"] = name
        return finish()
    if self.mode == "TUNNEL_POLICY":
      match = re.fullmatch(r"tunnel\s+select-seq\s+(sr-te|cr-lsp|srbe-lsp)(?:\s+load-balance-number\s+(\d+))?(?:\s+(unmix))?", cmd, re.I)
      if match:
        protocol, count, unmix = match.groups()
        policy = self.running["tunnel_policies"][self.active_tunnel_policy]
        if protocol.lower() not in policy["protocols"]: policy["protocols"].append(protocol.lower())
        policy["load_balance"] = int(count or 1)
        policy["unmix"] = bool(unmix)
        return finish()
    if self.mode in ("VPN_INSTANCE", "BGP_VPN_AF"):
      vpn = self.running.get("vpn_instances", {}).get(self.active_vpn_instance, {})
      match = re.fullmatch(r"(?:tnl-policy|tunnel-policy)\s+(\S+)", cmd, re.I)
      if match:
        name = match.group(1)
        if name not in self.running.get("tunnel_policies", {}): return error(f"Error: Tunnel policy {name} does not exist.")
        vpn["tunnel_policy"] = name
        return finish()
      match = re.fullmatch(r"default-color\s+(\d+)", cmd, re.I)
      if match:
        color = int(match.group(1))
        if not 0 <= color <= 4294967295: return error("Error: Color value must be from 0 to 4294967295.")
        vpn["default_color"] = color
        return finish()
    if self.mode == "ISIS":
      process = self.running["isis_processes"][self.active_isis_process]
      if low == "segment-routing mpls": process["segment_routing_mpls"] = True; return finish()
      if low == "traffic-eng level-2": process["traffic_eng_level"] = "level-2"; return finish()
      match = re.fullmatch(r"segment-routing\s+global-block\s+(\d+)\s+(\d+)", cmd, re.I)
      if match:
        low_label, high_label = map(int, match.groups())
        if not 16 <= low_label < high_label <= 1048575: return error("Error: SRGB range must be valid labels from 16 to 1048575.")
        process["srgb"] = [low_label, high_label]; return finish()
    if self.mode == "INTERFACE":
      iface = self.running.get("interfaces", {}).get(self.interface, {})
      if iface.get("is_loopback"):
        match = re.fullmatch(r"isis\s+prefix-sid\s+index\s+(\d+)", cmd, re.I)
        if match:
          index = int(match.group(1))
          if not 1 <= index <= 1048575: return error("Error: Prefix-SID index must be from 1 to 1048575.")
          iface["isis_prefix_sid_index"] = index; return finish()
    if self.mode == "MPLS":
      if low == "mpls te": self.running["mpls_te_enabled"] = True; return finish()
    if self.mode == "INTERFACE":
      iface = self.running.get("interfaces", {}).get(self.interface, {})
      if iface.get("is_tunnel"):
        if low == "tunnel-protocol mpls te": iface["sr_te"] = True; return finish()
        match = re.fullmatch(r"destination\s+(\S+)", cmd, re.I)
        if match:
          try: iface["destination"] = str(ipaddress.IPv4Address(match.group(1)))
          except ipaddress.AddressValueError: return error("Error: Tunnel destination must be a valid IPv4 address.")
          return finish()
        if low == "mpls te signal-protocol segment-routing": iface["sr_te"] = True; return finish()
        match = re.fullmatch(r"mpls\s+te\s+tunnel-id\s+(\d+)", cmd, re.I)
        if match: iface["tunnel_id"] = int(match.group(1)); return finish()
        match = re.fullmatch(r"mpls\s+te\s+path\s+explicit-path\s+(\S+)", cmd, re.I)
        if match:
          name = match.group(1)
          if name not in self.running.get("sr_mpls", {}).get("explicit_paths", {}): return error(f"Error: Explicit path {name} does not exist.")
          iface["explicit_path"] = name; return finish()
        match = re.fullmatch(r"ip\s+address\s+unnumbered\s+interface\s+(loopback\s*\d+)", cmd, re.I)
        if match: iface["unnumbered_interface"] = "LoopBack" + re.search(r"\d+", match.group(1)).group(); return finish()
    return False

  def _router_id_for_display(self):
    for name in ("LoopBack0", "Loopback0"):
      iface = self.running.get("interfaces", {}).get(name, {})
      if iface.get("ip"): return iface["ip"]
    return "0.0.0.0"

  def _bgp_peer_established(self, peer, info):
    try:
      from modules.datacom_bgp import bgp_state
      conn = get_connection()
      sessions = bgp_state(conn, self.username).get("sessions", {}).get(self.node_name, [])
      return any(row.get("peer_ip") == peer for row in sessions)
    except Exception:
      return False
    finally:
      try: conn.close()
      except Exception: pass

  def _display_bgp_routes(self, command):
    from modules.datacom_bgp import bgp_state
    conn = get_connection()
    try: routes = bgp_state(conn, self.username).get("routes", {}).get(self.node_name, [])
    finally: conn.close()
    filter_text = command.partition("|")[2].strip().split()[-1] if "|" in command else ""
    lines = [f"BGP Local router ID is {(self.running.get('bgp') or {}).get('router_id') or self._router_id_for_display()}",
             "Network             NextHop          MED   LocPrf  PrefVal  Path/Ogn"]
    for route in routes:
      network = str(route["network"])
      if filter_text and filter_text not in network: continue
      lines.append(f"{'>*' if route.get('best') else '* '} {network:<19} {route.get('next_hop','0.0.0.0'):<15} {route.get('med',0):<5} {route.get('local_pref','-')!s:<7} {route.get('preference',0):<8} {' '.join(map(str, route.get('as_path', [])))}{route.get('origin','i')}")
    self.append_plain_text("\n".join(lines)); self._prompt()

  def _display_ospf_routing(self, command):
    from modules.datacom_network import datacom_routing_table
    parts = command.split()
    target = parts[2] if len(parts) > 2 else ""
    conn = get_connection()
    try:
      rows = [row for row in datacom_routing_table(conn, self.username, self.node_name)
              if row.get("protocol") in ("OSPF", "O_ASE")]
      for process in (self.running.get("ospf_processes") or {}).values():
        imported = process.get("import_bgp")
        vpn_name = process.get("vpn_instance")
        if not imported or not vpn_name: continue
        from modules.datacom_mpls import mpls_state
        vpn_routes = mpls_state(conn, self.username)["vpn_routes"].get((self.node_name, vpn_name), [])
        for route in vpn_routes:
          rows.append({"network": route["network"], "protocol": "O_ASE", "cost": imported.get("cost", 0),
                       "next_hop": route.get("next_hop", "-"), "interface": "-", "active": True,
                       "vpn_instance": vpn_name})
    finally:
      conn.close()
    if target:
      try:
        address = ipaddress.IPv4Address(target)
        rows = [row for row in rows if address in row["network"]]
      except ipaddress.AddressValueError:
        rows = []
    lines = ["OSPF Routing Tables", "Destination/Mask  Cost  Type       NextHop       Interface"]
    for row in rows:
      lines.append(f"{str(row['network']):<18} {row.get('cost', 0):<5} {row.get('protocol','OSPF'):<10} {row.get('next_hop','-'):<13} {row.get('interface','-')}")
      if row.get("backup_next_hop"):
        lines.append(f"  Backup NextHop: {row['backup_next_hop']}  Backup Interface: {row.get('backup_interface','-')}  Backup Type: {row.get('backup_type','LFA')}")
    if not rows:
      lines.append("No OSPF route information.")
    self.append_plain_text("\n".join(lines)); self._prompt()

  def _display_isis_routes(self, command):
    from modules.datacom_isis import isis_state
    conn = get_connection()
    try: routes = isis_state(conn, self.username).get("routes", {}).get(self.node_name, [])
    finally: conn.close()
    target = command.split()[-1] if len(command.split()) > 3 else ""
    lines = ["Route information for IS-IS", "IPv4 Destination     IntCost  ExitInterface  NextHop"]
    for route in routes:
      if target and target not in str(route["network"]): continue
      lines.append(f"{str(route['network']):<20} {route.get('cost',1):<8} {route.get('interface','-'):<14} {route.get('next_hop','-')}")
    self.append_plain_text("\n".join(lines)); self._prompt()

  def _display_ospf_bfd(self, command):
    from modules.datacom_ospf import ospf_state
    conn = get_connection()
    try: state = ospf_state(conn, self.username)
    finally: conn.close()
    rows = []
    for peer in state.get("neighbors", {}).get(self.node_name, []):
      iface = (state["devices"].get(self.node_name, {}).get("cfg", {}).get("interfaces", {}).get(peer["interface"], {}))
      process = state["devices"].get(self.node_name, {}).get("processes", {}).get(peer["process"], {})
      if iface.get("ospf_bfd") or process.get("bfd_all_interfaces"):
        params = iface.get("ospf_bfd_params", {"tx": 1000, "rx": 1000, "multiplier": 3})
        rows.append(f"NeighborId:{peer['address']} AreaId:{peer['area']} Interface:{peer['interface']}\nBFDState:Up rx:{params['rx']} tx:{params['tx']} Multiplier:{params['multiplier']}")
    self.append_plain_text("\n\n".join(rows) if rows else "No OSPF BFD sessions."); self._prompt()

  def handle_user_input(self, command):
    cmd = command.strip()
    low = cmd.lower()
    if self.pending == "reboot_enter":
      self._pending_input(cmd)
      return
    if not cmd:
      self._prompt()
      return
    if self._handle_srv6_command(cmd):
      return
    if self._handle_srmpls_command(cmd):
      return
    if self._handle_campus_command(cmd):
      return
    if self._handle_vxlan_command(cmd):
      return
    if self._handle_advanced_routing_command(cmd):
      return
    if self._handle_wlan_command(cmd):
      return
    if low == "display lldp neighbor brief" or low == "display lldp neighbor":
      self._display_lldp_neighbors()
      return
    if self.mode == "SYSTEM" and low in ("lldp enable", "undo lldp enable"):
      self.running["lldp_enabled"] = low == "lldp enable"
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low in ("set ztp disable", "set ztp enable"):
      self.running["ztp_enabled"] = low.endswith("enable")
      self._persist()
      self._prompt()
      return
    if getattr(self, "remote_active", False) and self.mode == "USER" and low in ("quit", "exit", "return"):
      self._persist()
      self._leave_remote_session()
      return
    if self.mode == "FTP":
      self._handle_ftp_command(cmd)
      return
    if self.mode in ("AUTH_SCHEME", "AUTHZ_SCHEME", "ACCOUNTING_SCHEME", "AAA_DOMAIN") and low in ("quit", "return"):
      self.mode = "USER" if low == "return" else "AAA"
      self._prompt()
      return
    if self.mode == "VTY" and low in ("quit", "return"):
      self.mode, self.active_vty = ("USER" if low == "return" else "SYSTEM"), None
      self._persist()
      self._prompt()
      return
    if self.mode == "NAT_RULE" and low in ("quit", "return"):
      self.mode = "USER" if low == "return" else "NAT_POLICY"
      self._prompt()
      return
    if self.mode in ("NAT_ADDRESS_GROUP", "NAT_POLICY") and low in ("quit", "return"):
      self.mode = "USER" if low == "return" else "SYSTEM"
      self._prompt()
      return
    if self.mode == "AAA" and low in ("quit", "return"):
      self.mode = "USER" if low == "return" else "SYSTEM"
      self._prompt()
      return
    if self.mode == "AAA" and low.startswith("local-user "):
      result = self._configure_aaa_user(cmd)
      if result:
        self.append_plain_text(result)
      if self.pending not in ("aaa_service_confirm", "aaa_privilege_confirm"):
        self._persist()
        self._prompt()
      return
    if self.mode == "AAA" and low.startswith("authentication-scheme "):
      name = cmd.split(None, 1)[1].strip()
      if not re.fullmatch(r"[\w.-]{1,32}", name):
        self.append_plain_text("Error: Invalid authentication scheme name.")
      else:
        self.active_auth_scheme = name
        self.running.setdefault("authentication_schemes", {}).setdefault(name, {"mode": "local"})
        self.mode = "AUTH_SCHEME"
        self._persist()
      self._prompt()
      return
    if self.mode == "AAA" and low.startswith("authorization-scheme "):
      name = cmd.split(None, 1)[1].strip()
      if not re.fullmatch(r"[\w.-]{1,32}", name):
        self.append_plain_text("Error: Invalid authorization scheme name.")
      else:
        self.active_authz_scheme = name
        self.running.setdefault("authorization_schemes", {}).setdefault(name, {"mode": "local"})
        self.mode = "AUTHZ_SCHEME"
        self._persist()
      self._prompt()
      return
    if self.mode == "AAA" and low.startswith("accounting-scheme "):
      name = cmd.split(None, 1)[1].strip()
      self.active_accounting_scheme = name
      self.running.setdefault("accounting_schemes", {}).setdefault(name, {"mode": "none"})
      self.mode = "ACCOUNTING_SCHEME"
      self._persist()
      self._prompt()
      return
    if self.mode == "AAA" and low.startswith("domain "):
      name = cmd.split(None, 1)[1].strip()
      if not re.fullmatch(r"[\w.-]{1,32}", name):
        self.append_plain_text("Error: Invalid domain name.")
      else:
        self.active_aaa_domain = name
        self.running.setdefault("aaa_domains", {}).setdefault(name, {"authentication_scheme": "default", "authorization_scheme": "default", "accounting_scheme": "default"})
        self.mode = "AAA_DOMAIN"
        self._persist()
      self._prompt()
      return
    if self.mode in ("AUTH_SCHEME", "AUTHZ_SCHEME", "ACCOUNTING_SCHEME", "AAA_DOMAIN"):
      result = self._configure_aaa_submode(cmd)
      if result is not None:
        if result:
          self.append_plain_text(result)
        self._persist()
        self._prompt()
        return
    if self.mode in ("NAT_ADDRESS_GROUP", "NAT_POLICY", "NAT_RULE"):
      result = self._configure_nat_view(cmd)
      if result is not None:
        if result:
          self.append_plain_text(result)
        self._persist()
        self._prompt()
        return
    if self.mode == "VTY":
      result = self._configure_vty(cmd)
      if result is not None:
        if result:
          self.append_plain_text(result)
        self._persist()
        self._prompt()
        return
    if self.mode == "SYSTEM" and low == "aaa":
      self.running.setdefault("aaa_users", {})
      self.mode = "AAA"
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("nat address-group "):
      match = re.fullmatch(r"nat address-group\s+([\w.-]{1,63})\s+(\d+)", cmd, re.I)
      if not match or not 0 <= int(match.group(2)) <= 65535:
        self.append_plain_text("Error: Use nat address-group <name> <0-65535>.")
        self._prompt()
      else:
        name, group_id = match.group(1), int(match.group(2))
        self.active_nat_group = name
        self.running.setdefault("nat_address_groups", {}).setdefault(name, {"id": group_id, "mode": "pat", "sections": []})
        self.mode = "NAT_ADDRESS_GROUP"
        self._persist()
        self._prompt()
      return
    if self.mode == "SYSTEM" and low == "nat-policy":
      self.running.setdefault("nat_policy_rules", {})
      self.mode = "NAT_POLICY"
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and (low.startswith("nat server ") or low.startswith("undo nat server ")):
      result = self._configure_nat_server(cmd)
      if result:
        self.append_plain_text(result)
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low == "telnet server enable":
      self.running["telnet_server_enabled"] = True
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low == "undo telnet server enable":
      self.running["telnet_server_enabled"] = False
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and (low.startswith("telnet server source ") or low.startswith("telnet server-source ")):
      source = cmd.split()[-1]
      if source.lower() != "all-interface" and source not in self.running.get("interfaces", {}):
        self.append_plain_text("Error: Use all-interface or a configured interface.")
        self._prompt()
        return
      self.pending_remote_source = source
      self.pending = "telnet_source_confirm"
      self._write("Warning: Telnet server source changes can affect remote access. Continue? [Y/N]:")
      self.prompt_position = len(self.terminal.toPlainText())
      return
    if self.mode == "SYSTEM" and low == "stelnet server enable":
      self.running["stelnet_server_enabled"] = True
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low == "undo stelnet server enable":
      self.running["stelnet_server_enabled"] = False
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("ssh server port "):
      match = re.fullmatch(r"ssh server port\s+(\d+)", cmd, re.I)
      if not match or not 1 <= int(match.group(1)) <= 65535:
        self.append_plain_text("Error: Use ssh server port <1-65535>.")
      else:
        self.running["ssh_server_port"] = int(match.group(1))
        self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("ssh server-source "):
      source = cmd.split()[-1]
      if source.lower() != "all-interface" and source not in self.running.get("interfaces", {}):
        self.append_plain_text("Error: Use all-interface or a configured interface.")
        self._prompt()
        return
      self.pending_remote_source = source
      self.pending = "ssh_source_confirm"
      self._write("Warning: SSH server source changes can affect remote access. Continue? [Y/N]:")
      self.prompt_position = len(self.terminal.toPlainText())
      return
    if self.mode == "SYSTEM" and low.startswith("ssh user "):
      result = self._configure_ssh_user(cmd)
      if result:
        self.append_plain_text(result)
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low == "ssh client first-time enable":
      self.running["ssh_client_first_time_enabled"] = True
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("user-interface vty "):
      match = re.fullmatch(r"user-interface vty\s+(\d+)(?:\s+(\d+))?", cmd, re.I)
      if not match or int(match.group(1)) > 20 or int(match.group(2) or match.group(1)) > 20 or int(match.group(1)) > int(match.group(2) or match.group(1)):
        self.append_plain_text("Error: Use user-interface vty <0-20> [<0-20>].")
        self._prompt()
      else:
        start, end = map(int, (match.group(1), match.group(2) or match.group(1)))
        self.active_vty = f"{start}-{end}"
        self.active_vty_range = (start, end)
        key = f"{start}-{end}"
        self.running.setdefault("vty_interfaces", {}).setdefault(key, {"start": start, "end": end, "authentication_mode": "aaa", "protocols": ["ssh"], "privilege": 3})
        self.mode = "VTY"
        self._persist()
        self._prompt()
      return
    if self.mode == "SYSTEM" and low == "ftp server enable":
      self.running["ftp_server_enabled"] = True
      self._persist()
      self.append_plain_text("Warning: FTP is not a secure protocol, and it is recommended to use SFTP.")
      self._prompt()
      return
    if self.mode == "SYSTEM" and low == "undo ftp server enable":
      self.running["ftp_server_enabled"] = False
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("ftp server source "):
      source = cmd.split(None, 3)[3].strip()
      if source.lower() == "all-interface" or re.fullmatch(r"(?:GE|GigabitEthernet)\s*\d+/\d+/\d+", source, re.I):
        self.pending_ftp_source = source
        self.pending = "ftp_source_confirm"
        self._write("Warning: FTP server source configuration will take effect in the next login. Continue? [Y/N]:")
        self.prompt_position = len(self.terminal.toPlainText())
      else:
        self.append_plain_text("Error: Use ftp server source all-interface or a GE interface.")
      if self.pending != "ftp_source_confirm":
        self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("ftp server acl "):
      number = cmd.split()[-1]
      if number not in self.running.get("acls", {}):
        self.append_plain_text(f"Error: ACL {number} does not exist.")
      else:
        self.running["ftp_server_acl"] = number
        self._persist()
      self._prompt()
      return
    if self.mode == "USER" and low.startswith("ftp "):
      self._start_ftp_client(cmd.split(None, 1)[1].strip())
      return
    if self.mode == "USER" and (low.startswith("telnet ") or low.startswith("stelnet ")):
      self._start_remote_client(cmd)
      return
    if self.mode == "USER" and low.startswith("install feature-software "):
      self.append_plain_text("Info: The package has been installed will be skipped: WEAKEA.")
      self._prompt()
      return
    if low in ("return", "quit", "exit"):
      if self.mode == "VLAN":
        self.mode, self.active_vlan = "SYSTEM", None
      elif self.mode == "INTERFACE":
        self.mode, self.interface = "SYSTEM", None
        self.interface_ports = []
      elif self.mode == "ETH_TRUNK":
        self.mode, self.active_trunk = "SYSTEM", None
      elif self.mode == "DHCP_POOL":
        self.mode, self.active_dhcp_pool = "SYSTEM", None
      elif self.mode == "OSPF_AREA":
        self.mode, self.active_ospf_area = "OSPF", None
      elif self.mode == "OSPF":
        self.mode, self.active_ospf_process = "SYSTEM", None
      elif self.mode in ("ACL", "CLASSIFIER", "BEHAVIOR", "POLICY"):
        self.mode = "SYSTEM"
      elif self.mode == "SYSTEM":
        self.mode = "USER"
      self._prompt()
      return
    if low in ("system-view", "system-view immediately", "sys"):
      self.mode = "SYSTEM"
      self._prompt()
      return
    if low == "display vlan" or low.startswith("display vlan "):
      self._display_vlans(cmd)
      return
    if low == "display users":
      self._display_remote_users()
      return
    if low.startswith("display nat address-group"):
      self._display_nat_address_groups(cmd)
      return
    if low == "display nat server" or low.startswith("display nat server "):
      self._display_nat_servers()
      return
    if low == "display nat-policy" or low.startswith("display nat-policy "):
      self._display_nat_policy()
      return
    if low in ("display session all verbose", "display nat session all verbose"):
      self._display_nat_sessions()
      return
    if low == "display aaa configuration":
      self._display_aaa_configuration()
      return
    if low.startswith("display local-user username "):
      self._display_local_user(cmd.split()[-1])
      return
    if low == "display vty ip-block all":
      self.append_plain_text("The VTY IP address blocking function is not configured.\n")
      self._prompt()
      return
    if low == "display ssh server ip-block all":
      self.append_plain_text("The SSH server IP address blocking function is not configured.\n")
      self._prompt()
      return
    if low in ("display stp", "display stp brief"):
      self._display_stp(brief=low.endswith("brief"))
      return
    if low == "display eth-trunk" or low.startswith("display eth-trunk "):
      self._display_eth_trunk(cmd)
      return
    if low == "display ip routing-table" or low == "display ip routing-table verbose":
      self._display_ip_routing_table()
      return
    if low.startswith("display ospf peer"):
      self._display_ospf_peer(brief=low.endswith("brief"))
      return
    if low.startswith("display ip routing-table protocol ospf"):
      self._display_ip_routing_table(protocol="ospf")
      return
    if low.startswith("display ipv6 interface"):
      self._display_ipv6_interface(cmd)
      return
    if low == "display ipv6 routing-table" or low.startswith("display ipv6 routing-table "):
      self._display_ipv6_routing_table()
      return
    if low.startswith("display ipv6 neighbors"):
      self._display_ipv6_neighbors(cmd)
      return
    if low == "display acl" or low.startswith("display acl "):
      self._display_acl(cmd)
      return
    if low == "display traffic policy" or low.startswith("display traffic policy "):
      self._display_traffic_policy(cmd)
      return
    if low.startswith("display dhcp client interface "):
      self._display_dhcp_client(cmd)
      return
    if low.startswith("display ip interface "):
      self._display_ip_interface(cmd)
      return
    if low.startswith("display ip pool name "):
      self._display_ip_pool(cmd)
      return
    if low.startswith("display ip pool interface "):
      self._display_ip_pool_interface(cmd)
      return
    if low.startswith("display interface ge "):
      self._display_physical_interface(cmd)
      return
    if low == "display mac-address":
      self._display_mac_table()
      return
    if low == "display arp":
      self._display_arp()
      return
    if low.startswith("ping "):
      if low.startswith("ping ipv6 "):
        self._ping_ipv6(cmd)
        return
      self._ping(cmd.split(None, 1)[1])
      return
    if low.startswith("tracert "):
      self._tracert(cmd)
      return
    if self.mode == "USER" and low == "display version":
      if self.is_router:
        self.append_plain_text("Huawei Versatile Routing Platform Software\nVRP (R) software, Version 8.230 (AR6700 V600R025C10SPC100)\nHuawei AR6700 Router uptime is 0 day, 0 hour, 0 minute\nMPU 0: uptime is 0 day, 0 hour, 0 minute\nMemory Size: 4096 M bytes\nFlash 0: 0 M bytes\n")
      else:
        self.append_plain_text("Huawei Versatile Routing Platform Software\nVRP (R) software, Version 8.230 (eNSP-LSW)\n")
      self._prompt()
      return
    if low in ("display current", "dis current", "display current-configuration", "display current-configuration all", "dis cu", "d cu"):
      self._display_config()
      return
    if low in ("display startup", "dis startup"):
      self.append_plain_text(f"Startup system software: flash:/\nNext startup saved-configuration file: {('flash:/' + self.startup_file) if self.startup_file else 'NULL'}\nCurrent startup saved-configuration file: {('flash:/' + self.startup_file) if self.startup_file else 'NULL'}")
      self._prompt()
      return
    if low == "dir" or low.startswith("dir | include "):
      self._refresh_storage()
      filter_match = re.fullmatch(r"dir\s+\|\s+include\s+(\S+)", cmd, re.I)
      file_rows = [(name, cfg) for name, cfg in self.files.items()
                   if not filter_match or filter_match.group(1).lower() in name.lower()]
      self.append_plain_text("Directory of flash:/\n\n  Idx Attr  Size(Byte) Date Time FileName\n" + "\n".join(f"  {i:<3} -rw-  {self._file_byte_count(cfg):<9} 2026-11-09 00:00:00 {name}" for i, (name, cfg) in enumerate(file_rows, 1)) + f"\n\n{len(file_rows)} file(s)")
      self._prompt()
      return
    if low == "save":
      self._refresh_storage()
      self.pending = "confirm_save"
      self._write("Are you sure to save the configuration? (y/n)[n]:")
      self.prompt_position = len(self.terminal.toPlainText())
      return
    if low.startswith("save "):
      self._refresh_storage()
      self.pending_file = cmd.split(None, 1)[1]
      if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}\.cfg", self.pending_file, re.I):
        self.append_plain_text("Error: Invalid file name. Use a name ending in .cfg.")
        self._prompt()
        return
      self.pending = "confirm_file_save"
      self._write(f"Are you sure to save the configuration to flash:/{self.pending_file}? (y/n)[n]:")
      self.prompt_position = len(self.terminal.toPlainText())
      return
    if low == "compare configuration":
      self._refresh_storage()
      if self.saved is None:
        self.append_plain_text("There is no saved configuration.")
      else:
        self.append_plain_text("The current configuration is the same as the saved configuration." if self.saved == self.running else "The current configuration is different from the saved configuration.")
      self._prompt()
      return
    if low.startswith("startup saved-configuration "):
      self._refresh_storage()
      self.pending = "saved_file_name"
      self._pending_input(cmd.split(None, 2)[2])
      return
    if low == "reset saved-configuration":
      self.pending = "confirm_reset"
      self._write("Warning: The action will delete the saved configuration file. Continue? [Y/N]:")
      self.prompt_position = len(self.terminal.toPlainText())
      return
    if low == "reboot":
      self.pending = "reboot_save"
      self._write("Warning: The system will reboot. Continue? [Y/N]:")
      self.prompt_position = len(self.terminal.toPlainText())
      return
    if self.mode == "SYSTEM" and low.startswith("sysname "):
      name = cmd.split(None, 1)[1].strip()
      if not 1 <= len(name) <= 50:
        self.append_plain_text("Error: The system name must contain 1-50 characters.")
      else:
        self.sysname = name
        self.running["sysname"] = name
        self._persist()
        window = self.window()
        if hasattr(window, "update_device_sysname"):
          window.update_device_sysname(self.node_name, name)
      self._prompt()
      return
    if self.mode == "SYSTEM" and (low.startswith("ip route-static ") or low.startswith("undo ip route-static ")):
      result = self._configure_static_route(cmd)
      if result:
        self.append_plain_text(result)
      self._persist()
      self._renew_dhcp_clients()
      self._prompt()
      return
    if self.mode == "SYSTEM" and (low.startswith("ipv6 route-static ") or low.startswith("undo ipv6 route-static ")):
      result = self._configure_ipv6_static_route(cmd)
      if result:
        self.append_plain_text(result)
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and (low.startswith("acl ") or low.startswith("acl number ")):
      match = re.fullmatch(r"acl(?:\s+number)?\s+(\d+)", cmd, re.I)
      if not match or not 2000 <= int(match.group(1)) <= 3999:
        self.append_plain_text("Error: Use acl [number] <2000-3999>.")
      else:
        number = match.group(1)
        acl_type = "basic" if 2000 <= int(number) <= 2999 else "advance"
        self.running.setdefault("acls", {}).setdefault(number, {"type": acl_type, "rules": []})
        self.running["acls"][number].setdefault("type", acl_type)
        self.active_acl, self.mode = number, "ACL"
        self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("traffic classifier "):
      parts = cmd.split()
      if len(parts) not in (3, 5) or (len(parts) == 5 and (parts[3].lower() != "type" or parts[4].lower() not in ("or", "and"))):
        self.append_plain_text("Error: Use traffic classifier <name> [type {or|and}].")
      else:
        name = parts[2]
        self.running.setdefault("traffic_classifiers", {}).setdefault(name, {"type": parts[4].lower() if len(parts) == 5 else "or", "acl": None})
        self.active_classifier, self.mode = name, "CLASSIFIER"
        self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("traffic behavior "):
      parts = cmd.split()
      if len(parts) != 3:
        self.append_plain_text("Error: Use traffic behavior <name>.")
      else:
        name = parts[2]
        self.running.setdefault("traffic_behaviors", {}).setdefault(name, {"action": "permit"})
        self.active_behavior, self.mode = name, "BEHAVIOR"
        self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("traffic policy "):
      parts = cmd.split()
      if len(parts) != 3:
        self.append_plain_text("Error: Use traffic policy <name>.")
      else:
        name = parts[2]
        self.running.setdefault("traffic_policies", {}).setdefault(name, {"classifiers": []})
        self.active_policy, self.mode = name, "POLICY"
        self._persist()
      self._prompt()
      return
    if self.mode == "ACL":
      result = self._configure_acl_view(cmd)
      if result is not None:
        if result:
          self.append_plain_text(result)
        self._persist()
        self._prompt()
        return
    if self.mode == "CLASSIFIER":
      result = self._configure_classifier_view(cmd)
      if result is not None:
        if result:
          self.append_plain_text(result)
        self._persist()
        self._prompt()
        return
    if self.mode == "BEHAVIOR":
      result = self._configure_behavior_view(cmd)
      if result is not None:
        if result:
          self.append_plain_text(result)
        self._persist()
        self._prompt()
        return
    if self.mode == "POLICY":
      result = self._configure_policy_view(cmd)
      if result is not None:
        if result:
          self.append_plain_text(result)
        self._persist()
        self._prompt()
        return
    if self.mode == "SYSTEM" and re.fullmatch(r"ospf(?:\s+\d+)?(?:\s+router-id\s+\S+)?(?:\s+vpn-instance\s+\S+)?", cmd, re.I):
      import ipaddress
      match = re.fullmatch(r"ospf(?:\s+(\d+))?(?:\s+router-id\s+(\S+))?(?:\s+vpn-instance\s+(\S+))?", cmd, re.I)
      process_id, router_id, vpn_instance = match.groups()
      process_id = process_id or "1"
      if router_id:
        try:
          router_id = str(ipaddress.IPv4Address(router_id))
        except ipaddress.AddressValueError:
          self.append_plain_text("Error: Enter a valid IPv4 router ID.")
          self._prompt()
          return
      process = self.running.setdefault("ospf_processes", {}).setdefault(process_id, {"router_id": "", "areas": {}, "default_route_advertise": {"enabled": False, "always": False}})
      if router_id:
        if process.get("areas") and process.get("router_id") != router_id:
          self.pending_ospf_router_id = (process_id, router_id)
          self._write("Warning: The operation will cause reset OSPF. Continue? [Y/N] :")
          self.pending, self.prompt_position = "ospf_routerid_reset", len(self.terminal.toPlainText())
          return
        process["router_id"] = router_id
      if vpn_instance:
        if vpn_instance not in self.running.get("vpn_instances", {}):
          self.append_plain_text(f"Error: VPN instance {vpn_instance} does not exist."); self._prompt(); return
        process["vpn_instance"] = vpn_instance
      self.active_ospf_process, self.mode = process_id, "OSPF"
      self._persist()
      self._prompt()
      return
    if self.mode == "OSPF":
      if low.startswith("area "):
        area_id = cmd.split(None, 1)[1].strip()
        try:
          int(area_id, 0)
        except ValueError:
          self.append_plain_text("Error: Enter a numeric OSPF area ID.")
          self._prompt()
          return
        area = self.running["ospf_processes"][self.active_ospf_process].setdefault("areas", {}).setdefault(area_id, {"networks": [], "authentication": None})
        self.active_ospf_area, self.mode = area_id, "OSPF_AREA"
        self._persist()
        self._prompt()
        return
      if low.startswith("default-route-advertise"):
        parts = low.split()
        if len(parts) > 2 or (len(parts) == 2 and parts[1] != "always"):
          self.append_plain_text("Error: Use default-route-advertise [always].")
        else:
          self.running["ospf_processes"][self.active_ospf_process]["default_route_advertise"] = {"enabled": True, "always": len(parts) == 2}
          self._persist()
        self._prompt()
        return
      if low.startswith("import-route bgp"):
        match = re.fullmatch(r"import-route\s+bgp(?:\s+cost\s+(\d+))?(?:\s+type\s+(1|2))?", cmd, re.I)
        if not match:
          self.append_plain_text("Error: Use import-route bgp [cost <0-65535>] [type {1|2}].")
        else:
          cost, route_type = match.groups()
          self.running["ospf_processes"][self.active_ospf_process]["import_bgp"] = {"cost": int(cost or 0), "type": int(route_type or 2)}
          self._persist()
        self._prompt(); return
    if self.mode == "OSPF_AREA":
      if low.startswith("network "):
        parts = cmd.split()
        if len(parts) != 3:
          self.append_plain_text("Error: Use network <network-address> <wildcard-mask>.")
        else:
          try:
            import ipaddress
            network, wildcard = str(ipaddress.IPv4Address(parts[1])), str(ipaddress.IPv4Address(parts[2]))
            area = self.running["ospf_processes"][self.active_ospf_process]["areas"][self.active_ospf_area]
            entry = {"network": network, "wildcard": wildcard}
            if entry not in area["networks"]:
              area["networks"].append(entry)
            self._persist()
          except ipaddress.AddressValueError:
            self.append_plain_text("Error: Enter a valid network address and wildcard mask.")
        self._prompt()
        return
      if low.startswith("authentication-mode "):
        result = self._parse_ospf_auth(cmd)
        if isinstance(result, str):
          self.append_plain_text(result)
        else:
          self.running["ospf_processes"][self.active_ospf_process]["areas"][self.active_ospf_area]["authentication"] = result
          self._persist()
        self._prompt()
        return
      if low == "undo authentication-mode":
        self.running["ospf_processes"][self.active_ospf_process]["areas"][self.active_ospf_area]["authentication"] = None
        self._persist()
        self._prompt()
        return
    if self.mode == "SYSTEM" and re.fullmatch(r"interface\s+loopback\s*\d+", cmd, re.I):
      number = cmd.split()[-1]
      name = f"LoopBack{number}"
      self.running.setdefault("interfaces", {}).setdefault(name, {"ip": "", "mask": "", "description": "", "shutdown": False,
          "link_type": "route", "is_loopback": True, "ipv6_enabled": False, "ipv6_addresses": [],
          "ipv6_link_local_auto": False, "ipv6_auto_global": False, "ipv6_auto_global_default": False,
          "ipv6_ra_halt": True, "ipv6_ra_prefix": None, "ospf_cost": 1, "ospf_auth": None})
      self.interface, self.interface_ports, self.mode = name, [name], "INTERFACE"
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and re.fullmatch(r"interface\s+tunnel\s*\d+", cmd, re.I):
      number = re.search(r"\d+", cmd).group()
      name = f"Tunnel{number}"
      self.running.setdefault("interfaces", {}).setdefault(name, {
          "ip": "", "mask": "", "description": "", "shutdown": False,
          "link_type": "route", "is_tunnel": True, "sr_te": False,
          "destination": "", "explicit_path": "", "tunnel_id": int(number),
          "unnumbered_interface": "", "ospf_enabled": False, "isis_process": None,
      })
      self.interface, self.interface_ports, self.mode = name, [name], "INTERFACE"
      self._persist(); self._prompt(); return
    if self.mode == "SYSTEM" and low == "dhcp enable":
      self.running["dhcp_enabled"] = True
      self._persist()
      self.append_plain_text("Info: The DHCP function is enabled.")
      self._renew_dhcp_clients()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("ip pool "):
      name = cmd.split(None, 2)[2].strip()
      if not self.running.get("dhcp_enabled"):
        self.append_plain_text("Error: Run dhcp enable before creating a DHCP address pool.")
      elif not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
        self.append_plain_text("Error: Invalid DHCP pool name.")
      else:
        self.running.setdefault("dhcp_pools", {}).setdefault(name, {"network": "", "mask": "", "gateway": "", "dns": "", "lease_seconds": 86400, "bindings": {}, "leases": {}})
        self.active_dhcp_pool, self.mode = name, "DHCP_POOL"
        self._persist()
      self._prompt()
      return
    if self.mode == "DHCP_POOL":
      changed = self._configure_dhcp_pool(cmd)
      if changed is not None:
        if changed:
          self.append_plain_text(changed)
        self._persist()
        self._renew_dhcp_clients()
        self._prompt()
        return
    if self.mode == "SYSTEM" and re.fullmatch(r"lacp\s+priority\s+\d+", low):
      priority = int(low.split()[-1])
      if not 0 <= priority <= 65535:
        self.append_plain_text("Error: LACP system priority must be in the range 0-65535.")
      else:
        self.running["lacp_priority"] = priority
        self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and re.fullmatch(r"interface\s+eth-trunk\s+\d+", low):
      trunk_id = int(low.split()[-1])
      if not 1 <= trunk_id <= 128:
        self.append_plain_text("Error: Eth-Trunk ID must be in the range 1-128.")
      else:
        key = str(trunk_id)
        self.running.setdefault("eth_trunks", {}).setdefault(key, {
            "mode": "manual", "max_active": 128, "least_active": 1, "preempt": False,
            "load_balance": "src-dst-ip", "link_type": "access", "pvid": 1,
            "allowed_vlans": [1], "tagged_vlans": [], "untagged_vlans": [1],
            "stp_enabled": True, "stp_cost": 20000, "stp_port_priority": 128, "stp_edge": False,
        })
        self.active_trunk, self.mode = key, "ETH_TRUNK"
        self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("stp "):
      message = self._configure_stp_global(cmd)
      if message:
        self.append_plain_text(message)
      self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("vlan batch "):
      try:
        vlan_ids = self._parse_vlan_list(cmd.split(None, 2)[2])
        if not vlan_ids or any(v < 1 or v > 4094 for v in vlan_ids):
          raise ValueError
        self.running["vlans"] = sorted(set(self.running.get("vlans", [1])) | set(vlan_ids))
        self._persist()
        self.append_plain_text("VLANs " + ", ".join(map(str, vlan_ids)) + " are created successfully.")
      except ValueError:
        self.append_plain_text("Error: Use vlan batch <vlan-id> [to <vlan-id>]. Valid range: 1-4094.")
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("undo vlan batch "):
      try:
        vlan_ids = self._parse_vlan_list(cmd.split(None, 3)[3])
        self.running["vlans"] = [v for v in self.running.get("vlans", [1]) if v not in vlan_ids or v == 1]
        self._persist()
      except (ValueError, IndexError):
        self.append_plain_text("Error: Invalid VLAN list.")
      self._prompt()
      return
    if self.mode == "SYSTEM" and re.fullmatch(r"vlan\s+\d+", low):
      vlan_id = int(low.split()[1])
      if not 1 <= vlan_id <= 4094:
        self.append_plain_text("Error: VLAN ID range is 1-4094.")
      else:
        self.running["vlans"] = sorted(set(self.running.get("vlans", [1])) | {vlan_id})
        self.active_vlan, self.mode = vlan_id, "VLAN"
        self._persist()
      self._prompt()
      return
    if self.mode == "SYSTEM" and low.startswith("interface range "):
      match = re.fullmatch(
          r"interface\s+range\s+GE\s*(\d+)/(\d+)/(\d+)\s+to\s+(?:GE\s*)?(\d+)/(\d+)/(\d+)",
          cmd, re.I,
      )
      if not match:
        self.append_plain_text("Error: Use interface range GE 1/0/5 to 1/0/10.")
      else:
        frame1, slot1, first, frame2, slot2, last = map(int, match.groups())
        if (frame1, slot1) != (frame2, slot2) or last < first:
          self.append_plain_text("Error: The interface range must be on the same board and in ascending order.")
        else:
          ports = [f"GE{frame1}/{slot1}/{port}" for port in range(first, last + 1)]
          if not self.is_switch or any(port not in self.running["interfaces"] for port in ports):
            self.append_plain_text("Error: One or more interfaces in the range do not exist.")
          else:
            self.interface_ports = ports
            self.interface = "port-group"
            self.mode = "INTERFACE"
      self._prompt()
      return
    if self.mode == "SYSTEM" and re.fullmatch(r"interface\s+(?:ge\s*|gigabitethernet\s*)\d+/\d+/\d+(?:\.\d+)?", cmd, re.I):
      raw = re.sub(r"^interface\s+(?:ge|gigabitethernet)\s*", "", cmd, flags=re.I).replace(" ", "")
      candidate = "GE" + raw
      sub_match = re.fullmatch(r"(GE\d+/\d+/\d+)\.(\d+)", candidate, re.I)
      if sub_match:
        parent, sub_id = sub_match.groups()
        if parent not in self.running["interfaces"]:
          self.append_plain_text("Error: The parent interface does not exist.")
          self._prompt(); return
        self.running["interfaces"].setdefault(candidate, {
            "ip": "", "mask": "", "description": "", "shutdown": False, "link_type": "route",
            "parent": parent, "subinterface": True, "vlan_type": "", "vlan_id": None,
            "vpn_instance": "", "mpls_enabled": False, "mpls_ldp_enabled": False,
            "ospf_enabled": False, "ospf_process": "1", "ospf_area": "0", "ospf_cost": 1,
            "isis_process": None, "isis_circuit_type": "broadcast"})
      if candidate not in self.running["interfaces"]:
        self.append_plain_text("Error: The interface does not exist.")
      else:
        self.interface = candidate
        self.interface_ports = [candidate]
        self.mode = "INTERFACE"
      self._prompt()
      return
    if self.mode == "SYSTEM" and re.fullmatch(r"interface\s+vlanif\s+\d+", cmd, re.I):
      vlan_id = int(cmd.split()[-1])
      if not self.is_switch:
        self.append_plain_text("Error: VLANIF interfaces are available only on switches.")
      elif vlan_id not in set(map(int, self.running.get("vlans", [1]))):
        self.append_plain_text(f"Error: Create VLAN {vlan_id} before configuring its VLANIF interface.")
      else:
        name = f"Vlanif{vlan_id}"
        self.running.setdefault("interfaces", {}).setdefault(name, {
            "ip": "", "mask": "", "description": "", "shutdown": False, "link_type": "route",
            "is_vlanif": True, "vlan_id": vlan_id, "ipv6_enabled": False, "ipv6_addresses": [],
            "ipv6_link_local_auto": False, "ipv6_auto_global": False, "ipv6_auto_global_default": False,
            "ipv6_ra_halt": True, "ipv6_ra_prefix": None, "traffic_policies": {}, "dhcp_mode": "",
            "dhcp_select": "", "dhcp_relay_server_ip": "", "dhcp_server_gateway": "",
            "dhcp_server_dns": "", "dhcp_option_43": "", "dhcp_leases": {}, "dhcp_bindings": {},
            "ospf_cost": 1, "ospf_auth": None,
        })
        self.interface, self.interface_ports, self.mode = name, [name], "INTERFACE"
        self._persist()
      self._prompt()
      return
    if self.mode == "ETH_TRUNK":
      result = self._configure_eth_trunk(cmd)
      if result is not None:
        if result:
          self.append_plain_text(result)
        self._persist()
        self._prompt()
        return
    if self.mode == "INTERFACE":
      active_ports = self.interface_ports or [self.interface]
      config = self.running["interfaces"][active_ports[0]]
      if config.get("subinterface"):
        match = re.fullmatch(r"vlan-type\s+dot1q\s+(\d+)", cmd, re.I)
        if match:
          vlan_id = int(match.group(1))
          if not 1 <= vlan_id <= 4094:
            self.append_plain_text("Error: VLAN ID must be from 1 to 4094.")
          else:
            config["vlan_type"], config["vlan_id"] = "dot1q", vlan_id; self._persist()
          self._prompt(); return
        match = re.fullmatch(r"ip\s+binding\s+vpn-instance\s+(\S+)", cmd, re.I)
        if match:
          name = match.group(1)
          if name not in self.running.get("vpn_instances", {}): self.append_plain_text(f"Error: VPN instance {name} does not exist.")
          else:
            config["vpn_instance"] = name
            vpn = self.running["vpn_instances"][name]
            if self.interface not in vpn["interfaces"]: vpn["interfaces"].append(self.interface)
            self._persist()
          self._prompt(); return
        if low in ("mpls", "mpls ldp"):
          if not self.running.get("mpls_enabled"):
            self.append_plain_text("Error: Enable MPLS globally first.")
          else:
            config["mpls_enabled"] = True
            if low == "mpls ldp":
              if not self.running.get("mpls_ldp_enabled"): self.append_plain_text("Error: Enable MPLS LDP globally first.")
              else: config["mpls_ldp_enabled"] = True
            self._persist()
          self._prompt(); return
        if low == "undo mpls":
          config["mpls_enabled"] = config["mpls_ldp_enabled"] = False; self._persist(); self._prompt(); return
      if low in ("mpls", "mpls ldp"):
        if not self.running.get("mpls_enabled"):
          self.append_plain_text("Error: Enable MPLS globally first.")
        else:
          for port in active_ports:
            cfg = self.running["interfaces"][port]; cfg["mpls_enabled"] = True
            if low == "mpls ldp":
              if not self.running.get("mpls_ldp_enabled"): self.append_plain_text("Error: Enable MPLS LDP globally first.")
              else: cfg["mpls_ldp_enabled"] = True
          self._persist()
        self._prompt(); return
      if low == "undo mpls":
        for port in active_ports:
          self.running["interfaces"][port]["mpls_enabled"] = False
          self.running["interfaces"][port]["mpls_ldp_enabled"] = False
        self._persist(); self._prompt(); return
      if low in ("nat enable", "undo nat enable"):
        if not self.is_router:
          self.append_plain_text("Error: NAT is available only on router interfaces.")
        else:
          for port in active_ports:
            self.running["interfaces"][port]["nat_enabled"] = low == "nat enable"
          self._persist()
        self._prompt()
        return
      policy_match = re.fullmatch(r"traffic-policy\s+(\S+)\s+(inbound|outbound)", cmd, re.I)
      undo_policy_match = re.fullmatch(r"undo\s+traffic-policy\s+(inbound|outbound)", cmd, re.I)
      if policy_match:
        policy_name, direction = policy_match.group(1), policy_match.group(2).lower()
        if policy_name not in self.running.get("traffic_policies", {}):
          self.append_plain_text(f"Error: Traffic policy {policy_name} does not exist.")
        else:
          for port in active_ports:
            self.running["interfaces"][port].setdefault("traffic_policies", {})[direction] = policy_name
          self._persist()
        self._prompt()
        return
      if undo_policy_match:
        direction = undo_policy_match.group(1).lower()
        for port in active_ports:
          self.running["interfaces"][port].setdefault("traffic_policies", {}).pop(direction, None)
        self._persist()
        self._prompt()
        return
      if low == "ipv6 enable":
        for port in active_ports:
          self.running["interfaces"][port]["ipv6_enabled"] = True
        self._persist()
        self._prompt()
        return
      if low == "undo ipv6 enable":
        for port in active_ports:
          self.running["interfaces"][port]["ipv6_enabled"] = False
          self.running["interfaces"][port]["ipv6_addresses"] = []
          self.running["interfaces"][port]["ipv6_auto_global"] = False
          self.running["interfaces"][port]["ipv6_auto_global_default"] = False
          self.running["interfaces"][port]["ipv6_link_local_auto"] = False
        self._persist()
        self._prompt()
        return
      if low == "ipv6 address auto link-local":
        if not config.get("ipv6_enabled"):
          self.append_plain_text("Error: Run ipv6 enable first.")
        else:
          for port in active_ports:
            self.running["interfaces"][port]["ipv6_link_local_auto"] = True
          self._persist()
        self._prompt()
        return
      if low in ("ipv6 address auto global", "ipv6 address auto global default"):
        if not config.get("ipv6_enabled"):
          self.append_plain_text("Error: Run ipv6 enable first.")
        else:
          for port in active_ports:
            self.running["interfaces"][port]["ipv6_auto_global"] = True
            self.running["interfaces"][port]["ipv6_auto_global_default"] = low.endswith(" default")
            self.running["interfaces"][port]["ipv6_link_local_auto"] = True
          self._persist()
        self._prompt()
        return
      if low.startswith("ipv6 address "):
        if not config.get("ipv6_enabled"):
          self.append_plain_text("Error: Run ipv6 enable first.")
        else:
          result = self._configure_ipv6_interface_address(cmd, active_ports)
          if result:
            self.append_plain_text(result)
          self._persist()
        self._prompt()
        return
      if low == "undo ipv6 address auto global":
        for port in active_ports:
          self.running["interfaces"][port]["ipv6_auto_global"] = False
          self.running["interfaces"][port]["ipv6_auto_global_default"] = False
        self._persist()
        self._prompt()
        return
      if low.startswith("ipv6 nd ra halt "):
        setting = low.split()[-1]
        if setting != "disable":
          self.append_plain_text("Error: Use ipv6 nd ra halt disable to enable Router Advertisements.")
        else:
          for port in active_ports:
            self.running["interfaces"][port]["ipv6_ra_halt"] = False
          self._persist()
        self._prompt()
        return
      if low.startswith("undo ipv6 nd ra halt"):
        for port in active_ports:
          self.running["interfaces"][port]["ipv6_ra_halt"] = True
        self._persist()
        self._prompt()
        return
      if low.startswith("ipv6 nd ra prefix "):
        result = self._configure_ipv6_ra_prefix(cmd)
        if isinstance(result, str):
          self.append_plain_text(result)
        else:
          for port in active_ports:
            self.running["interfaces"][port]["ipv6_ra_prefix"] = result
          self._persist()
        self._prompt()
        return
      if low in ("shutdown", "undo shutdown"):
        for port in active_ports:
          self.running["interfaces"][port]["shutdown"] = low == "shutdown"
        self._persist()
        self._prompt()
        return
      if low in ("undo portswitch", "portswitch"):
        link_type = "route" if low == "undo portswitch" else "access"
        for port in active_ports:
          self.running["interfaces"][port]["link_type"] = link_type
        self._persist()
        self._prompt()
        return
      if low == "undo port link-type" and self.is_switch:
        # Real CloudEngine lab devices require this before changing the
        # interface to routed mode with `undo portswitch`.
        for port in active_ports:
          self.running["interfaces"][port]["port_link_type_removed"] = True
        self._persist()
        self._prompt()
        return
      if low.startswith("dhcp select "):
        selection = low.split()[-1]
        if selection not in ("global", "relay", "interface"):
          self.append_plain_text("Error: Use dhcp select {global|interface|relay}.")
        elif not self.running.get("dhcp_enabled"):
          self.append_plain_text("Error: Run dhcp enable before configuring DHCP server or relay functions.")
        elif config.get("link_type") != "route":
          self.append_plain_text(f"Error: Convert {self.interface} to a Layer 3 port with undo portswitch first.")
        else:
          for port in active_ports:
            self.running["interfaces"][port]["dhcp_select"] = selection
            if selection != "relay":
              self.running["interfaces"][port].pop("dhcp_relay_server_ip", None)
          self._persist()
          self._renew_dhcp_clients()
        self._prompt()
        return
      if low.startswith("dhcp server gateway-list "):
        try:
          gateway = str(ipaddress.IPv4Address(cmd.split()[-1]))
          if config.get("dhcp_select") != "interface":
            raise RuntimeError("select")
          network = ipaddress.IPv4Network(f"{config.get('ip')}/{config.get('mask') or '255.255.255.0'}", strict=False)
          if ipaddress.IPv4Address(gateway) not in network:
            raise RuntimeError("network")
          for port in active_ports:
            self.running["interfaces"][port]["dhcp_server_gateway"] = gateway
          self._persist()
          self._renew_dhcp_clients()
        except ipaddress.AddressValueError:
          self.append_plain_text("Error: Enter a valid DHCP gateway IPv4 address.")
        except RuntimeError as exc:
          self.append_plain_text("Error: Configure dhcp select interface first." if str(exc) == "select" else "Error: The DHCP gateway must be in the VLANIF subnet.")
        self._prompt()
        return
      option43 = re.fullmatch(r"dhcp server option 43 sub-option 2 ip-address (\S+)", cmd, re.I)
      if option43:
        try:
          address = str(ipaddress.IPv4Address(option43.group(1)))
          if config.get("dhcp_select") != "interface":
            raise RuntimeError("select")
          for port in active_ports:
            self.running["interfaces"][port]["dhcp_option_43"] = address
          self._persist()
        except ipaddress.AddressValueError:
          self.append_plain_text("Error: Enter a valid IPv4 address for DHCP option 43.")
        except RuntimeError:
          self.append_plain_text("Error: Configure dhcp select interface first.")
        self._prompt()
        return
      if low.startswith("dhcp relay server-ip "):
        try:
          import ipaddress
          server_ip = str(ipaddress.IPv4Address(cmd.split()[-1]))
          for port in active_ports:
            if self.running["interfaces"][port].get("dhcp_select") != "relay":
              self.append_plain_text("Error: Configure dhcp select relay first.")
              self._prompt()
              return
            if self.running["interfaces"][port].get("link_type") != "route":
              self.append_plain_text(f"Error: Convert {port} to a Layer 3 port with undo portswitch first.")
              self._prompt()
              return
            self.running["interfaces"][port]["dhcp_relay_server_ip"] = server_ip
          self._persist()
          self._renew_dhcp_clients()
        except ipaddress.AddressValueError:
          self.append_plain_text("Error: Enter a valid DHCP server IPv4 address.")
        self._prompt()
        return
      if low.startswith("ospf cost "):
        parts = low.split()
        try:
          if len(parts) != 3 or not 1 <= int(parts[2]) <= 65535:
            raise ValueError
          for port in active_ports:
            self.running["interfaces"][port]["ospf_cost"] = int(parts[2])
          self._persist()
        except ValueError:
          self.append_plain_text("Error: Use ospf cost <1-65535>.")
        self._prompt()
        return
      if low.startswith("ospf authentication-mode "):
        result = self._parse_ospf_auth(cmd[len("ospf "):])
        if isinstance(result, str):
          self.append_plain_text(result)
        else:
          for port in active_ports:
            self.running["interfaces"][port]["ospf_auth"] = result
          self._persist()
        self._prompt()
        return
      if low == "undo ospf authentication-mode":
        for port in active_ports:
          self.running["interfaces"][port]["ospf_auth"] = None
        self._persist()
        self._prompt()
        return
      if low == "undo ospf cost":
        for port in active_ports:
          self.running["interfaces"][port]["ospf_cost"] = 1
        self._persist()
        self._prompt()
        return
      if low == "ip address dhcp-alloc":
        if config.get("link_type") != "route":
          self.append_plain_text(f"Error: Interface {self.interface} is a Layer 2 port. Run 'undo portswitch' before enabling the DHCP client.")
          self._prompt()
          return
        from modules.datacom_dhcp import acquire_dhcp_client
        conn = get_connection()
        try:
          success, message = acquire_dhcp_client(conn, self.username, self.node_name, active_ports[0])
        finally:
          conn.close()
        if message:
          self.append_plain_text(message)
        self._load_running_after_dhcp()
        self._prompt()
        return
      if self.is_switch:
        if low.startswith("eth-trunk ") or low == "undo eth-trunk":
          result = self._set_eth_trunk(cmd, active_ports)
          if result:
            self.append_plain_text(result)
          self._persist()
          self._prompt()
          return
        if low.startswith("lacp priority "):
          result = self._set_lacp_port_priority(cmd, active_ports)
          if result:
            self.append_plain_text(result)
          self._persist()
          self._prompt()
          return
        if low.startswith("stp "):
          result = self._configure_stp_port(cmd, active_ports)
          if result:
            self.append_plain_text(result)
          self._persist()
          self._prompt()
          return
        if low == "undo portswitch":
          for port in active_ports:
            self.running["interfaces"][port]["link_type"] = "route"
          self._persist()
          self._prompt()
          return
        result = None
        for port in active_ports:
          result = self._configure_switch_port(self.running["interfaces"][port], cmd)
          if result is not None:
            break
        if result is not None:
          if result == "":
            # Apply valid VLAN port commands to every member of a temporary group.
            for port in active_ports[1:]:
              self._configure_switch_port(self.running["interfaces"][port], cmd)
          if result:
            self.append_plain_text(result)
          self._persist()
          self._prompt()
          return
      if low == "undo portswitch":
        self._prompt()
        return
      if low.startswith("ip address ") and config.get("link_type") != "route":
        self.append_plain_text(
            f"Error: Interface {self.interface} is a Layer 2 port. "
            "Run 'undo portswitch' to enable Layer 3 mode before configuring an IP address."
        )
        self._prompt()
        return
      if low.startswith("ip address ") and config.get("link_type") == "route":
        parts = cmd.split()
        try:
          import ipaddress
          ip = ipaddress.IPv4Address(parts[2])
          mask = parts[3]
          mask = str(ipaddress.IPv4Network(f"0.0.0.0/{mask}").netmask)
          config["ip"], config["mask"] = str(ip), mask
          config["dhcp_mode"] = ""
          config.pop("dhcp_lease", None)
          config.pop("dhcp_gateway", None)
          config.pop("dhcp_dns", None)
          for port in active_ports[1:]:
            self.running["interfaces"][port]["ip"] = str(ip)
            self.running["interfaces"][port]["mask"] = mask
          self._persist()
        except (ValueError, IndexError):
          self.append_plain_text("Error: Invalid IP address or mask.")
        self._prompt()
        return
      if low == "undo ip address":
        for port in active_ports:
          self.running["interfaces"][port]["ip"] = ""
          self.running["interfaces"][port]["mask"] = ""
          self.running["interfaces"][port]["dhcp_mode"] = ""
          self.running["interfaces"][port].pop("dhcp_lease", None)
          self.running["interfaces"][port].pop("dhcp_gateway", None)
          self.running["interfaces"][port].pop("dhcp_dns", None)
        self._persist()
        self._prompt()
        return
      if low in ("display this", "dis this"):
        self._display_interface()
        return
    self.append_plain_text("Error: Unrecognized command.")
    self._prompt()

  def _configure_stp_global(self, command):
    if not self.is_switch:
      return "Error: STP commands are available only on a Layer 2 switch."
    low = command.lower().strip()
    stp = self.running.setdefault("stp", {"enabled": True, "mode": "mstp", "priority": 32768, "root_role": ""})
    if low == "stp enable":
      stp["enabled"] = True
      return ""
    if low == "stp disable":
      stp["enabled"] = False
      return ""
    match = re.fullmatch(r"stp\s+mode\s+(stp|rstp|mstp)", low)
    if match:
      stp["mode"] = match.group(1)
      return f"The spanning tree working mode of the current device has been changed to {match.group(1).upper()}."
    match = re.fullmatch(r"stp(?:\s+instance\s+0)?\s+root\s+(primary|secondary)", low)
    if match:
      role = match.group(1)
      stp["root_role"] = role
      stp["priority"] = 0 if role == "primary" else 4096
      return ""
    match = re.fullmatch(r"stp(?:\s+instance\s+0)?\s+priority\s+(\d+)", low)
    if match:
      priority = int(match.group(1))
      if not 0 <= priority <= 61440 or priority % 4096:
        return "Error: STP priority must be 0-61440 in increments of 4096."
      if stp.get("root_role"):
        return "Error: Priority cannot be changed while this switch is configured as a root bridge."
      stp["priority"] = priority
      return ""
    return "Error: Use stp {enable|disable|mode {stp|rstp|mstp}|root {primary|secondary}|priority <0-61440>}."

  def _configure_stp_port(self, command, ports):
    low = command.lower().strip()
    if low in ("stp enable", "stp disable"):
      enabled = low.endswith("enable")
      for port in ports:
        self.running["interfaces"][port]["stp_enabled"] = enabled
      return ""
    match = re.fullmatch(r"stp(?:\s+instance\s+0)?\s+cost\s+(\d+)", low)
    if match:
      cost = int(match.group(1))
      if not 1 <= cost <= 200000000:
        return "Error: STP port cost must be in the range 1-200000000."
      for port in ports:
        self.running["interfaces"][port]["stp_cost"] = cost
      return ""
    match = re.fullmatch(r"stp\s+port\s+priority\s+(\d+)", low)
    if match:
      priority = int(match.group(1))
      if not 0 <= priority <= 240 or priority % 16:
        return "Error: STP port priority must be 0-240 in increments of 16."
      for port in ports:
        self.running["interfaces"][port]["stp_port_priority"] = priority
      return ""
    if low in ("stp edged-port enable", "stp edged-port disable"):
      edge = low.endswith("enable")
      for port in ports:
        self.running["interfaces"][port]["stp_edge"] = edge
      return ""
    return "Error: Use stp {enable|disable|cost <value>|port priority <value>|edged-port {enable|disable}}."

  def _set_eth_trunk(self, command, ports):
    match = re.fullmatch(r"eth-trunk\s+(\d+)", command.strip(), re.I)
    removing = command.strip().lower() == "undo eth-trunk"
    if not match and not removing:
      return "Error: Use eth-trunk <1-128> or undo eth-trunk."
    trunk_id = match.group(1) if match else None
    if trunk_id is not None and trunk_id not in self.running.get("eth_trunks", {}):
      return f"Error: Eth-Trunk {trunk_id} does not exist. Create it first with interface Eth-Trunk {trunk_id}."
    for port in ports:
      current = self.running["interfaces"][port].get("eth_trunk")
      if trunk_id is not None and current is not None and str(current) != trunk_id:
        return f"Error: Interface {port} already belongs to Eth-Trunk {current}."
    for port in ports:
      self.running["interfaces"][port]["eth_trunk"] = None if removing else trunk_id
    return ""

  def _set_lacp_port_priority(self, command, ports):
    match = re.fullmatch(r"lacp\s+priority\s+(\d+)", command.strip(), re.I)
    if not match:
      return "Error: Use lacp priority <0-65535>."
    value = int(match.group(1))
    if value > 65535:
      return "Error: LACP port priority must be in the range 0-65535."
    for port in ports:
      self.running["interfaces"][port]["lacp_priority"] = value
    return ""

  def _configure_eth_trunk(self, command):
    low = command.strip().lower()
    cfg = self.running["eth_trunks"][self.active_trunk]
    if low.startswith("mode "):
      match = re.fullmatch(r"mode\s+(manual\s+load-balance|lacp-static)", low)
      if not match:
        return "Error: Use mode manual load-balance or mode lacp-static."
      members = [p for p, info in self.running["interfaces"].items() if str(info.get("eth_trunk")) == self.active_trunk]
      if members and cfg.get("mode") != ("manual" if match.group(1).startswith("manual") else "lacp-static"):
        return "Error: Remove all member interfaces before changing the Eth-Trunk mode."
      cfg["mode"] = "manual" if match.group(1).startswith("manual") else "lacp-static"
      return ""
    match = re.fullmatch(r"lacp\s+max\s+active-linknumber\s+(\d+)", low)
    if match:
      value = int(match.group(1))
      if not 1 <= value <= 128 or value < int(cfg.get("least_active", 1)):
        return "Error: Maximum active links must be 1-128 and no less than the least active-link number."
      cfg["max_active"] = value
      return ""
    match = re.fullmatch(r"least\s+active-linknumber\s+(\d+)", low)
    if match:
      value = int(match.group(1))
      if not 1 <= value <= int(cfg.get("max_active", 128)):
        return "Error: Least active-link number must be 1 and no greater than the maximum active-link number."
      cfg["least_active"] = value
      return ""
    match = re.fullmatch(r"lacp\s+preempt\s+(enable|disable)", low)
    if match:
      cfg["preempt"] = match.group(1) == "enable"
      return ""
    match = re.fullmatch(r"load-balance\s+(dst-ip|src-ip|src-dst-ip|dst-mac|src-mac|src-dst-mac|round-robin|enhanced)", low)
    if match:
      cfg["load_balance"] = match.group(1)
      return ""
    if low.startswith("trunkport ") or low.startswith("undo trunkport "):
      removing = low.startswith("undo ")
      expression = command.strip()[len("undo ") if removing else 0:]
      match = re.fullmatch(r"trunkport\s+GE\s*(\d+/\d+/\d+)(?:\s+to\s+(?:GE\s*)?(\d+/\d+/\d+))?", expression, re.I)
      if not match:
        return "Error: Use trunkport GE 1/0/1 [to GE 1/0/4]."
      first = "GE" + match.group(1)
      last = "GE" + (match.group(2) or match.group(1))
      f1, s1, p1 = map(int, match.group(1).split("/"))
      f2, s2, p2 = map(int, (match.group(2) or match.group(1)).split("/"))
      if (f1, s1) != (f2, s2) or p2 < p1:
        return "Error: The trunkport range must be on the same board and in ascending order."
      ports = [f"GE{f1}/{s1}/{i}" for i in range(p1, p2 + 1)]
      if any(port not in self.running["interfaces"] for port in ports):
        return "Error: One or more interfaces do not exist."
      if not removing:
        for port in ports:
          current = self.running["interfaces"][port].get("eth_trunk")
          if current not in (None, self.active_trunk):
            return f"Error: Interface {port} already belongs to Eth-Trunk {current}."
      else:
        for port in ports:
          if str(self.running["interfaces"][port].get("eth_trunk")) != self.active_trunk:
            return f"Error: Interface {port} is not a member of Eth-Trunk {self.active_trunk}."
      for port in ports:
        self.running["interfaces"][port]["eth_trunk"] = None if removing else self.active_trunk
      return ""
    result = self._configure_switch_port(cfg, command)
    if result is not None:
      return result
    return None

  def _display_eth_trunk(self, command):
    parts = command.split()
    requested = parts[2] if len(parts) > 2 and parts[2].isdigit() else None
    try:
      from modules.datacom_network import datacom_topology_links
      conn = get_connection()
      try:
        _links, info = datacom_topology_links(conn, self.username)
      finally:
        conn.close()
    except Exception:
      info = {"member_states": {}, "groups": {}}
    lines = []
    for trunk_id, cfg in sorted(self.running.get("eth_trunks", {}).items(), key=lambda item: int(item[0])):
      if requested and trunk_id != requested:
        continue
      members = [port for port, iface in self.running["interfaces"].items() if str(iface.get("eth_trunk")) == trunk_id]
      selected = sum(info.get("member_states", {}).get((self.node_name, port)) == "Selected" for port in members)
      mode = "Static" if cfg.get("mode") == "manual" else "Dynamic"
      lines.extend([f"Eth-Trunk{trunk_id}'s state information is:",
          f"  WorkingMode: {mode:<8} Hash arithmetic: {cfg.get('load_balance', 'src-dst-ip')}",
          f"  System Priority: {self.running.get('lacp_priority', 32768)}  Max Active-linknumber: {cfg.get('max_active', 128)}",
          f"  Least Active-linknumber: {cfg.get('least_active', 1)}  Preempt: {'Enable' if cfg.get('preempt') else 'Disable'}",
          f"  Operating status: {'UP' if selected >= int(cfg.get('least_active', 1)) else 'DOWN'}  Number Of Up Port In Trunk: {selected}",
          "  PortName                      Status       Priority"])
      for port in members:
        iface = self.running["interfaces"][port]
        status = info.get("member_states", {}).get((self.node_name, port), "Down")
        lines.append(f"  {port:<29} {status:<12} {iface.get('lacp_priority', 32768)}")
      lines.append("")
    if not lines:
      lines = ["Info: No Eth-Trunk is configured." if not requested else f"Error: Eth-Trunk {requested} does not exist."]
    self.append_plain_text("\n".join(lines).rstrip())
    self._prompt()

  def _configure_static_route(self, command):
    if not self.is_router and not any(iface.get("link_type") == "route" for iface in self.running.get("interfaces", {}).values()):
      return "Error: Configure at least one Layer 3 interface before adding a static route."
    removing = command.strip().lower().startswith("undo ")
    body = command.strip()[len("undo "):] if removing else command.strip()
    match = re.fullmatch(r"ip\s+route-static\s+(\S+)\s+(\S+)(?:\s+(\S+))?", body, re.I)
    if not match or (not removing and not match.group(3)):
      return "Error: Use ip route-static <destination> <mask> <next-hop> or undo ip route-static <destination> <mask> [next-hop]."
    import ipaddress
    try:
      network = ipaddress.IPv4Network(f"{match.group(1)}/{match.group(2)}", strict=False)
      next_hop = ipaddress.IPv4Address(match.group(3)) if match.group(3) else None
    except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
      return "Error: Enter a valid IPv4 destination, subnet mask, and next-hop address."
    routes = self.running.setdefault("static_routes", [])
    same_prefix = [route for route in routes if route.get("destination") == str(network.network_address) and route.get("mask") == str(network.netmask)]
    if removing:
      if next_hop is None:
        self.running["static_routes"] = [route for route in routes if route not in same_prefix]
      else:
        self.running["static_routes"] = [route for route in routes if not (
            route.get("destination") == str(network.network_address) and route.get("mask") == str(network.netmask)
            and route.get("next_hop") == str(next_hop))]
      if len(self.running["static_routes"]) == len(routes):
        return "Error: The specified static route does not exist."
      return ""
    if any(route.get("destination") == str(network.network_address) and route.get("mask") == str(network.netmask)
           and route.get("next_hop") == str(next_hop) for route in routes):
      return "Info: This static route already exists."
    routes.append({"destination": str(network.network_address), "mask": str(network.netmask), "next_hop": str(next_hop), "preference": 60})
    return ""

  def _configure_acl_view(self, command):
    low = command.strip().lower()
    acl = self.running.get("acls", {}).get(self.active_acl)
    if not acl:
      return "Error: The ACL does not exist."
    if acl.get("type", "advance") == "basic":
      if low.startswith("undo rule "):
        try:
          sequence = int(command.split()[2])
        except (IndexError, ValueError):
          return "Error: Use undo rule <sequence-number>."
        before = len(acl.setdefault("rules", []))
        acl["rules"] = [rule for rule in acl["rules"] if rule["sequence"] != sequence]
        return "" if len(acl["rules"]) != before else "Error: The specified ACL rule does not exist."
      match = re.fullmatch(r"rule\s+(?:(\d+)\s+)?(permit|deny)(?:\s+source\s+(\S+)(?:\s+(\S+))?)?", command.strip(), re.I)
      if not match:
        return "Error: Use rule [sequence] {permit|deny} [source <IPv4|any> <wildcard>]." if low.startswith("rule ") else None
      sequence_token, action, source, wildcard = match.groups()
      sequence = int(sequence_token) if sequence_token else max((rule["sequence"] for rule in acl.get("rules", [])), default=0) + 5
      address, mask = None, None
      if source:
        if source.lower() == "any":
          address, mask = "0.0.0.0", "255.255.255.255"
        else:
          try:
            address, mask = str(ipaddress.IPv4Address(source)), str(ipaddress.IPv4Address(wildcard or "0.0.0.0"))
          except ipaddress.AddressValueError:
            return "Error: Enter a valid IPv4 address and wildcard mask."
      rule = {"sequence": sequence, "action": action.lower(), "protocol": "ip", "source": address,
              "source_wildcard": mask, "destination": None, "destination_wildcard": None}
      acl["rules"] = [item for item in acl.get("rules", []) if item["sequence"] != sequence]
      acl["rules"].append(rule)
      acl["rules"].sort(key=lambda item: item["sequence"])
      return ""
    if low.startswith("undo rule "):
      try:
        sequence = int(command.split()[2])
      except (IndexError, ValueError):
        return "Error: Use undo rule <sequence-number>."
      before = len(acl.setdefault("rules", []))
      acl["rules"] = [rule for rule in acl["rules"] if rule["sequence"] != sequence]
      return "" if len(acl["rules"]) != before else "Error: The specified ACL rule does not exist."
    match = re.fullmatch(r"rule\s+(?:(\d+)\s+)?(permit|deny)\s+(ip|icmp)(?:\s+(source|destination)\s+(\S+)(?:\s+(\S+))?)?(?:\s+(source|destination)\s+(\S+)(?:\s+(\S+))?)?", command.strip(), re.I)
    if not match:
      if low.startswith("rule "):
        return "Error: Use rule [sequence] {permit|deny} {ip|icmp} [source|destination <IPv4> <wildcard>]."
      return None
    sequence_token, action, protocol, field1, value1, wildcard1, field2, value2, wildcard2 = match.groups()
    if field2 and field1 == field2:
      return "Error: Specify source and destination at most once each."
    sequence = int(sequence_token) if sequence_token else max((rule["sequence"] for rule in acl.get("rules", [])), default=0) + 5
    if not 0 <= sequence <= 4294967295:
      return "Error: ACL rule sequence must be in the range 0-4294967295."
    rule = {"sequence": sequence, "action": action.lower(), "protocol": protocol.lower(),
            "source": None, "source_wildcard": None, "destination": None, "destination_wildcard": None}
    for field, value, wildcard in ((field1, value1, wildcard1), (field2, value2, wildcard2)):
      if not field:
        continue
      if value.lower() == "any":
        address, mask = "0.0.0.0", "255.255.255.255"
      else:
        try:
          address = str(ipaddress.IPv4Address(value))
          mask = str(ipaddress.IPv4Address(wildcard or "0.0.0.0"))
        except ipaddress.AddressValueError:
          return "Error: Enter a valid IPv4 address and wildcard mask."
      rule[field.lower()] = address
      rule[f"{field.lower()}_wildcard"] = mask
    acl["rules"] = [entry for entry in acl.get("rules", []) if entry.get("sequence") != sequence]
    acl["rules"].append(rule)
    acl["rules"].sort(key=lambda entry: entry["sequence"])
    return ""

  def _configure_classifier_view(self, command):
    low = command.strip().lower()
    classifier = self.running.get("traffic_classifiers", {}).get(self.active_classifier)
    if not classifier:
      return "Error: The traffic classifier does not exist."
    if low.startswith("if-match acl "):
      number = command.split()[-1]
      if number not in self.running.get("acls", {}):
        return f"Error: ACL {number} does not exist."
      classifier["acl"] = number
      return ""
    if low == "undo if-match acl":
      classifier["acl"] = None
      return ""
    return None

  def _configure_behavior_view(self, command):
    low = command.strip().lower()
    behavior = self.running.get("traffic_behaviors", {}).get(self.active_behavior)
    if not behavior:
      return "Error: The traffic behavior does not exist."
    if low in ("permit", "deny"):
      behavior["action"] = low
      return ""
    if low == "undo permit" or low == "undo deny":
      behavior["action"] = "permit"
      return ""
    return None

  def _configure_policy_view(self, command):
    low = command.strip().lower()
    policy = self.running.get("traffic_policies", {}).get(self.active_policy)
    if not policy:
      return "Error: The traffic policy does not exist."
    match = re.fullmatch(r"classifier\s+(\S+)\s+behavior\s+(\S+)(?:\s+precedence\s+(\d+))?", command.strip(), re.I)
    if match:
      classifier, behavior, precedence = match.groups()
      if classifier not in self.running.get("traffic_classifiers", {}):
        return f"Error: Traffic classifier {classifier} does not exist."
      if behavior not in self.running.get("traffic_behaviors", {}):
        return f"Error: Traffic behavior {behavior} does not exist."
      entry = {"classifier": classifier, "behavior": behavior, "precedence": int(precedence or 5)}
      policy["classifiers"] = [item for item in policy.get("classifiers", []) if item["classifier"] != classifier]
      policy["classifiers"].append(entry)
      policy["classifiers"].sort(key=lambda item: item["precedence"])
      return ""
    if low.startswith("undo classifier "):
      name = command.split()[2]
      old = len(policy.get("classifiers", []))
      policy["classifiers"] = [item for item in policy.get("classifiers", []) if item["classifier"] != name]
      return "" if len(policy["classifiers"]) != old else "Error: The classifier is not referenced by this policy."
    return None

  def _display_acl(self, command):
    parts = command.split()
    selected = None
    if len(parts) > 2 and parts[1].lower() == "acl":
      selected = parts[3] if parts[2].lower() == "number" and len(parts) > 3 else parts[2]
    acls = self.running.get("acls", {})
    numbers = [selected] if selected else sorted(acls, key=lambda value: int(value))
    lines = []
    for number in numbers:
      acl = acls.get(number)
      if not acl:
        lines.append(f"Error: ACL {number} does not exist.")
        continue
      lines.extend([f"Advanced ACL {number}, {len(acl.get('rules', []))} rules"])
      for rule in sorted(acl.get("rules", []), key=lambda item: item["sequence"]):
        fields = [f"rule {rule['sequence']} {rule['action']} {rule.get('protocol', 'ip')}"]
        for field in ("source", "destination"):
          address = rule.get(field)
          if address:
            fields.extend([field, address, rule.get(f"{field}_wildcard") or "0.0.0.0"])
        lines.append(" ".join(fields))
    self.append_plain_text("\n".join(lines) if lines else "(No ACL is configured.)")
    self._prompt()

  def _display_traffic_policy(self, command):
    parts = command.split()
    policy_name = parts[4] if len(parts) > 4 and parts[2].lower() == "user-defined" else None
    policies = self.running.get("traffic_policies", {})
    names = [policy_name] if policy_name else sorted(policies)
    lines = []
    for name in names:
      policy = policies.get(name)
      if not policy:
        lines.append(f"Error: Traffic policy {name} does not exist.")
        continue
      lines.append(f"Traffic Policy: {name}")
      for entry in policy.get("classifiers", []):
        classifier = self.running.get("traffic_classifiers", {}).get(entry["classifier"], {})
        behavior = self.running.get("traffic_behaviors", {}).get(entry["behavior"], {})
        lines.append(f" Classifier: {entry['classifier']} (type {classifier.get('type', 'or')}) ACL {classifier.get('acl') or '-'}; Behavior: {entry['behavior']} ({behavior.get('action', 'permit')}); Precedence: {entry.get('precedence', 5)}")
      for ifname, iface in self.running.get("interfaces", {}).items():
        for direction, bound in iface.get("traffic_policies", {}).items():
          if bound == name:
            lines.append(f" Applied on: {ifname} {direction}")
    self.append_plain_text("\n".join(lines) if lines else "(No traffic policy is configured.)")
    self._prompt()

  def _start_ftp_client(self, address):
    try:
      address = str(ipaddress.IPv4Address(address))
    except ipaddress.AddressValueError:
      self.append_plain_text("Error: Enter a valid FTP server IPv4 address.")
      self._prompt()
      return
    conn = get_connection()
    try:
      rows = conn.execute("SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (self.username,)).fetchall()
      server = None
      for name, _kind, raw in rows:
        try:
          config = json.loads(raw)
        except (TypeError, ValueError):
          continue
        for ifname, iface in (config.get("interfaces") or {}).items():
          if iface.get("ip") == address:
            server = (name, config, ifname)
            break
        if server:
          break
      if not server:
        self.append_plain_text(f"Error: No reachable Datacom FTP server uses address {address}.")
        self._prompt()
        return
      server_name, config, server_if = server
      local_candidates = [item.get("ip") for item in self.running.get("interfaces", {}).values()
                          if item.get("ip") and not item.get("shutdown")]
      try:
        server_iface = (config.get("interfaces") or {}).get(server_if, {})
        server_network = ipaddress.IPv4Network(f"{address}/{server_iface.get('mask') or '255.255.255.0'}", strict=False)
        local_ip = next((value for value in local_candidates if ipaddress.IPv4Address(value) in server_network),
                        local_candidates[0] if local_candidates else "")
      except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
        local_ip = local_candidates[0] if local_candidates else ""
      source = config.get("ftp_server_source", "all-interface")
      source_port = re.sub(r"^(?:GE|GigabitEthernet)\s*", "GE", source, flags=re.I).replace(" ", "").upper()
      server_port = re.sub(r"^(?:GE|GigabitEthernet)\s*", "GE", server_if, flags=re.I).replace(" ", "").upper()
      source_match = source.lower() == "all-interface" or source_port == server_port
      acl_number = str(config.get("ftp_server_acl") or "")
      acl = (config.get("acls") or {}).get(acl_number) if acl_number else None
      if acl:
        permitted = False
        try:
          client_ip = ipaddress.IPv4Address(local_ip)
          for rule in sorted(acl.get("rules", []), key=lambda item: item.get("sequence", 0)):
            rule_source = rule.get("source")
            wildcard = ipaddress.IPv4Address(rule.get("source_wildcard") or "0.0.0.0")
            if not rule_source or (int(client_ip) & (~int(wildcard) & 0xffffffff)) == (int(ipaddress.IPv4Address(rule_source)) & (~int(wildcard) & 0xffffffff)):
              permitted = rule.get("action") == "permit"
              break
        except ipaddress.AddressValueError:
          permitted = False
        if not permitted:
          self.append_plain_text(f"Error: FTP connection denied by ACL {acl_number}.")
          self._prompt()
          return
      from modules.datacom_network import ping_datacom
      ping = ping_datacom(self.username, self.node_name, address)
      if not config.get("ftp_server_enabled") or not source_match or ping is None or not ping[1]:
        self.append_plain_text(f"Error: FTP connection to {address} failed.")
        self._prompt()
        return
      self.ftp_session = {"server_node": server_name, "server_ip": address, "server_interface": server_if,
                          "username": "", "permissions": [], "directory": "flash:/", "transfer_mode": "ascii"}
      self.append_plain_text(f"Trying {address} ...\nConnected to {address}.\n220 FTP service ready.\nUser({address}:(none)):")
      self.pending = "ftp_login_user"
      self.prompt_position = len(self.terminal.toPlainText())
    finally:
      conn.close()

  def _complete_ftp_login(self, password):
    conn = get_connection()
    try:
      row = conn.execute("SELECT running_json FROM olt_datacom_devices WHERE username=? AND node_name=?",
                         (self.username, self.ftp_session["server_node"])).fetchone()
      if not row:
        self.mode = "USER"
        return "530 FTP server is unavailable."
      config = json.loads(row[0])
      user = (config.get("aaa_users") or {}).get(self.ftp_login_name)
      if not user or not user.get("password_hash") or not user.get("password_salt"):
        self.mode = "USER"
        return "530 Login incorrect."
      supplied = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(user["password_salt"]), 120000).hex()
      if not hmac.compare_digest(supplied, user["password_hash"]) or "ftp" not in user.get("service_types", []):
        self.mode = "USER"
        return "530 Login incorrect."
      self.ftp_session.update({"username": self.ftp_login_name, "permissions": user.get("ftp_permissions", ["read", "write", "execute"]),
                               "directory": user.get("ftp_directory", "flash:/")})
      self.mode = "FTP"
      return "230 User logged in.\n200 Current directory is flash:/"
    finally:
      conn.close()

  def _ftp_server_file_data(self):
    conn = get_connection()
    try:
      row = conn.execute("SELECT files_json,running_json,saved_json,startup_file FROM olt_datacom_devices WHERE username=? AND node_name=?",
                         (self.username, self.ftp_session["server_node"])).fetchone()
      if not row:
        return None, None
      return conn, row
    except Exception:
      conn.close()
      raise

  @staticmethod
  def _file_byte_count(file_data):
    return len(json.dumps(file_data, ensure_ascii=False, sort_keys=True).encode("utf-8"))

  def _handle_ftp_command(self, command):
    low, parts = command.lower().strip(), command.split()
    if low in ("bye", "quit", "exit"):
      self.append_plain_text("221 Server closing connection.")
      self.ftp_session = None
      self.mode = "USER"
      self._prompt()
      return
    if low in ("ascii", "binary"):
      self.ftp_session["transfer_mode"] = low
      self.append_plain_text("200 Type set to A." if low == "ascii" else "200 Type set to I.")
      self._prompt()
      return
    if low in ("pwd", "pwd/"):
      self.append_plain_text(f'257 "{self.ftp_session.get("directory", "flash:/")}" is current directory.')
      self._prompt()
      return
    if low.startswith("cd "):
      directory = command.split(None, 1)[1].strip()
      if directory not in ("flash:/", "/", "."):
        self.append_plain_text("550 Only flash:/ is available in this simulator.")
      else:
        self.ftp_session["directory"] = "flash:/"
        self.append_plain_text('250 Directory changed to "flash:/".')
      self._prompt()
      return
    if low == "dir" or low == "ls":
      if "read" not in self.ftp_session.get("permissions", []):
        self.append_plain_text("550 Permission denied.")
        self._prompt()
        return
      conn, row = self._ftp_server_file_data()
      try:
        remote_files = json.loads(row[0] or "{}") if row else {}
        lines = ["Directory of flash:/"]
        lines.extend(f"-rw- {self._file_byte_count(data):>9} {name}" for name, data in sorted(remote_files.items()))
        self.append_plain_text("\n".join(lines) if remote_files else "Directory of flash:/\n(No files.)")
      finally:
        conn.close()
      self._prompt()
      return
    match = re.fullmatch(r"get\s+(\S+)(?:\s+(\S+))?", command, re.I)
    if match:
      remote_name, local_name = match.group(1), match.group(2) or match.group(1)
      if "read" not in self.ftp_session.get("permissions", []):
        self.append_plain_text("550 Permission denied.")
        self._prompt()
        return
      conn, row = self._ftp_server_file_data()
      try:
        remote_files = json.loads(row[0] or "{}") if row else {}
        data = remote_files.get(remote_name)
        if data is None and row and row[3] == remote_name and row[2]:
          data = json.loads(row[2])
        if data is None:
          self.append_plain_text(f"550 {remote_name}: No such file.")
        else:
          conn.close()
          conn = None
          self._refresh_storage()
          self.files[local_name] = json.loads(json.dumps(data))
          self._storage_dirty = True
          self._persist()
          size = self._file_byte_count(data)
          self.append_plain_text(f"200 Port command okay.\n150 Opening {self.ftp_session['transfer_mode'].upper()} mode data connection for {remote_name}.\n{remote_name} 100% [**********]\n226 Transfer complete.\nFTP: {size} byte(s) received.")
      finally:
        if conn:
          conn.close()
      self._prompt()
      return
    match = re.fullmatch(r"put\s+(\S+)(?:\s+(\S+))?", command, re.I)
    if match:
      local_name, remote_name = match.group(1), match.group(2) or match.group(1)
      if "write" not in self.ftp_session.get("permissions", []):
        self.append_plain_text("550 Permission denied.")
      else:
        self._refresh_storage()
        if local_name not in self.files:
          self.append_plain_text(f"550 {local_name}: No such local file.")
          self._prompt()
          return
        conn, row = self._ftp_server_file_data()
        try:
          if not row:
            self.append_plain_text("550 FTP server is unavailable.")
          else:
            remote_files = json.loads(row[0] or "{}")
            remote_files[remote_name] = json.loads(json.dumps(self.files[local_name]))
            conn.execute("UPDATE olt_datacom_devices SET files_json=? WHERE username=? AND node_name=?",
                         (json.dumps(remote_files), self.username, self.ftp_session["server_node"]))
            conn.commit()
            size = self._file_byte_count(self.files[local_name])
            self.append_plain_text(f"200 Port command okay.\n150 Opening {self.ftp_session['transfer_mode'].upper()} mode data connection for {remote_name}.\n{remote_name} 100% [**********]\n226 Transfer complete.\nFTP: {size} byte(s) sent.")
        finally:
          conn.close()
      self._prompt()
      return
    match = re.fullmatch(r"delete\s+(\S+)", command, re.I)
    if match:
      filename = match.group(1)
      if "write" not in self.ftp_session.get("permissions", []):
        self.append_plain_text("550 Permission denied.")
        self._prompt()
        return
      self.pending_ftp_delete = filename
      self.pending = "ftp_delete_confirm"
      self._write(f"Warning: File {filename} will be deleted. Continue? [Y/N]:")
      self.prompt_position = len(self.terminal.toPlainText())
      return
    self.append_plain_text("Error: Supported FTP commands are ascii, binary, dir, get, put, delete, pwd, cd, and bye.")
    self._prompt()

  def _ftp_delete_remote(self, filename, confirmed):
    if not confirmed:
      self.append_plain_text("Info: File deletion cancelled.")
      return
    conn, row = self._ftp_server_file_data()
    try:
      remote_files = json.loads(row[0] or "{}") if row else {}
      if filename not in remote_files:
        self.append_plain_text(f"550 {filename}: No such file.")
      else:
        del remote_files[filename]
        conn.execute("UPDATE olt_datacom_devices SET files_json=?,startup_file=CASE WHEN startup_file=? THEN '' ELSE startup_file END WHERE username=? AND node_name=?",
                     (json.dumps(remote_files), filename, self.username, self.ftp_session["server_node"]))
        conn.commit()
        self.append_plain_text(f"250 DELE command successful: {filename}.")
    finally:
      if conn:
        conn.close()

  def _configure_aaa_submode(self, command):
    low = command.lower().strip()
    if self.mode == "AUTH_SCHEME" and low.startswith("authentication-mode "):
      mode = low.split()[-1]
      if mode != "local":
        return "Error: This lab supports authentication-mode local."
      self.running["authentication_schemes"][self.active_auth_scheme]["mode"] = mode
      return ""
    if self.mode == "AUTHZ_SCHEME" and low.startswith("authorization-mode "):
      mode = low.split()[-1]
      if mode != "local":
        return "Error: This lab supports authorization-mode local."
      self.running["authorization_schemes"][self.active_authz_scheme]["mode"] = mode
      return ""
    if self.mode == "ACCOUNTING_SCHEME" and low.startswith("accounting-mode "):
      mode = low.split()[-1]
      self.running["accounting_schemes"][self.active_accounting_scheme]["mode"] = mode
      return ""
    if self.mode == "AAA_DOMAIN":
      match = re.fullmatch(r"(authentication-scheme|authorization-scheme|accounting-scheme)\s+(\S+)", command, re.I)
      if match:
        field = {"authentication-scheme": "authentication_scheme", "authorization-scheme": "authorization_scheme", "accounting-scheme": "accounting_scheme"}[match.group(1).lower()]
        self.running["aaa_domains"].setdefault(self.active_aaa_domain, {})[field] = match.group(2)
        return ""
      if low == "undo accounting-scheme":
        self.running["aaa_domains"].setdefault(self.active_aaa_domain, {}).pop("accounting_scheme", None)
        return ""
    return None

  @staticmethod
  def _nat_port(value):
    services = {"ftp": 21, "ssh": 22, "telnet": 23, "smtp": 25, "dns": 53, "http": 80, "www": 80, "https": 443}
    return services.get(value.lower(), int(value) if value.isdigit() else None)

  def _configure_nat_view(self, command):
    low = command.lower().strip()
    if self.mode == "NAT_ADDRESS_GROUP":
      group = self.running.setdefault("nat_address_groups", {}).setdefault(self.active_nat_group, {"id": 1, "mode": "pat", "sections": []})
      match = re.fullmatch(r"mode\s+(pat|no-pat)", low)
      if match:
        group["mode"] = match.group(1)
        return ""
      match = re.fullmatch(r"section\s+(\d+)\s+(\S+)\s+(\S+)", command, re.I)
      if match:
        section_id, first, last = match.groups()
        try:
          first, last = ipaddress.IPv4Address(first), ipaddress.IPv4Address(last)
        except ipaddress.AddressValueError:
          return "Error: Enter valid IPv4 addresses for the section range."
        if int(last) < int(first):
          return "Error: The section end address must be greater than or equal to the start address."
        sections = [item for item in group.setdefault("sections", []) if int(item["id"]) != int(section_id)]
        sections.append({"id": int(section_id), "start": str(first), "end": str(last)})
        group["sections"] = sorted(sections, key=lambda item: item["id"])
        return ""
      match = re.fullmatch(r"undo section\s+(\d+)", low)
      if match:
        group["sections"] = [item for item in group.get("sections", []) if int(item["id"]) != int(match.group(1))]
        return ""
      return "Error: Use mode pat, mode no-pat, or section <id> <start-ip> <end-ip>."
    if self.mode == "NAT_POLICY":
      match = re.fullmatch(r"rule name\s+([\w.-]{1,63})", command, re.I)
      if match:
        name = match.group(1)
        self.active_nat_rule = name
        self.running.setdefault("nat_policy_rules", {}).setdefault(name, {"source": "0.0.0.0/0", "action": None, "address_group": ""})
        self.mode = "NAT_RULE"
        return ""
      match = re.fullmatch(r"undo rule name\s+(\S+)", command, re.I)
      if match:
        self.running.setdefault("nat_policy_rules", {}).pop(match.group(1), None)
        return ""
      return "Error: Use rule name <rule-name>."
    rule = self.running.setdefault("nat_policy_rules", {}).setdefault(self.active_nat_rule, {"source": "0.0.0.0/0", "action": None, "address_group": ""})
    match = re.fullmatch(r"source-address\s+range\s+(\S+)\s+(\S+)", command, re.I)
    if match:
      try:
        first, last = map(ipaddress.IPv4Address, match.groups())
      except ipaddress.AddressValueError:
        return "Error: Enter valid IPv4 source-address range values."
      if int(first) > int(last):
        return "Error: The source-address range start must not exceed its end."
      rule["source"] = {"type": "range", "start": str(first), "end": str(last)}
      return ""
    match = re.fullmatch(r"source-address\s+(\S+)\s+(\S+)", command, re.I)
    if match:
      address, mask = match.groups()
      try:
        network = ipaddress.IPv4Network(f"{address}/{mask}", strict=False)
      except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
        return "Error: Use source-address <network-address> <mask> or source-address range <start> <end>."
      rule["source"] = {"type": "network", "network": str(network)}
      return ""
    match = re.fullmatch(r"source-address\s+(\S+)", command, re.I)
    if match:
      try:
        network = ipaddress.IPv4Network(match.group(1), strict=False)
      except ValueError:
        return "Error: Enter a valid IPv4 address or prefix."
      rule["source"] = {"type": "network", "network": str(network)}
      return ""
    match = re.fullmatch(r"action\s+source-nat\s+(?:address-group\s+(\S+)|(easy-ip))", command, re.I)
    if match:
      group, easy_ip = match.groups()
      if group and group not in self.running.get("nat_address_groups", {}):
        return f"Error: NAT address group {group} does not exist."
      rule["action"] = "address-group" if group else "easy-ip"
      rule["address_group"] = group or ""
      return ""
    return "Error: Use source-address [range] ... or action source-nat {address-group <name>|easy-ip}."

  def _configure_nat_server(self, command):
    match = re.fullmatch(r"undo nat server\s+(\S+)", command, re.I)
    if match:
      self.running.setdefault("nat_servers", {}).pop(match.group(1), None)
      return ""
    match = re.fullmatch(r"nat server\s+(\S+)\s+protocol\s+(tcp|udp)\s+global\s+(\S+)\s+(\S+)\s+inside\s+(\S+)\s+(\S+)", command, re.I)
    if not match:
      return "Error: Use nat server <name> protocol {tcp|udp} global <ip> <port|service> inside <ip> <port|service>."
    name, protocol, global_ip, global_port, inside_ip, inside_port = match.groups()
    try:
      global_ip = str(ipaddress.IPv4Address(global_ip))
      inside_ip = str(ipaddress.IPv4Address(inside_ip))
    except ipaddress.AddressValueError:
      return "Error: NAT Server global and inside addresses must be valid IPv4 addresses."
    global_port, inside_port = self._nat_port(global_port), self._nat_port(inside_port)
    if global_port is None or inside_port is None or not 1 <= global_port <= 65535 or not 1 <= inside_port <= 65535:
      return "Error: NAT Server ports must be 1-65535 or a supported service name."
    self.running.setdefault("nat_servers", {})[name] = {"protocol": protocol.lower(), "global_address": global_ip,
        "global_port": global_port, "inside_address": inside_ip, "inside_port": inside_port}
    return ""

  def _configure_vty(self, command):
    low = command.lower().strip()
    start, end = self.active_vty_range
    entry = self.running.setdefault("vty_interfaces", {}).setdefault(f"{start}-{end}", {"start": start, "end": end, "authentication_mode": "aaa", "protocols": ["ssh"], "privilege": 3})
    if low == "authentication-mode aaa":
      entry["authentication_mode"] = "aaa"
      return ""
    match = re.fullmatch(r"protocol inbound (telnet|ssh|all)", low)
    if match:
      entry["protocols"] = ["telnet", "ssh"] if match.group(1) == "all" else [match.group(1)]
      return ""
    match = re.fullmatch(r"user privilege level (\d+)", low)
    if match and 0 <= int(match.group(1)) <= 15:
      entry["privilege"] = int(match.group(1))
      return ""
    return "Error: Use authentication-mode aaa, protocol inbound {telnet|ssh|all}, or user privilege level <0-15>."

  def _configure_ssh_user(self, command):
    match = re.fullmatch(r"ssh user (\S+)(?:\s+authentication-type\s+(password|rsa|password-rsa))?", command, re.I)
    if not match:
      match = re.fullmatch(r"ssh user (\S+) service-type (stelnet|all)", command, re.I)
      if not match:
        return "Error: Use ssh user <name> authentication-type password or ssh user <name> service-type stelnet."
      name, service = match.groups()
      self.running.setdefault("ssh_users", {}).setdefault(name, {"authentication_type": "password", "service_type": "stelnet"})["service_type"] = service.lower()
      return ""
    name, auth_type = match.groups()
    self.running.setdefault("ssh_users", {}).setdefault(name, {"authentication_type": "password", "service_type": "stelnet"})["authentication_type"] = (auth_type or "password").lower()
    return ""

  def _start_remote_client(self, command):
    match = re.fullmatch(r"(telnet|stelnet)\s+(\S+)(?:\s+(\d+))?", command, re.I)
    if not match:
      self.append_plain_text("Error: Use telnet <ip-address> or stelnet <ip-address> [port].")
      self._prompt()
      return
    protocol, address, port = match.group(1).lower(), match.group(2), int(match.group(3) or (23 if match.group(1).lower() == "telnet" else 22))
    try:
      ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
      self.append_plain_text("Error: Enter a valid IPv4 address.")
      self._prompt()
      return
    conn = get_connection()
    try:
      rows = conn.execute("SELECT node_name,device_type,running_json,password_salt,password_hash FROM olt_datacom_devices WHERE username=?", (self.username,)).fetchall()
    finally:
      conn.close()
    nat_server_mapping = None
    for server_row in rows:
      server_config = json.loads(server_row[2] or "{}")
      for server_name, server in server_config.get("nat_servers", {}).items():
        if (protocol == "telnet" and server.get("protocol") == "tcp" and server.get("global_address") == address
            and int(server.get("global_port", 23)) == port):
          nat_server_mapping = {"router": server_row[0], "name": server_name, **server, "config": server_config}
          break
      if nat_server_mapping:
        break
    lookup_address = nat_server_mapping["inside_address"] if nat_server_mapping else address
    found = None
    for row in rows:
      config = json.loads(row[2] or "{}")
      for interface, settings in config.get("interfaces", {}).items():
        if settings.get("ip") == lookup_address and not settings.get("shutdown"):
          found = (row, config, interface)
          break
      if found:
        break
    if not found:
      self.append_plain_text(f"Error: No reachable Datacom device is configured with address {address}.")
      self._prompt()
      return
    row, config, target_interface = found
    enabled = config.get("telnet_server_enabled") if protocol == "telnet" else config.get("stelnet_server_enabled")
    expected_port = 23 if protocol == "telnet" else int(config.get("ssh_server_port", 22))
    if not enabled or (not nat_server_mapping and port != expected_port):
      self.append_plain_text(f"Error: {protocol.upper()} connection refused by {row[0]}.")
      self._prompt()
      return
    source_field = "telnet_server_source" if protocol == "telnet" else "ssh_server_source"
    source = config.get(source_field, "all-interface")
    if source.lower() != "all-interface" and source.replace(" ", "").lower() != target_interface.replace(" ", "").lower():
      self.append_plain_text(f"Error: {protocol.upper()} server source interface does not match the destination.")
      self._prompt()
      return
    local_ip = next((item.get("ip") for item in self.running.get("interfaces", {}).values() if item.get("ip") and not item.get("shutdown")), "")
    if not local_ip:
      self.append_plain_text("Error: Configure an IP address on this device before starting a remote session.")
      self._prompt()
      return
    from modules.datacom_network import ping_datacom
    reachability = ping_datacom(self.username, self.node_name, address, source_address=local_ip)
    if nat_server_mapping and (not reachability or not reachability[1]):
      public_interface = next((iface.get("ip") for iface in nat_server_mapping["config"].get("interfaces", {}).values()
                               if iface.get("nat_enabled") and iface.get("ip")), None)
      if public_interface:
        reachability = ping_datacom(self.username, self.node_name, public_interface, source_address=local_ip)
    if not reachability or not reachability[1]:
      self.append_plain_text(f"Error: No IP connectivity to {address}; check interfaces, routes, VLANs, and links.")
      self._prompt()
      return
    self.remote_target = {"node_name": row[0], "device_type": row[1], "config": config, "salt": row[3], "hash": row[4], "address": address, "inside_address": lookup_address, "interface": target_interface, "protocol": protocol, "client_ip": local_ip}
    if nat_server_mapping:
      self.remote_target["nat_server_mapping"] = nat_server_mapping
    self.remote_address = address
    self.remote_protocol = protocol
    if protocol == "stelnet":
      fingerprint = hashlib.sha256(f"{row[0]}:{address}".encode()).hexdigest()[:40]
      self.remote_fingerprint = fingerprint
      if self.running.get("ssh_known_hosts", {}).get(address) != fingerprint:
        if not self.running.get("ssh_client_first_time_enabled"):
          self.append_plain_text("Error: SSH host key is unknown. Run ssh client first-time enable first.")
          self._prompt()
          return
        self.pending = "remote_key_trust"
        self._write(f"The server's key fingerprint is ssh-rsa {fingerprint}.\nThe server is not authenticated. Continue to access it? [Y/N]:")
        self.prompt_position = len(self.terminal.toPlainText())
        return
    self.pending = "remote_user"
    self._write("Please input the username:")
    self.prompt_position = len(self.terminal.toPlainText())

  @staticmethod
  def _verify_remote_password(account, password):
    salt, expected = account.get("password_salt", ""), account.get("password_hash", "")
    if not salt or not expected:
      return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120000).hex()
    return hmac.compare_digest(actual, expected)

  def _remote_credentials_valid(self, password):
    config = self.remote_target["config"]
    account = config.get("aaa_users", {}).get(self.remote_login_name)
    domain_name = self.remote_login_name.rsplit("@", 1)[1] if "@" in self.remote_login_name else "default"
    domain = config.get("aaa_domains", {}).get(domain_name)
    if domain is None and domain_name != "default":
      return False
    domain = domain or {}
    auth_scheme = config.get("authentication_schemes", {}).get(domain.get("authentication_scheme", "default"), {"mode": "local"})
    authz_scheme = config.get("authorization_schemes", {}).get(domain.get("authorization_scheme", "default"), {"mode": "local"})
    if not account or auth_scheme.get("mode") != "local" or authz_scheme.get("mode") != "local":
      return False
    required_service = "ssh" if self.remote_protocol == "stelnet" else "telnet"
    if required_service not in [value.lower() for value in account.get("service_types", [])]:
      return False
    if self.remote_protocol == "stelnet":
      ssh_user = config.get("ssh_users", {}).get(self.remote_login_name, {})
      if ssh_user.get("service_type", "stelnet") not in ("stelnet", "all") or ssh_user.get("authentication_type", "password") not in ("password", "password-rsa"):
        return False
    vty = next((row for row in config.get("vty_interfaces", {}).values()
                if row.get("authentication_mode") == "aaa" and required_service in row.get("protocols", [])
                and int(row.get("start", 0)) <= (5 if self.remote_protocol == "stelnet" else 0) <= int(row.get("end", 20))), None)
    if not vty:
      return False
    if not self._verify_remote_password(account, password):
      return False
    self.remote_account = account
    self.remote_target["vty"] = 5 if self.remote_protocol == "stelnet" else 0
    return True

  def _set_remote_password(self, account, password):
    salt = secrets.token_bytes(16).hex()
    account["password_salt"] = salt
    account["password_hash"] = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120000).hex()
    account["password_force_change"] = False

  def _persist_remote_target(self):
    conn = get_connection()
    try:
      conn.execute("UPDATE olt_datacom_devices SET running_json=? WHERE username=? AND node_name=?",
                   (json.dumps(self.remote_target["config"]), self.username, self.remote_target["node_name"]))
      conn.commit()
    finally:
      conn.close()

  def _enter_remote_session(self):
    from modules.datacom_remote import register
    mapping = self.remote_target.get("nat_server_mapping")
    if mapping:
      from modules.datacom_nat import record_session
      record_session(self.username, mapping["router"], "TCP", self.remote_target["client_ip"], mapping["global_address"],
                     new_destination=mapping["inside_address"], source_port=53000,
                     destination_port=mapping["global_port"], new_destination_port=mapping["inside_port"],
                     ttl=596, nat_rule=mapping["name"])
    self._persist()
    self.remote_origin = {key: copy.deepcopy(getattr(self, key)) for key in ("node_name", "device_type", "is_switch", "is_router", "sysname", "mode", "interface", "active_vlan", "active_dhcp_pool", "active_ospf_process", "active_ospf_area", "interface_ports", "running", "saved", "files", "startup_file", "_storage_dirty", "password_salt", "password_hash", "boot_count")}
    target = self.remote_target
    self.node_name, self.device_type = target["node_name"], target["device_type"]
    self.is_switch = "switch" in self.device_type.lower()
    self.is_router = not self.is_switch
    self.mode, self.interface, self.active_vlan = "USER", None, None
    self.active_dhcp_pool = self.active_ospf_process = self.active_ospf_area = None
    self.interface_ports = []
    self._load_state()
    self.mode = "USER"
    self.remote_session_id = register(self.username, self.node_name, self.remote_origin["node_name"], target["client_ip"], target["protocol"], target.get("vty", 0), self.remote_login_name)
    self.remote_active = True
    self.append_plain_text(f"\nInfo: Connected to {self.sysname} ({target['address']}) using {target['protocol'].upper()}.")
    self._prompt()

  def _leave_remote_session(self):
    from modules.datacom_remote import unregister
    unregister(getattr(self, "remote_session_id", ""))
    for key, value in self.remote_origin.items():
      setattr(self, key, value)
    self.remote_active = False
    self.remote_origin = None
    self.append_plain_text("Info: Remote session closed.")
    self._prompt()

  def _display_remote_users(self):
    from modules.datacom_remote import sessions
    rows = sessions(self.username, self.node_name)
    listing = "\n".join(f"VTY {row['vty']:<7} {row['login_name']:<20} {row['client_ip']:<16} {row['protocol']:<10} 00:00  {self.running.get('aaa_users', {}).get(row['login_name'], {}).get('privilege', 3)}" for row in rows)
    self.append_plain_text("User-Intf    Username             Client           Type       Idle   User's Level\n" + (listing or "No active remote users."))
    self._prompt()

  def _display_aaa_configuration(self):
    lines = [f"AAA configuration for {self.sysname}:", f"Telnet server: {'Enabled' if self.running.get('telnet_server_enabled') else 'Disabled'}",
             f"Telnet source: {self.running.get('telnet_server_source', 'all-interface')}",
             f"STelnet server: {'Enabled' if self.running.get('stelnet_server_enabled') else 'Disabled'}",
             f"SSH server source: {self.running.get('ssh_server_source', 'all-interface')}", "Local users:"]
    for name, user in sorted(self.running.get("aaa_users", {}).items()):
      lines.append(f"  {name}  service-type: {' '.join(user.get('service_types', [])) or '-'}  privilege: {user.get('privilege', 0)}")
    lines.append("VTY lines:")
    for key, vty in sorted(self.running.get("vty_interfaces", {}).items()):
      lines.append(f"  {key}: authentication-mode {vty.get('authentication_mode', 'aaa')}; protocol inbound {' '.join(vty.get('protocols', []))}")
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _display_local_user(self, name):
    user = self.running.get("aaa_users", {}).get(name)
    if not user:
      self.append_plain_text(f"Error: Local user {name} does not exist.")
    else:
      active = __import__("modules.datacom_remote", fromlist=["sessions"]).sessions(self.username, self.node_name)
      accessed = sum(1 for session in active if session["login_name"] == name)
      self.append_plain_text(f"Password : {'****************' if user.get('password_hash') else 'Not set'}\nState : active\nService-type-mask : {' '.join(user.get('service_types', [])) or '-'}\nPrivilege level : {user.get('privilege', 0)}\nPassword-force-change : {'Yes' if user.get('password_force_change') else 'No'}\nAccessed-num : {accessed}")
    self._prompt()

  def _display_nat_address_groups(self, command):
    requested = command.split()[-1] if len(command.split()) > 3 else None
    groups = self.running.get("nat_address_groups", {})
    selected = [(name, group) for name, group in groups.items() if requested in (None, "all", name)]
    lines = ["NAT Address Group Information:", "Name                 ID    Mode    Section  Start IP        End IP", "-----------------------------------------------------------------------"]
    for name, group in selected:
      sections = group.get("sections", []) or [{"id": "-", "start": "-", "end": "-"}]
      for section in sections:
        lines.append(f"{name:<20} {group.get('id', 1):<5} {group.get('mode', 'pat'):<7} {str(section['id']):<8} {section['start']:<15} {section['end']}")
    lines.extend(["-----------------------------------------------------------------------", f"Total: {len(selected)}"])
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _display_nat_servers(self):
    rows = self.running.get("nat_servers", {})
    lines = ["NAT Server Information:", "Name            Protocol  Global Address:Port  Inside Address:Port", "------------------------------------------------------------------"]
    for name, item in sorted(rows.items()):
      lines.append(f"{name:<15} {item['protocol'].upper():<9} {item['global_address']}:{item['global_port']:<5} {item['inside_address']}:{item['inside_port']}")
    lines.extend(["------------------------------------------------------------------", f"Total: {len(rows)}"])
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _display_nat_policy(self):
    rules = self.running.get("nat_policy_rules", {})
    lines = ["NAT Policy Information:"]
    for name, rule in sorted(rules.items()):
      source = rule.get("source", {})
      if source.get("type") == "range":
        source_text = f"range {source['start']} {source['end']}"
      else:
        source_text = source.get("network", "0.0.0.0/0")
      action = f"address-group {rule['address_group']}" if rule.get("action") == "address-group" else (rule.get("action") or "not set")
      lines.append(f"Rule name: {name}\n  Source address: {source_text}\n  Action: source-nat {action}")
    if not rules:
      lines.append("No NAT policy rules are configured.")
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _display_nat_sessions(self):
    from modules.datacom_nat import sessions
    rows = sessions(self.username, self.node_name)
    lines = ["Session Table Information:"]
    for row in rows:
      protocol = {"ICMP": "1", "TCP": "6", "UDP": "17"}.get(row["protocol"], row["protocol"])
      lines.append(f"Protocol : {protocol} ({row['protocol']})")
      if row.get("source_port") is not None:
        lines.append(f"SrcAddr Port VPN : {row['source']} {row['source_port']}")
        lines.append(f"DestAddr Port VPN : {row['destination']} {row['destination_port']}")
      else:
        lines.append(f"SrcAddr VPN : {row['source']}\nDestAddr VPN : {row['destination']}")
      if row.get("icmp_id") is not None:
        lines.append(f"Type Code IcmpId : 8 0 {row['icmp_id']}")
      lines.append(f"Time To Live : {row['ttl']} s\nNAT Info")
      if row["new_source"] != "-":
        lines.append(f"New SrcAddr : {row['new_source']}")
      if row["new_destination"] != "-":
        lines.append(f"New DestAddr : {row['new_destination']}")
      if row.get("new_source_port") is not None:
        lines.append(f"New SrcPort : {row['new_source_port']}")
      if row.get("new_destination_port") is not None:
        lines.append(f"New DestPort : {row['new_destination_port']}")
      lines.append("")
    lines.append(f"Total : {len(rows)}")
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _configure_aaa_user(self, command):
    match = re.fullmatch(r"local-user\s+(\S+)\s+password\s+(?:irreversible-cipher|cipher|plain)\s+(\S+)", command, re.I)
    if match:
      name, password = match.groups()
      cipher_match = re.fullmatch(r"([0-9a-f]{32})\$([0-9a-f]{64})", password, re.I)
      if not 1 <= len(name) <= 64 or (not cipher_match and not 1 <= len(password) <= 64):
        return "Error: Local user name and password must contain 1-64 characters."
      users = self.running.setdefault("aaa_users", {})
      user = users.setdefault(name, {"service_types": [], "privilege": 0,
                                     "ftp_directory": "flash:/", "ftp_permissions": ["read", "write", "execute"]})
      must_change = not user.get("password_hash")
      if cipher_match:
        user["password_salt"], user["password_hash"] = cipher_match.groups()
      else:
        salt = secrets.token_bytes(16).hex()
        user["password_salt"] = salt
        user["password_hash"] = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120000).hex()
        user["password_force_change"] = must_change
      return f"Info: The initial password of {name} must be changed during login." if must_change and not cipher_match else ""
    match = re.fullmatch(r"local-user\s+(\S+)\s+service-type\s+((?:(?:telnet|ssh|ftp|sftp)\s*)+)", command, re.I)
    if match:
      name, services = match.groups()
      self.pending_aaa_change = (name, services.lower().split())
      self.pending = "aaa_service_confirm"
      self._write("Warning: Changing local-user service type may affect access. Continue? [Y/N]:")
      self.prompt_position = len(self.terminal.toPlainText())
      return ""
    match = re.fullmatch(r"local-user\s+(\S+)\s+privilege\s+level\s+(\d+)", command, re.I)
    if match:
      name, level = match.groups()
      if not 0 <= int(level) <= 15:
        return "Error: Privilege level range is 0-15."
      self.pending_aaa_change = (name, level)
      self.pending = "aaa_privilege_confirm"
      self._write("Warning: Changing user privilege may affect online users. Continue? [Y/N]:")
      self.prompt_position = len(self.terminal.toPlainText())
      return ""
    match = re.fullmatch(r"local-user\s+(\S+)\s+ftp-directory\s+(\S+)(?:\s+(.*))?", command, re.I)
    if match:
      name, directory, permission_text = match.groups()
      if not directory.startswith("flash:/"):
        return "Error: FTP directory must be an absolute flash:/ path."
      permissions = [word.lower() for word in (permission_text or "read execute write").split()]
      if any(word not in ("read", "write", "execute") for word in permissions):
        return "Error: FTP permissions can be read, write, or execute."
      user = self.running.setdefault("aaa_users", {}).setdefault(name, {"service_types": [], "privilege": 0,
                                                                         "ftp_directory": "flash:/", "ftp_permissions": ["read", "write", "execute"]})
      user["ftp_directory"], user["ftp_permissions"] = directory, permissions
      return ""
    if command.lower().startswith("local-user "):
      return "Error: Use local-user <name> password irreversible-cipher <password>, service-type {telnet|ssh|ftp}, privilege level <0-15>, or ftp-directory flash:/ [read execute write]."
    return None

  def _configure_ipv6_static_route(self, command):
    removing = command.strip().lower().startswith("undo ")
    body = command.strip()[len("undo "):] if removing else command.strip()
    parts = body.split()
    if len(parts) not in (3, 4, 5) or " ".join(parts[:2]).lower() != "ipv6 route-static":
      return "Error: Use ipv6 route-static <destination> <prefix-length> <next-hop>."
    destination_token = parts[2]
    if "/" in destination_token:
      try:
        parsed_network = ipaddress.IPv6Network(destination_token, strict=False)
      except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
        return "Error: Enter a valid IPv6 destination and prefix length."
      destination, prefix = str(parsed_network.network_address), str(parsed_network.prefixlen)
      next_hop = parts[3] if len(parts) == 4 else None
    else:
      if len(parts) < 4:
        return "Error: Use ipv6 route-static <destination> <prefix-length> <next-hop>."
      destination, prefix = parts[2], parts[3]
      next_hop = parts[4] if len(parts) == 5 else None
    if not prefix or (not removing and not next_hop):
      return "Error: Use ipv6 route-static <destination> <prefix-length> <next-hop>."
    try:
      network = ipaddress.IPv6Network(f"{destination}/{prefix}", strict=False)
      parsed_next_hop = ipaddress.IPv6Address(next_hop) if next_hop else None
    except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
      return "Error: Enter a valid IPv6 destination, prefix length, and next-hop address."
    routes = self.running.setdefault("ipv6_static_routes", [])
    if removing:
      remaining = [item for item in routes if not (
          item.get("destination") == str(network.network_address)
          and int(item.get("prefix", -1)) == network.prefixlen
          and (parsed_next_hop is None or item.get("next_hop") == str(parsed_next_hop))
      )]
      if len(remaining) == len(routes):
        return "Error: The specified IPv6 static route does not exist."
      self.running["ipv6_static_routes"] = remaining
      return ""
    if any(item.get("destination") == str(network.network_address)
           and int(item.get("prefix", -1)) == network.prefixlen
           and item.get("next_hop") == str(parsed_next_hop) for item in routes):
      return "Info: This IPv6 static route already exists."
    routes.append({"destination": str(network.network_address), "prefix": network.prefixlen,
                   "next_hop": str(parsed_next_hop), "preference": 60})
    return ""

  def _configure_ipv6_interface_address(self, command, active_ports):
    parts = command.split()
    if len(parts) == 4 and parts[2].lower() == "auto":
      return "Error: Use ipv6 address auto {link-local|global [default]}."
    if len(parts) == 5 and parts[2].lower() == "auto":
      return "Error: Use ipv6 address auto {link-local|global [default]}."
    if len(parts) == 3 and parts[2].lower().startswith("auto"):
      return "Error: Use ipv6 address auto {link-local|global [default]}."
    try:
      if len(parts) == 3:
        address = ipaddress.IPv6Interface(parts[2])
      elif len(parts) == 4:
        address = ipaddress.IPv6Interface(f"{parts[2]}/{parts[3]}")
      else:
        return "Error: Use ipv6 address <IPv6-address> <prefix-length>."
    except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
      return "Error: Enter a valid IPv6 address and prefix length."
    if address.ip.is_multicast or address.ip.is_unspecified:
      return "Error: Configure a valid unicast IPv6 address."
    for port in active_ports:
      iface = self.running["interfaces"][port]
      iface.setdefault("ipv6_addresses", [])
      iface["ipv6_addresses"] = [item for item in iface["ipv6_addresses"]
                                 if item.get("address") != str(address.ip)]
      iface["ipv6_addresses"].append({"address": str(address.ip), "prefix": address.network.prefixlen})
    return ""

  @staticmethod
  def _configure_ipv6_ra_prefix(command):
    parts = command.split()
    if len(parts) != 7:
      return "Error: Use ipv6 nd ra prefix <IPv6-prefix> <valid-lifetime> <preferred-lifetime>."
    try:
      network = ipaddress.IPv6Network(parts[4], strict=False)
      valid, preferred = int(parts[5]), int(parts[6])
      if network.prefixlen != 64 or valid < 0 or preferred < 0 or preferred > valid:
        raise ValueError
    except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
      return "Error: Use a valid /64 prefix; lifetimes must be non-negative and preferred <= valid."
    return {"prefix": str(network), "valid_lifetime": valid, "preferred_lifetime": preferred}

  @staticmethod
  def _parse_ospf_auth(command):
    match = re.fullmatch(r"authentication-mode\s+hmac-sha256\s+(\d+)\s+(?:cipher|plain)\s+(\S+)", command.strip(), re.I)
    if not match:
      return "Error: Use authentication-mode hmac-sha256 <key-id> cipher <password>."
    key_id, password = match.groups()
    if not 1 <= int(key_id) <= 255:
      return "Error: OSPF authentication key ID range is 1-255."
    return {"mode": "hmac-sha256", "key_id": int(key_id), "password": password}

  def _display_ip_routing_table(self, protocol=None):
    from modules.datacom_network import datacom_routing_table
    conn = get_connection()
    try:
      rows = datacom_routing_table(conn, self.username, self.node_name)
    finally:
      conn.close()
    if protocol == "ospf":
      rows = [row for row in rows if row["protocol"] in ("OSPF", "O_ASE")]
    lines = [f"Route Flags: R - relay, D - download to fib", "Routing Tables: Public", "         Destinations : {}        Routes : {}".format(len({str(row['network']) for row in rows}), len(rows)),
             "Destination/Mask    Proto   Pre  Cost      Flags  NextHop         Interface",
             "----------------------------------------------------------------------------"]
    for row in rows:
      flags = "D" if row["active"] else "I"
      lines.append(f"{str(row['network']):<19} {row['protocol']:<7} {row['preference']:<4} {row.get('cost', 0):<9} {flags:<6} {row['next_hop']:<15} {row['interface']}")
    if not rows:
      lines.append("(No active routes.)")
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _display_ospf_peer(self, brief=False):
    from modules.datacom_ospf import ospf_state
    conn = get_connection()
    try:
      state = ospf_state(conn, self.username)
    finally:
      conn.close()
    device = state["devices"].get(self.node_name)
    if not device:
      self.append_plain_text("Info: OSPF is not configured.")
      self._prompt()
      return
    rid = state["router_ids"].get(self.node_name, "0.0.0.0")
    process_id = next(iter(device["processes"]), "1")
    peers = state["neighbors"].get(self.node_name, [])
    lines = [f"OSPF Process {process_id} with Router ID {rid}"]
    if brief:
      lines.extend(["", "Peer Statistic Information", f"Total number of peer(s): {len(peers)}",
                    f"Peer(s) in full state: {len(peers)}", "-" * 77,
                    "Area Id Interface Neighbor id State"])
      lines.extend(f"{p['area']:<7} {p['interface']:<16} {p['router_id']:<16} Full" for p in peers)
      lines.append("-" * 77)
    else:
      for peer in peers:
        lines.extend(["", f"Area {peer['area']} interface {peer['interface']} ({peer['interface']})'s neighbors",
                      f"Router ID: {peer['router_id']} Address : {peer['address']}",
                      "State : Full Mode : Nbr is Master Priority: 1",
                      f"DR : {peer['router_id']} BDR : - MTU :1500",
                      "Dead timer due (in seconds) : 40", "Retrans timer interval :5",
                      "Neighbor up time : 00h00m30s", "Authentication Sequence :0"])
      if not peers:
        lines.append("No OSPF neighbor information.")
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _configure_dhcp_pool(self, command):
    import ipaddress
    low = command.strip().lower()
    pool = self.running["dhcp_pools"][self.active_dhcp_pool]
    match = re.fullmatch(r"network\s+(\S+)\s+mask\s+(\S+)", command.strip(), re.I)
    if match:
      try:
        network = ipaddress.IPv4Network(f"{match.group(1)}/{match.group(2)}", strict=False)
      except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
        return "Error: Enter a valid IPv4 network and mask."
      pool["network"], pool["mask"] = str(network.network_address), str(network.netmask)
      return ""
    for keyword, key in (("gateway-list", "gateway"), ("dns-list", "dns")):
      match = re.fullmatch(rf"{keyword}\s+(\S+)", command.strip(), re.I)
      if match:
        try:
          pool[key] = str(ipaddress.IPv4Address(match.group(1)))
        except ipaddress.AddressValueError:
          return f"Error: Enter a valid IPv4 address for {keyword}."
        return ""
    match = re.fullmatch(r"lease\s+day\s+(\d+)(?:\s+hour\s+(\d+))?(?:\s+minute\s+(\d+))?", low)
    if match:
      days, hours, minutes = (int(v or 0) for v in match.groups())
      if hours > 23 or minutes > 59 or not (days or hours or minutes):
        return "Error: Configure a positive DHCP lease period; hours must be 0-23 and minutes 0-59."
      pool["lease_seconds"] = days * 86400 + hours * 3600 + minutes * 60
      return ""
    if low == "lease unlimited":
      pool["lease_seconds"] = 0
      return ""
    match = re.fullmatch(r"static-bind\s+ip-address\s+(\S+)\s+mac-address\s+([0-9a-f:-]+)", command.strip(), re.I)
    if match:
      try:
        address = str(ipaddress.IPv4Address(match.group(1)))
      except ipaddress.AddressValueError:
        return "Error: Enter a valid IPv4 address for the static binding."
      mac = normalize_mac(match.group(2))
      if len(mac) != 12:
        return "Error: Enter a valid 12-digit MAC address."
      pool.setdefault("bindings", {})[mac] = address
      return ""
    if low.startswith("undo static-bind "):
      match = re.fullmatch(r"undo\s+static-bind\s+ip-address\s+(\S+)\s+mac-address\s+([0-9a-f:-]+)", command.strip(), re.I)
      if not match:
        return "Error: Use undo static-bind ip-address <ip> mac-address <mac>."
      mac = normalize_mac(match.group(2))
      if pool.setdefault("bindings", {}).get(mac) != match.group(1):
        return "Error: The specified static binding does not exist."
      del pool["bindings"][mac]
      return ""
    return "Error: Use network, gateway-list, dns-list, lease, or static-bind in the IP pool view."

  def _renew_dhcp_clients(self):
    from modules.datacom_dhcp import renew_clients
    conn = get_connection()
    try:
      renew_clients(conn, self.username)
      row = conn.execute("SELECT running_json FROM olt_datacom_devices WHERE username=? AND node_name=?", (self.username, self.node_name)).fetchone()
      if row:
        self.running = json.loads(row[0])
    finally:
      conn.close()

  def _load_running_after_dhcp(self):
    conn = get_connection()
    try:
      row = conn.execute("SELECT running_json FROM olt_datacom_devices WHERE username=? AND node_name=?", (self.username, self.node_name)).fetchone()
      if row:
        self.running = json.loads(row[0])
    finally:
      conn.close()

  def _display_dhcp_client(self, command):
    match = re.search(r"interface\s+GE\s*(\d+/\d+/\d+)", command, re.I)
    port = "GE" + match.group(1) if match else ""
    iface = self.running.get("interfaces", {}).get(port, {})
    lease = iface.get("dhcp_lease") or {}
    if iface.get("dhcp_mode") != "client":
      text = f"Info: DHCP client is not configured on {port or 'the specified interface'}."
    elif not lease:
      text = f"DHCP client lease information on interface {port}:\nCurrent machine state : Init\nInternet address allocated via : DHCP\nIP address : -"
    else:
      text = (f"DHCP client lease information on interface {port} :\n"
              f"Current machine state : {lease.get('state', 'Bound')}\nInternet address assigned via : DHCP\n"
              f"Physical address : {lease.get('mac', '-')}\nIP address : {lease['ip']}\n"
              f"Subnet mask : {lease['mask']}\nGateway ip address : {lease.get('gateway') or '-'}\n"
              f"DHCP server : {lease.get('server') or '-'}\nDNS : {lease.get('dns') or '-'}\n"
              f"Client identifier : {lease.get('mac', '-')}\nPool : {lease.get('pool', '-')}\n"
              f"Relay : {lease.get('relay') or '-'}")
    self.append_plain_text(text)
    self._prompt()

  def _display_ip_interface(self, command):
    parts = command.split()
    port = None
    if command.lower().endswith(" brief"):
      port = None
    elif len(parts) >= 4 and parts[3].lower() == "ge":
      port = "GE" + "".join(parts[4:])
    elif len(parts) >= 4:
      port = parts[3].upper() + "".join(parts[4:])
      port = next((name for name in self.running["interfaces"] if name.lower() == port.lower()), port)
    if port and port not in self.running["interfaces"]:
      self.append_plain_text("Error: The interface does not exist.")
    elif port:
      iface = self.running["interfaces"][port]
      lease = iface.get("dhcp_lease") or {}
      address = f"{iface.get('ip')}/{ipaddress_prefix(iface.get('mask'))}" if iface.get("ip") else "unassigned"
      source = "Internet Address is allocated by DHCP" if lease else (f"Internet Address is {address}" if iface.get("ip") else "Internet protocol processing : disabled")
      self.append_plain_text(f"{port} current state : {'DOWN' if iface.get('shutdown') else 'UP'}\nLine protocol current state : {'UP' if iface.get('ip') and not iface.get('shutdown') else 'DOWN'}\n{source}{', ' + address if lease else ''}")
    else:
      lines = ["Interface             IP Address/Mask      Physical   Protocol"]
      for name, iface in self.running["interfaces"].items():
        addr = f"{iface.get('ip')}/{ipaddress_prefix(iface.get('mask'))}" if iface.get("ip") else "unassigned"
        physical = "down" if iface.get("shutdown") else "up"
        protocol = "up" if iface.get("ip") and not iface.get("shutdown") else "down"
        lines.append(f"{name:<21} {addr:<20} {physical:<10} {protocol}")
      self.append_plain_text("\n".join(lines))
    self._prompt()

  def _display_ipv6_interface(self, command):
    import ipaddress
    from modules.datacom_ipv6 import interface_addresses
    parts = command.split()
    requested = None
    if len(parts) >= 4 and parts[3].lower() == "ge":
      requested = "GE" + "".join(parts[4:])
    elif len(parts) >= 4 and parts[3].lower() != "brief":
      requested = parts[3].upper() + "".join(parts[4:])
    if requested:
      requested = next((name for name in self.running["interfaces"] if name.lower() == requested.lower()), requested)
      if requested not in self.running["interfaces"]:
        self.append_plain_text("Error: The interface does not exist.")
        self._prompt()
        return
      selected = [(requested, self.running["interfaces"][requested])]
    else:
      selected = list(self.running["interfaces"].items())
    conn = get_connection()
    try:
      addresses = interface_addresses(conn, self.username)
    finally:
      conn.close()
    output = []
    brief = command.lower().endswith(" brief")
    if brief:
      output.append("Interface             Physical   Protocol   IPv6 Address")
    for name, iface in selected:
      rows = addresses.get((self.node_name, name), [])
      physical = "DOWN" if iface.get("shutdown") else "UP"
      if brief:
        address = next((item["address"] for item in rows if not ipaddress.IPv6Address(item["address"]).is_link_local), "unassigned")
        output.append(f"{name:<21} {physical:<10} {'UP' if iface.get('ipv6_enabled') and physical == 'UP' else 'DOWN':<10} {address}")
        continue
      output.extend([f"{name} current state : {physical}",
                     f"IPv6 protocol current state : {'UP' if iface.get('ipv6_enabled') and physical == 'UP' else 'DOWN'}"])
      link_local = next((item["address"] for item in rows if ipaddress.IPv6Address(item["address"]).is_link_local), None)
      output.append("IPv6 is enabled" + (f", link-local address is {link_local}" if link_local else ""))
      globals_ = [item for item in rows if not ipaddress.IPv6Address(item["address"]).is_link_local]
      if globals_:
        output.append("Global unicast address(es):")
        for item in globals_:
          subnet = ipaddress.IPv6Network(f"{item['address']}/{item['prefix']}", strict=False)
          suffix = f", subnet is {subnet} [{item['origin']}]"
          output.append(f"{item['address']}/{item['prefix']}{suffix}")
      else:
        output.append("No global unicast address configured")
      output.extend(["Joined group address(es):", "FF02::1", "FF02::2", "MTU is 1500 bytes",
                     "ND DAD is enabled, number of DAD attempts: 1",
                     "ND NUD is enabled, number of NUD attempts: 3",
                     "ND NUD interval is 5000 milliseconds", "ND reachable time is 1200000 milliseconds",
                     "ND stale time is 1200 seconds", "ND retransmit interval is 1000 milliseconds",
                     "ND RAs are " + ("halted" if iface.get("ipv6_ra_halt", True) else "enabled"),
                     "ND Proxy is disabled", ""])
    self.append_plain_text("\n".join(output).rstrip())
    self._prompt()

  def _display_ipv6_routing_table(self):
    from modules.datacom_ipv6 import ipv6_routing_table
    conn = get_connection()
    try:
      rows = ipv6_routing_table(conn, self.username, self.node_name)
    finally:
      conn.close()
    lines = ["Route Flags: R - relay, D - download to fib, T - to vpn-instance, B - black hole route",
             "Routing Table : _public_", f"Destinations : {len({str(item['destination']) for item in rows})} Routes : {len(rows)}",
             "Destination/Mask    Proto  Pre  Cost  Flags  NextHop         Interface"]
    lines.append("-" * 78)
    for item in rows:
      lines.extend([f"Destination : {item['destination'].network_address} PrefixLength : {item['destination'].prefixlen}",
                    f"NextHop : {item['next_hop']} Preference : {item['preference']}",
                    f"Cost : {item['cost']} Protocol : {item['protocol']}",
                    f"Interface : {item['interface']} Flags : {'D' if item['active'] else 'I'}", ""])
    if not rows:
      lines.append("(No IPv6 routes.)")
    self.append_plain_text("\n".join(lines).rstrip())
    self._prompt()

  def _display_ipv6_neighbors(self, command):
    parts = command.split()
    selected = None
    if len(parts) >= 5 and parts[3].lower() == "ge":
      selected = "GE" + "".join(parts[4:])
    elif len(parts) > 3:
      selected = parts[3].upper() + "".join(parts[4:])
    entries = [item for item in self.running.get("ipv6_neighbors", {}).values()
               if selected is None or item.get("interface", "").lower() == selected.lower()]
    lines = ["-" * 77]
    for item in entries:
      lines.extend([f"IPv6 Address : {item['address']}", f"Link-layer : {item['mac']} State : {item.get('state', 'REACH')}",
                    f"Interface : {item['interface']} Age : 1", "VLAN :- CEVLAN : -", "VPN name :- Is Router : TRUE",
                    "Secure FLAG : UN-SECURE Nickname : -", "Source IP :-", "Destination IP : -", "VNI :- BD :-", "",
                    "-" * 77])
    lines.append(f"Total: {len(entries)} Dynamic: {len(entries)} Static: 0 Remote: 0")
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _ping_ipv6(self, command):
    parts = command.split()
    if len(parts) == 3:
      destination, interface = parts[2], None
    elif len(parts) == 6 and parts[3].lower() == "-i" and parts[4].lower() == "ge":
      destination, interface = parts[2], "GE" + parts[5].replace(" ", "")
    else:
      self.append_plain_text("Error: Use ping ipv6 <destination> [-i GE <frame/slot/port>].")
      self._prompt()
      return
    conn = get_connection()
    try:
      from modules.datacom_ipv6 import ping_ipv6, remember_neighbors
      reachable, source_address, path = ping_ipv6(conn, self.username, self.node_name, destination, interface)
      if reachable:
        remember_neighbors(conn, self.username, self.node_name, path, destination)
    finally:
      conn.close()
    try:
      destination = str(ipaddress.IPv6Address(destination))
    except ipaddress.AddressValueError:
      self.append_plain_text("Error: Enter a valid IPv6 destination address.")
      self._prompt()
      return
    lines = [f"PING {destination} : 56 data bytes, press CTRL_C to break"]
    hop_limit = max(1, 64 - len(path)) if reachable else 64
    for sequence in range(1, 6):
      lines.append(f"Reply from {destination}\nbytes=56 Sequence={sequence} hop limit={hop_limit} time<1 ms" if reachable else "Request time out")
    lines.extend([f"--- {destination} ping statistics---", "5 packet(s) transmitted",
                  f"{5 if reachable else 0} packet(s) received", f"{0 if reachable else 100:.2f}% packet loss"])
    self.append_plain_text("\n".join(lines))
    self._load_running_after_dhcp()
    self._prompt()

  def _display_ip_pool(self, command):
    parts = command.split()
    name = parts[4] if len(parts) > 4 and parts[3].lower() == "name" else ""
    pool = self.running.get("dhcp_pools", {}).get(name)
    if not pool:
      self.append_plain_text(f"Error: DHCP address pool {name} does not exist.")
    else:
      leases = pool.get("leases", {})
      total = 0
      try:
        total = sum(1 for _ in ipaddress.IPv4Network(f"{pool['network']}/{pool['mask']}").hosts())
      except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
        pass
      lines = [f"Pool-name : {name}", "Position : Local", "Status : Unlocked",
               f"Gateway-0 : {pool.get('gateway') or '-'}", f"DNS-server0 : {pool.get('dns') or '-'}",
               f"Network : {pool.get('network') or '-'}", f"Mask : {pool.get('mask') or '-'}",
               f"Address Statistic: Total :{total} Used :{len(leases)}",
               "Index IP Client-ID Type Left Status"]
      lines.extend(f"{i} {info.get('ip')} {mac} DHCP - {info.get('state', 'Bound')}" for i, (mac, info) in enumerate(leases.items(), 1))
      self.append_plain_text("\n".join(lines))
    self._prompt()

  def _display_ip_pool_interface(self, command):
    match = re.search(r"interface\s+(Vlanif\s*\d+)", command, re.I)
    name = re.sub(r"\s+", "", match.group(1)) if match else ""
    iface = (self.running.get("interfaces") or {}).get(name)
    if not iface or not iface.get("is_vlanif") or iface.get("dhcp_select") != "interface":
      self.append_plain_text(f"Error: No interface address pool is configured on {name or 'the specified VLANIF'}.")
      self._prompt()
      return
    leases = {}
    for mac, info in (iface.get("dhcp_leases") or {}).items():
      if isinstance(info, dict) and info.get("ip"):
        leases[normalize_mac(mac)] = (info.get("ip"), info.get("client", "-"), "DHCP")
    conn = get_connection()
    try:
      from modules.datacom_wlan import ensure_schema
      ensure_schema(conn)
      rows = conn.execute("SELECT client_mac,ip_address,client_node,client_kind,ssid FROM olt_wlan_leases "
                          "WHERE username=? AND dhcp_node=? AND vlanif=?",
                          (self.username, self.node_name, name)).fetchall()
      for mac, address, client, kind, ssid in rows:
        kind_text = "AP" if kind == "ap" else ("WLAN" if kind == "station" else "DHCP")
        leases[normalize_mac(mac)] = (address, client, f"{kind_text}{':'+ssid if ssid else ''}")
    finally:
      conn.close()
    try:
      network = ipaddress.IPv4Network(f"{iface.get('ip')}/{iface.get('mask') or '255.255.255.0'}", strict=False)
      total = max(0, network.num_addresses - (2 if network.prefixlen < 31 else 0))
      if ipaddress.IPv4Address(iface["ip"]) in network.hosts():
        total = max(0, total - 1)
      first = next(network.hosts(), network.network_address)
      last = network.broadcast_address - 1 if network.prefixlen < 31 else network.broadcast_address
    except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
      self.append_plain_text(f"Error: {name} has no valid IPv4 address and mask.")
      self._prompt()
      return
    gateway = iface.get("dhcp_server_gateway") or iface.get("ip")
    lines = [f"Pool-name : {name}", "Pool-No : 0", "Lease : 1 Days 0 Hours 0 Minutes",
             "Domain-name : -"]
    if iface.get("dhcp_option_43"):
      lines.extend(["Option-code : 43", "Option-subcode : 2", "Option-type : ip-address",
                    f"Option-value : {iface['dhcp_option_43']}"])
    lines.extend(["DNS-server0 : " + (iface.get("dhcp_server_dns") or "-"), "Position : Interface",
                  "Status : Unlocked", f"Gateway-0 : {gateway}", f"Network : {network.network_address}",
                  f"Mask : {network.netmask}", "VPN instance : --", "Logging : Disable",
                  f"Address Statistic: Total :{total} Used :{len(leases)} Idle :{max(0, total-len(leases))} Expired :0 Conflict :0 Disabled :0",
                  "Network section", "Start              End                Total  Used  Idle(Expired)",
                  f"{first}   {last}   {total}   {len(leases)}   {max(0,total-len(leases))}(0)",
                  "Index   IP              Client-ID       Type   Left   Status"])
    lines.extend(f"{index:<7} {address:<15} {mac:<15} {kind:<6} 86399 Used"
                 for index, (mac, (address, _client, kind)) in enumerate(sorted(leases.items()), 1))
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _display_physical_interface(self, command):
    match = re.search(r"interface\s+GE\s*(\d+/\d+/\d+)", command, re.I)
    port = "GE" + match.group(1) if match else ""
    iface = self.running.get("interfaces", {}).get(port)
    if not iface:
      self.append_plain_text("Error: The interface does not exist.")
    else:
      from modules.datacom_dhcp import interface_mac
      mac = interface_mac(self.node_name, port)
      self.append_plain_text(f"{port} current state : {'DOWN' if iface.get('shutdown') else 'UP'}\nLine protocol current state : {'UP' if iface.get('ip') and not iface.get('shutdown') else 'DOWN'}\nDescription: {iface.get('description') or ''}\nRoute Port,The Maximum Transmit Unit is 1500,The Maximum Frame Length is 9216\nInternet Address is {iface.get('ip') + '/' + str(ipaddress_prefix(iface.get('mask'))) if iface.get('ip') else 'unassigned'}\nHardware address is {mac}\nSpeed: 1000, Duplex: FULL, Negotiation: ENABLE")
    self._prompt()

  def _display_stp(self, brief=False):
    from modules.datacom_network import stp_port_states
    conn = get_connection()
    try:
      states = stp_port_states(conn, self.username)
      rows = conn.execute("SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (self.username,)).fetchall()
      configs = {name: json.loads(raw) for name, _kind, raw in rows}
      configs[self.node_name] = self.running
      from modules.datacom_network import datacom_topology_links
      links, _lag_info = datacom_topology_links(conn, self.username)
    finally:
      conn.close()
    stp = self.running.get("stp", {})
    current_ports = []
    for a, b, ap, bp, up in links:
      if not up:
        continue
      if a == self.node_name:
        current_ports.append(ap)
      elif b == self.node_name:
        current_ports.append(bp)
    if brief:
      lines = [" MSTID  Port                        Role  STP State     Protection      Cost    Edged"]
      for port in sorted(set(current_ports)):
        state = states.get((self.node_name, port), {"role": "DESI", "state": "forwarding", "cost": 20000, "edge": False})
        iface = self.running["interfaces"].get(port, {})
        enabled = stp.get("enabled", True) and iface.get("stp_enabled", True)
        state_text = state["state"] if enabled else "forwarding"
        lines.append(f"     0  {port:<27} {state['role']:<5} {state_text:<13} none           {int(iface.get('stp_cost', state['cost'])):<8} {'enable' if iface.get('stp_edge') else 'disable'}")
      if not current_ports:
        lines.append("(No connected switch ports.)")
      self.append_plain_text("\n".join(lines))
      self._prompt()
      return

    all_switches = {}
    for name, config in configs.items():
      if name == self.node_name or "switch" in name.lower():
        current = config.get("stp", {})
        role = current.get("root_role", "")
        priority = 0 if role == "primary" else (4096 if role == "secondary" else int(current.get("priority", 32768)))
        mac = hashlib.md5(name.encode("utf-8")).hexdigest()[:12]
        all_switches[name] = (priority, mac)
    graph = {}
    for a, b, _ap, _bp, up in links:
      if up and a in all_switches and b in all_switches:
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    component, todo = set(), [self.node_name]
    while todo:
      node = todo.pop()
      if node in component:
        continue
      component.add(node)
      todo.extend(graph.get(node, set()))
    root = min((node for node in component if node in all_switches), key=lambda n: (all_switches[n][0], all_switches[n][1], n))
    local_priority, local_mac = all_switches[self.node_name]
    root_priority, root_mac = all_switches[root]
    root_port = next((port for port in current_ports if states.get((self.node_name, port), {}).get("role") == "ROOT"), "0.0")
    lines = [
        f"CIST Global Information:",
        f"  Mode                :{stp.get('mode', 'MSTP').upper()}",
        f"  CIST Bridge         :{local_priority}.{local_mac[:4]}-{local_mac[4:8]}-{local_mac[8:]}",
        "  Config Times        :Hello 2s MaxAge 20s FwDly 15s MaxHop 20",
        "  Active Times        :Hello 2s MaxAge 20s FwDly 15s MaxHop 20",
        f"  CIST Root/ERPC      :{root_priority}.{root_mac[:4]}-{root_mac[4:8]}-{root_mac[8:]} / {0 if root == self.node_name else 20000}" + (" (This bridge is the root)" if root == self.node_name else ""),
        f"  CIST RegRoot/IRPC   :{local_priority}.{local_mac[:4]}-{local_mac[4:8]}-{local_mac[8:]} / 0",
        f"  CIST RootPortId     :{root_port}",
        "  BPDU-Protection     :Disabled",
        f"  CIST Root Type      :{'Primary root' if stp.get('root_role') == 'primary' else ('Secondary root' if stp.get('root_role') == 'secondary' else 'Normal')}",
        "  STP Converge Mode   :Normal",
        "",
        "CIST Port Information:",
    ]
    for index, port in enumerate(sorted(set(current_ports)), 1):
      state = states.get((self.node_name, port), {"role": "DESI", "state": "forwarding"})
      protocol_enabled = stp.get("enabled", True) and self.running["interfaces"].get(port, {}).get("stp_enabled", True)
      port_state = state["state"] if protocol_enabled else "forwarding"
      lines.extend([f"  Port Id             :{index}", f"  Port Name           :{port}", f"  Port State          :{port_state.capitalize()}", f"  Port Protocol       :{'Enabled' if protocol_enabled else 'Disabled'}"])
    if not current_ports:
      lines.append("  (No connected switch ports.)")
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _display_interface(self):
    cfg = self.running["interfaces"][self.interface]
    body = f"#\ninterface {self.interface}"
    if cfg.get("port_link_type_removed"):
      body += "\n undo port link-type"
    if cfg.get("link_type") == "route":
      if not cfg.get("is_loopback") and not cfg.get("is_vlanif"):
        body += "\n undo portswitch"
    if cfg.get("ip"):
      body += f"\n ip address {cfg['ip']} {cfg['mask']}"
    if cfg.get("dhcp_mode") == "client":
      body += "\n ip address dhcp-alloc"
    if cfg.get("dhcp_select"):
      body += f"\n dhcp select {cfg['dhcp_select']}"
    if cfg.get("dhcp_relay_server_ip"):
      body += f"\n dhcp relay server-ip {cfg['dhcp_relay_server_ip']}"
    if int(cfg.get("ospf_cost", 1)) != 1:
      body += f"\n ospf cost {cfg['ospf_cost']}"
    if cfg.get("ospf_auth"):
      auth = cfg["ospf_auth"]
      body += f"\n ospf authentication-mode hmac-sha256 {auth['key_id']} cipher {auth['password']}"
    body += "\n#"
    self.append_plain_text(body)
    self._prompt()

  @staticmethod
  def _parse_vlan_list(text):
    values = []
    tokens = text.split()
    index = 0
    while index < len(tokens):
      first = int(tokens[index])
      if index + 2 < len(tokens) and tokens[index + 1].lower() == "to":
        last = int(tokens[index + 2])
        if last < first:
          raise ValueError
        values.extend(range(first, last + 1))
        index += 3
      else:
        values.append(first)
        index += 1
    return values

  def _configure_switch_port(self, config, command):
    low = command.lower()
    if low.startswith("port link-type "):
      link_type = low.split()[-1]
      if link_type not in ("access", "trunk", "hybrid"):
        return "Error: Link type must be access, trunk, or hybrid."
      config["link_type"] = link_type
      if link_type == "access":
        config["allowed_vlans"] = [int(config.get("pvid", 1))]
      elif link_type == "trunk":
        config.setdefault("allowed_vlans", [1])
      else:
        config.setdefault("untagged_vlans", [int(config.get("pvid", 1))])
        config.setdefault("tagged_vlans", [])
      return ""
    if low.startswith("port default vlan "):
      vlan_id = int(command.split()[-1])
      if vlan_id not in self.running.get("vlans", [1]):
        return f"Error: VLAN {vlan_id} does not exist."
      config["pvid"] = vlan_id
      config["allowed_vlans"] = [vlan_id]
      return ""
    if low.startswith("port trunk pvid vlan "):
      vlan_id = int(command.split()[-1])
      if vlan_id not in self.running.get("vlans", [1]):
        return f"Error: VLAN {vlan_id} does not exist."
      if config.get("link_type") != "trunk":
        return "Error: Configure port link-type trunk first."
      config["pvid"] = vlan_id
      return ""
    if low.startswith("port trunk allow-pass vlan ") or low.startswith("undo port trunk allow-pass vlan "):
      removing = low.startswith("undo ")
      tokens = command.split()
      offset = 5 if removing else 4
      try:
        vlan_ids = self._parse_vlan_list(" ".join(tokens[offset:]))
      except ValueError:
        return "Error: Invalid VLAN list."
      if any(v not in self.running.get("vlans", [1]) for v in vlan_ids):
        return "Error: Create the VLANs before adding them to the trunk."
      allowed = set(map(int, config.get("allowed_vlans", [1])))
      allowed.difference_update(vlan_ids) if removing else allowed.update(vlan_ids)
      config["allowed_vlans"] = sorted(allowed)
      return ""
    if low.startswith("port hybrid pvid vlan "):
      vlan_id = int(command.split()[-1])
      if vlan_id not in self.running.get("vlans", [1]):
        return f"Error: VLAN {vlan_id} does not exist."
      config["pvid"] = vlan_id
      config["untagged_vlans"] = sorted(set(config.get("untagged_vlans", [])) | {vlan_id})
      return ""
    if low.startswith("port hybrid untagged vlan ") or low.startswith("port hybrid tagged vlan "):
      tagged = " tagged " in f" {low} "
      prefix = "port hybrid tagged vlan " if tagged else "port hybrid untagged vlan "
      vlan_ids = self._parse_vlan_list(command[len(prefix):])
      if any(v not in self.running.get("vlans", [1]) for v in vlan_ids):
        return "Error: Create the VLANs before adding them to the hybrid port."
      key = "tagged_vlans" if tagged else "untagged_vlans"
      config[key] = sorted(set(map(int, config.get(key, []))) | set(vlan_ids))
      return ""
    if low.startswith("undo port hybrid untagged vlan ") or low.startswith("undo port hybrid tagged vlan "):
      tagged = " tagged " in f" {low} "
      phrase = "port hybrid tagged vlan " if tagged else "port hybrid untagged vlan "
      vlan_ids = self._parse_vlan_list(command[command.lower().index(phrase) + len(phrase):])
      key = "tagged_vlans" if tagged else "untagged_vlans"
      config[key] = [v for v in config.get(key, []) if int(v) not in vlan_ids]
      return ""
    return None

  def _display_vlans(self, command):
    filter_id = int(command.split()[2]) if len(command.split()) > 2 and command.split()[2].isdigit() else None
    lines = [f"The total number of vlans is : {len(self.running.get('vlans', [1]))}",
             "VID          Ports", "------------------------------------------------------------"]
    for vlan in sorted(set(map(int, self.running.get("vlans", [1])))):
      if filter_id is not None and vlan != filter_id:
        continue
      untagged, tagged = [], []
      ports = list(self.running["interfaces"].items()) + [
          (f"Eth-Trunk{trunk_id}", cfg) for trunk_id, cfg in self.running.get("eth_trunks", {}).items()
      ]
      for port, cfg in ports:
        link_type = cfg.get("link_type", "access")
        if link_type == "access" and int(cfg.get("pvid", 1)) == vlan:
          untagged.append(port)
        elif link_type == "trunk" and vlan in set(map(int, cfg.get("allowed_vlans", [1]))):
          tagged.append(port)
        elif link_type == "hybrid":
          if vlan in set(map(int, cfg.get("untagged_vlans", []))):
            untagged.append(port)
          if vlan in set(map(int, cfg.get("tagged_vlans", []))):
            tagged.append(port)
      lines.append(f"{vlan:>4}  UT:{', '.join(untagged) or '-'}")
      if tagged:
        lines.append(f"      TG:{', '.join(tagged)}")
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _display_mac_table(self):
    import hashlib
    lines = ["Flags: * - Backup", "MAC Address       VLAN   Learned-From   Type", "------------------------------------------------"]
    try:
      from modules.database import get_connection
      conn = get_connection()
      try:
        rows = conn.execute("SELECT source_node,target_node,source_port,target_port,is_up FROM olt_device_links WHERE username=?", (self.username,)).fetchall()
      finally:
        conn.close()
      learned = []
      for source, target, source_port, target_port, up in rows:
        if not up:
          continue
        if source == self.node_name:
          peer, port = target, source_port
        elif target == self.node_name:
          peer, port = source, target_port
        else:
          continue
        cfg = self.running["interfaces"].get(port, {})
        vlan = int(cfg.get("pvid", 1))
        mac = hashlib.md5(peer.encode("utf-8")).hexdigest()[:12]
        formatted = "-".join(mac[i:i + 4] for i in range(0, 12, 4))
        learned.append(f"{formatted:<17} {vlan:<6} {port:<14} dynamic")
      lines.extend(learned or ["(No dynamic MAC entries.)"])
    except Exception:
      lines.append("(MAC table unavailable.)")
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _ping(self, target):
    from modules.datacom_network import ping_datacom
    parts = target.split()
    source_address = None
    packet_count = 5
    destination = None
    index = 0
    try:
      while index < len(parts):
        option = parts[index].lower()
        if option == "-a" and index + 1 < len(parts) and source_address is None:
          source_address = str(ipaddress.IPv4Address(parts[index + 1]))
          index += 2
        elif option == "-c" and index + 1 < len(parts):
          packet_count = int(parts[index + 1])
          if not 1 <= packet_count <= 100: raise ValueError
          index += 2
        elif not option.startswith("-") and destination is None:
          destination = parts[index]
          index += 1
        else:
          raise ValueError
      if destination is None: raise ValueError
    except (ValueError, ipaddress.AddressValueError):
      self.append_plain_text("Error: Use ping [-a source-ip] [-c packet-count] <destination-ip>.")
      self._prompt(); return
    try:
      destination = str(ipaddress.IPv4Address(destination))
    except ipaddress.AddressValueError:
      self.append_plain_text("Error: Enter a valid destination IPv4 address.")
      self._prompt(); return
    result = ping_datacom(self.username, self.node_name, destination, source_address=source_address)
    address, reachable = result if result is not None else (destination, False)
    lines = [f"PING {address}: 56 data bytes, press CTRL_C to break"]
    lines.extend([f"Reply from {address}: bytes=56 Sequence={i} ttl=254 time<1 ms" if reachable else "Request time out" for i in range(1, packet_count + 1)])
    lines.extend(["", f"--- {address} ping statistics ---", f"{packet_count} packet(s) transmitted", f"{packet_count if reachable else 0} packet(s) received", f"{0 if reachable else 100:.2f}% packet loss"])
    self.append_plain_text("\n".join(lines))
    self._prompt()

  def _tracert(self, command):
    import ipaddress
    from modules.datacom_network import datacom_trace_path
    parts = command.split()
    if len(parts) == 4 and parts[1].lower() == "-a":
      source_address, destination = parts[2], parts[3]
      try:
        source_address = str(ipaddress.IPv4Address(source_address))
      except ipaddress.AddressValueError:
        self.append_plain_text("Error: Enter a valid source IPv4 address.")
        self._prompt()
        return
    elif len(parts) == 2:
      source_address, destination = None, parts[1]
    else:
      self.append_plain_text("Error: Use tracert [-a source-ip] <destination-ip>.")
      self._prompt()
      return
    try:
      destination = str(ipaddress.IPv4Address(destination))
    except ipaddress.AddressValueError:
      self.append_plain_text("Error: Enter a valid destination IPv4 address.")
      self._prompt()
      return
    conn = get_connection()
    try:
      path = datacom_trace_path(conn, self.username, self.node_name, destination, source_address=source_address)
    finally:
      conn.close()
    if path is None:
      self.append_plain_text(f"traceroute to {destination}, max hops: 64, packet length: 40\n  1  * * *\nFailure: Destination {destination} is unreachable.")
    else:
      lines = [f"traceroute to {destination}({destination}), max hops: 64, packet length: 40, press CTRL_C to break"]
      lines.extend(f"{index} {hop} <1 ms <1 ms <1 ms" for index, hop in enumerate(path, 1))
      lines.append(f"{len(path) + 1} {destination} <1 ms <1 ms <1 ms")
      self.append_plain_text("\n".join(lines))
    self._prompt()

  def _display_arp(self):
    import hashlib
    import ipaddress
    from modules.database import get_connection
    from modules.datacom_network import ping_datacom
    rows = ["ARP Entry Types: D - Dynamic, S - Static, I - Interface",
            "IP ADDRESS       MAC ADDRESS    TYPE        INTERFACE",
            "------------------------------------------------------"]
    own_interfaces = [(name, cfg) for name, cfg in self.running["interfaces"].items() if cfg.get("ip")]
    for ifname, iface in own_interfaces:
      mac = hashlib.md5((self.node_name + ifname).encode("utf-8")).hexdigest()[:12]
      rows.append(f"{iface['ip']:<16} {mac[0:4]}-{mac[4:8]}-{mac[8:12]}  I           {ifname}")
    conn = get_connection()
    try:
      devices = conn.execute("SELECT node_name,running_json FROM olt_datacom_devices WHERE username=? AND node_name<>?", (self.username, self.node_name)).fetchall()
    finally:
      conn.close()
    for peer, raw in devices:
      try:
        peer_config = json.loads(raw)
      except (TypeError, ValueError):
        continue
      for peer_if, peer_cfg in (peer_config.get("interfaces") or {}).items():
        if not peer_cfg.get("ip"):
          continue
        reachable = ping_datacom(self.username, self.node_name, peer_cfg["ip"])
        if not reachable or not reachable[1]:
          continue
        if not own_interfaces:
          continue
        local_name, local = own_interfaces[0]
        try:
          if ipaddress.IPv4Network(f"{local['ip']}/{local.get('mask') or '24'}", strict=False) != ipaddress.IPv4Network(f"{peer_cfg['ip']}/{peer_cfg.get('mask') or '24'}", strict=False):
            continue
        except ValueError:
          continue
        mac = hashlib.md5((peer + peer_if).encode("utf-8")).hexdigest()[:12]
        rows.append(f"{peer_cfg['ip']:<16} {mac[0:4]}-{mac[4:8]}-{mac[8:12]}  D           {local_name}")
    rows.append(f"Total: {len(rows) - 3}")
    self.append_plain_text("\n".join(rows))
    self._prompt()

  def _display_lldp_neighbors(self):
    from modules.datacom_network import datacom_topology_links
    conn = get_connection()
    try:
      links, _ = datacom_topology_links(conn, self.username)
      rows = conn.execute("SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (self.username,)).fetchall()
      configs = {}
      kinds = {}
      for name, kind, raw in rows:
        try:
          configs[name] = json.loads(raw or "{}")
          kinds[name] = kind or ""
        except (TypeError, ValueError):
          continue
    finally:
      conn.close()
    if not self.running.get("lldp_enabled", True):
      self.append_plain_text("LLDP is disabled on this device.")
      self._prompt()
      return
    output = ["Local Interface  Neighbor Device                 Neighbor Interface  Neighbor"]
    output.append("--------------------------------------------------------------------------")
    count = 0
    for a, b, ap, bp, up in links:
      if not up or self.node_name not in (a, b):
        continue
      peer = b if a == self.node_name else a
      local_port, peer_port = (ap, bp) if a == self.node_name else (bp, ap)
      if peer not in configs or not configs[peer].get("lldp_enabled", True):
        continue
      peer_type = kinds.get(peer, "")
      if "switch" in peer_type.lower():
        peer_type = "Huawei Switch"
      elif "router" in peer_type.lower():
        peer_type = "Huawei Router"
      output.append(f"{local_port:<16} {peer:<32} {peer_port:<20} {peer_type}")
      count += 1
    if not count:
      output.append("No LLDP neighbor information available.")
    self.append_plain_text("\n".join(output))
    self._prompt()

  def _display_config(self):
    lines = ["!", f"sysname {self.sysname}"]
    if self.running.get("dhcp_enabled"):
      lines.append("dhcp enable")
    if not self.running.get("lldp_enabled", True):
      lines.append("undo lldp enable")
    if not self.running.get("ztp_enabled", True):
      lines.append("set ztp disable")
    for number, acl in sorted(self.running.get("acls", {}).items(), key=lambda item: int(item[0])):
      lines.extend(["#", f"acl number {number}"])
      for rule in sorted(acl.get("rules", []), key=lambda item: item["sequence"]):
        command = f" rule {rule['sequence']} {rule['action']}"
        if acl.get("type", "advance") != "basic":
          command += f" {rule.get('protocol', 'ip')}"
        for field in ("source", "destination"):
          if rule.get(field):
            command += f" {field} {rule[field]} {rule.get(field + '_wildcard') or '0.0.0.0'}"
        lines.append(command)
    for name, classifier in sorted(self.running.get("traffic_classifiers", {}).items()):
      lines.extend(["#", f"traffic classifier {name} type {classifier.get('type', 'or')}"])
      if classifier.get("acl"):
        lines.append(f" if-match acl {classifier['acl']}")
    for name, behavior in sorted(self.running.get("traffic_behaviors", {}).items()):
      lines.extend(["#", f"traffic behavior {name}", f" {behavior.get('action', 'permit')}"])
    for name, policy in sorted(self.running.get("traffic_policies", {}).items()):
      lines.extend(["#", f"traffic policy {name}"])
      for entry in sorted(policy.get("classifiers", []), key=lambda item: item.get("precedence", 5)):
        lines.append(f" classifier {entry['classifier']} behavior {entry['behavior']} precedence {entry.get('precedence', 5)}")
    for pool_name, pool in sorted(self.running.get("dhcp_pools", {}).items()):
      lines.extend(["#", f"ip pool {pool_name}"])
      if pool.get("network") and pool.get("mask"):
        lines.append(f" network {pool['network']} mask {ipaddress_prefix(pool['mask'])}")
      if pool.get("dns"):
        lines.append(f" dns-list {pool['dns']}")
      if pool.get("gateway"):
        lines.append(f" gateway-list {pool['gateway']}")
      seconds = int(pool.get("lease_seconds", 86400))
      if seconds == 0:
        lines.append(" lease unlimited")
      elif seconds != 86400:
        days, remainder = divmod(seconds, 86400)
        hours, minutes = divmod(remainder, 3600)
        minutes //= 60
        lease = f" lease day {days}"
        if hours:
          lease += f" hour {hours}"
        if minutes:
          lease += f" minute {minutes}"
        lines.append(lease)
      for mac, address in sorted(pool.get("bindings", {}).items()):
        formatted_mac = "-".join((mac[i:i + 4] for i in range(0, 12, 4)))
        lines.append(f" static-bind ip-address {address} mac-address {formatted_mac}")
    if self.running.get("telnet_server_enabled"):
      lines.extend(["#", "telnet server enable", f"telnet server-source {self.running.get('telnet_server_source', 'all-interface')}"])
    if self.running.get("stelnet_server_enabled"):
      lines.extend(["#", "stelnet server enable", f"ssh server-source {self.running.get('ssh_server_source', 'all-interface')}"])
      if int(self.running.get("ssh_server_port", 22)) != 22:
        lines.append(f"ssh server port {self.running['ssh_server_port']}")
    if self.running.get("ssh_client_first_time_enabled"):
      lines.append("ssh client first-time enable")
    if self.running.get("ftp_server_enabled"):
      lines.extend(["#", "ftp server enable"])
      lines.append(f"ftp server source {self.running.get('ftp_server_source', 'all-interface')}")
      if self.running.get("ftp_server_acl"):
        lines.append(f"ftp server acl {self.running['ftp_server_acl']}")
    for name, scheme in sorted(self.running.get("authentication_schemes", {}).items()):
      lines.extend(["#", "aaa", f" authentication-scheme {name}", f"  authentication-mode {scheme.get('mode', 'local')}"])
    for name, scheme in sorted(self.running.get("authorization_schemes", {}).items()):
      lines.extend(["#", "aaa", f" authorization-scheme {name}", f"  authorization-mode {scheme.get('mode', 'local')}"])
    for name, scheme in sorted(self.running.get("accounting_schemes", {}).items()):
      lines.extend(["#", "aaa", f" accounting-scheme {name}", f"  accounting-mode {scheme.get('mode', 'none')}"])
    for name, domain in sorted(self.running.get("aaa_domains", {}).items()):
      lines.extend(["#", "aaa", f" domain {name}"])
      for field, command in (("authentication_scheme", "authentication-scheme"), ("authorization_scheme", "authorization-scheme"), ("accounting_scheme", "accounting-scheme")):
        if domain.get(field):
          lines.append(f"  {command} {domain[field]}")
    for name, ssh_user in sorted(self.running.get("ssh_users", {}).items()):
      lines.extend(["#", f"ssh user {name} authentication-type {ssh_user.get('authentication_type', 'password')}", f"ssh user {name} service-type {ssh_user.get('service_type', 'stelnet')}"])
    for name, user in sorted(self.running.get("aaa_users", {}).items()):
      digest = f"{user.get('password_salt', '')}${user.get('password_hash', '')}" if user.get("password_hash") else "<not-set>"
      lines.extend(["#", "aaa", f" local-user {name} password irreversible-cipher {digest}"])
      if user.get("service_types"):
        lines.append(f" local-user {name} service-type {' '.join(user['service_types'])}")
      if user.get("privilege"):
        lines.append(f" local-user {name} privilege level {user['privilege']}")
      lines.append(f" local-user {name} ftp-directory {user.get('ftp_directory', 'flash:/')} {' '.join(user.get('ftp_permissions', ['read', 'execute', 'write']))}")
    for key, vty in sorted(self.running.get("vty_interfaces", {}).items(), key=lambda item: int(item[1].get("start", 0))):
      lines.extend(["#", f"user-interface vty {vty.get('start', 0)} {vty.get('end', vty.get('start', 0))}", f" authentication-mode {vty.get('authentication_mode', 'aaa')}", f" protocol inbound {' '.join(vty.get('protocols', ['ssh']))}", f" user privilege level {vty.get('privilege', 3)}"])
    for name, group in sorted(self.running.get("nat_address_groups", {}).items()):
      lines.extend(["#", f"nat address-group {name} {group.get('id', 1)}", f" mode {group.get('mode', 'pat')}"])
      for section in sorted(group.get("sections", []), key=lambda item: int(item.get("id", 0))):
        lines.append(f" section {section['id']} {section['start']} {section['end']}")
    for name, server in sorted(self.running.get("nat_servers", {}).items()):
      lines.append(f"nat server {name} protocol {server['protocol']} global {server['global_address']} {server['global_port']} inside {server['inside_address']} {server['inside_port']}")
    if self.running.get("nat_policy_rules"):
      lines.extend(["#", "nat-policy"])
      for name, rule in sorted(self.running["nat_policy_rules"].items()):
        lines.append(f" rule name {name}")
        source = rule.get("source", {})
        if source.get("type") == "range":
          lines.append(f"  source-address range {source['start']} {source['end']}")
        elif source.get("network"):
          network = ipaddress.IPv4Network(source["network"], strict=False)
          lines.append(f"  source-address {network.network_address} {network.netmask}")
        if rule.get("action") == "address-group":
          lines.append(f"  action source-nat address-group {rule['address_group']}")
        elif rule.get("action") == "easy-ip":
          lines.append("  action source-nat easy-ip")
    if self.is_switch:
      stp = self.running.get("stp", {})
      if not stp.get("enabled", True):
        lines.append("stp disable")
      if stp.get("mode", "mstp") != "mstp":
        lines.append(f"stp mode {stp['mode']}")
      if stp.get("root_role"):
        lines.append(f"stp root {stp['root_role']}")
      elif int(stp.get("priority", 32768)) != 32768:
        lines.append(f"stp priority {stp['priority']}")
      if int(self.running.get("lacp_priority", 32768)) != 32768:
        lines.append(f"lacp priority {self.running['lacp_priority']}")
      lines.extend(f"vlan {vlan}" for vlan in sorted(set(map(int, self.running.get("vlans", [1])))) if vlan != 1)
      for trunk_id, trunk in sorted(self.running.get("eth_trunks", {}).items(), key=lambda item: int(item[0])):
        lines.extend(["#", f"interface Eth-Trunk{trunk_id}",
                      " mode lacp-static" if trunk.get("mode") == "lacp-static" else " mode manual load-balance",
                      f" lacp max active-linknumber {trunk.get('max_active', 128)}",
                      f" least active-linknumber {trunk.get('least_active', 1)}",
                      f" lacp preempt {'enable' if trunk.get('preempt') else 'disable'}",
                      f" load-balance {trunk.get('load_balance', 'src-dst-ip')}"])
        if trunk.get("link_type") == "trunk":
          if int(trunk.get("pvid", 1)) != 1:
            lines.append(f" port trunk pvid vlan {trunk['pvid']}")
          lines.append(" port trunk allow-pass vlan " + " ".join(map(str, trunk.get("allowed_vlans", [1]))))
        elif trunk.get("link_type") == "access":
          lines.extend([" port link-type access", f" port default vlan {trunk.get('pvid', 1)}"])
        elif trunk.get("link_type") == "hybrid":
          lines.extend([" port link-type hybrid", f" port hybrid pvid vlan {trunk.get('pvid', 1)}"])
          if trunk.get("untagged_vlans"):
            lines.append(" port hybrid untagged vlan " + " ".join(map(str, trunk["untagged_vlans"])))
          if trunk.get("tagged_vlans"):
            lines.append(" port hybrid tagged vlan " + " ".join(map(str, trunk["tagged_vlans"])))
    for process_id, process in sorted(self.running.get("ospf_processes", {}).items(), key=lambda item: int(item[0])):
      command = f"ospf {process_id}"
      if process.get("router_id"):
        command += f" router-id {process['router_id']}"
      if process.get("vpn_instance"):
        command += f" vpn-instance {process['vpn_instance']}"
      lines.extend(["#", command])
      if process.get("import_bgp"):
        imported = process["import_bgp"]
        lines.append(f" import-route bgp cost {imported.get('cost', 0)} type {imported.get('type', 2)}")
      if process.get("default_route_advertise", {}).get("enabled"):
        lines.append(" default-route-advertise" + (" always" if process["default_route_advertise"].get("always") else ""))
      if process.get("maximum_load_balancing"):
        lines.append(f" maximum load-balancing {process['maximum_load_balancing']}")
      if process.get("bfd_all_interfaces"):
        lines.append(" bfd all-interfaces enable")
      for area_id, area in sorted(process.get("areas", {}).items(), key=lambda item: int(item[0])):
        lines.extend([f" area {area_id}"])
        if area.get("export_ip_prefix"):
          lines.append(f"  filter ip-prefix {area['export_ip_prefix']} export")
        if area.get("authentication"):
          auth = area["authentication"]
          lines.append(f"  authentication-mode hmac-sha256 {auth['key_id']} cipher {auth['password']}")
        for statement in area.get("networks", []):
          lines.append(f"  network {statement['network']} {statement['wildcard']}")
      if process.get("frr", {}).get("loop_free_alternate"):
        lines.extend([" frr", "  loop-free-alternate"])
    for process_id, process in sorted(self.running.get("isis_processes", {}).items(), key=lambda item: int(item[0])):
      lines.extend(["#", f"isis {process_id}", f" is-level {process.get('level', 'level-1-2')}"])
      if process.get("cost_style"): lines.append(f" cost-style {process['cost_style']}")
      if process.get("is_name"): lines.append(f" is-name {process['is_name']}")
      lines.extend(f" network-entity {net}" for net in process.get("networks", []))
      if process.get("default_route_advertise"):
        lines.append(" default-route-advertise")
      if process.get("ipv6_enabled"):
        lines.append(" ipv6 enable topology ipv6")
      for item in process.get("ipv6_imports", []):
        lines.append(f" ipv6 import-route {item['protocol']}" + (f" {item['process']}" if item.get("process") else ""))
      if process.get("segment_routing_mpls"): lines.append(" segment-routing mpls")
      if process.get("srv6_locator"): lines.append(f" segment-routing ipv6 locator {process['srv6_locator']}")
      if process.get("traffic_eng_level"): lines.append(f" traffic-eng {process['traffic_eng_level']}")
      if process.get("srgb"): lines.append(f" segment-routing global-block {process['srgb'][0]} {process['srgb'][1]}")
    for process_id, process in sorted(self.running.get("ospfv3_processes", {}).items(), key=lambda item: int(item[0])):
      lines.extend(["#", f"ospfv3 {process_id}"])
      if process.get("router_id"):
        lines.append(f" router-id {process['router_id']}")
      for item in process.get("imports", []):
        lines.append(f" import-route {item['protocol']} {item['process']}")
    for name, entries in sorted(self.running.get("ip_prefix_lists", {}).items()):
      for entry in entries:
        line = f"ip ip-prefix {name} index {entry['index']} {entry['action']} {entry['network'].split('/')[0]} {entry['network'].split('/')[1]}"
        if entry.get("ge", int(entry['network'].split('/')[1])) != int(entry['network'].split('/')[1]):
          line += f" greater-equal {entry['ge']}"
        if entry.get("le", entry.get("ge", int(entry['network'].split('/')[1]))) != entry.get("ge", int(entry['network'].split('/')[1])):
          line += f" less-equal {entry['le']}"
        lines.append(line)
    for name, entries in sorted(self.running.get("community_filters", {}).items()):
      lines.extend(f"ip community-filter basic {name} index {entry['index']} {entry['action']} {entry['value']}" for entry in entries)
    for name, entries in sorted(self.running.get("as_path_filters", {}).items()):
      lines.extend(f"ip as-path-filter {name} {entry['action']} {entry['expression']}" for entry in entries)
    for name, nodes in sorted(self.running.get("route_policies", {}).items()):
      for node_id, node in sorted(nodes.items(), key=lambda item: int(item[0])):
        lines.extend(["#", f"route-policy {name} {node.get('action', 'permit')} node {node_id}"])
        if node.get("ip_prefix"): lines.append(f" if-match ip-prefix {node['ip_prefix']}")
        if node.get("community_filter"): lines.append(f" if-match community-filter {node['community_filter']}")
        if node.get("as_path_filter"): lines.append(f" if-match as-path-filter {node['as_path_filter']}")
        if node.get("community"): lines.append(f" apply community {node['community']} additive")
        lines.append("#")
    bgp = self.running.get("bgp")
    if self.running.get("mpls_lsr_id"):
      lines.append(f"mpls lsr-id {self.running['mpls_lsr_id']}")
    if self.running.get("mpls_enabled"):
      lines.append("mpls")
      if self.running.get("mpls_ldp_enabled"): lines.append(" mpls ldp")
      if self.running.get("mpls_te_enabled"): lines.append(" mpls te")
    sr_global = self.running.get("segment_routing") or {}
    if sr_global.get("enabled"):
      lines.extend(["#", "segment-routing"])
      for adjacency in sr_global.get("adjacency_sids", []):
        lines.append(f" ipv4 adjacency local-ip-addr {adjacency['local']} remote-ip-addr {adjacency['remote']} sid {adjacency['sid']}")
    srv6 = self.running.get("srv6") or {}
    if srv6.get("enabled"):
      lines.extend(["#", "segment-routing ipv6"])
      for locator_name, locator in sorted(srv6.get("locators", {}).items()):
        if locator.get("prefix"):
          lines.extend([f"#", f"locator {locator_name}",
                        f" ipv6-prefix {locator['prefix']} {locator.get('prefix_length', 64)}", " quit"])
      for segment_list_name, segments in sorted(srv6.get("segment_lists", {}).items()):
        lines.extend(["#", f"segment-list {segment_list_name}"])
        lines.extend(f" index {row['index']} sid ipv6 {row['sid']}" for row in segments)
        lines.append(" quit")
      for policy_name, policy in sorted(srv6.get("policies", {}).items()):
        lines.extend(["#", f"srv6-te policy {policy_name} endpoint {policy.get('endpoint', '::')} color {policy.get('color', 0)}"])
        for candidate in policy.get("candidates", []):
          lines.extend([f" candidate-path preference {candidate['preference']}"])
          if candidate.get("segment_list"): lines.append(f"  segment-list {candidate['segment_list']}")
        lines.append(" quit")
    sr_mpls = self.running.get("sr_mpls") or {}
    for path_name, segments in sorted(sr_mpls.get("explicit_paths", {}).items()):
      lines.extend(["#", f"explicit-path {path_name}"])
      lines.extend(f" next sid label {row['label']} type {row.get('type','prefix')}" for row in segments)
    if sr_mpls.get("segment_lists") or sr_mpls.get("policies"):
      lines.extend(["#", "segment-routing"])
      for list_name, segments in sorted(sr_mpls.get("segment_lists", {}).items()):
        lines.extend([f" segment-list name {list_name}"])
        lines.extend(f"  index {row['index']} mpls label {row['label']}" for row in segments)
        lines.append("  quit")
      for policy_name, policy in sorted(sr_mpls.get("policies", {}).items()):
        lines.append(f" sr-te policy {policy_name} endpoint {policy.get('endpoint','0.0.0.0')} color {policy.get('color',0)}")
        for candidate in policy.get("candidates", []):
          lines.extend([f"  candidate-path preference {candidate['preference']}"])
          if candidate.get("segment_list"): lines.append(f"   segment-list {candidate['segment_list']}")
          lines.append("   quit")
        lines.append(" quit")
    for policy_name, policy in sorted(self.running.get("tunnel_policies", {}).items()):
      lines.extend(["#", f"tunnel-policy {policy_name}"])
      for protocol in policy.get("protocols", []):
        command = f" tunnel select-seq {protocol}"
        if policy.get("load_balance"): command += f" load-balance-number {policy['load_balance']}"
        if policy.get("unmix"): command += " unmix"
        lines.append(command)
      lines.append(" quit")
    for vpn_name, vpn in sorted(self.running.get("vpn_instances", {}).items()):
      lines.extend(["#", f"ip vpn-instance {vpn_name}", " ipv4-family"])
      if vpn.get("rd"): lines.append(f"  route-distinguisher {vpn['rd']}")
      if vpn.get("tunnel_policy"): lines.append(f"  tnl-policy {vpn['tunnel_policy']}")
      if vpn.get("srv6_locator"): lines.append(f"  segment-routing ipv6 locator {vpn['srv6_locator']}")
      if vpn.get("srv6_service"): lines.append(f"  srv6 service-type {vpn['srv6_service']}")
      if vpn.get("srv6_policy"): lines.append(f"  segment-routing ipv6 policy {vpn['srv6_policy']}")
      if vpn.get("srv6_transport"): lines.append(f"  segment-routing ipv6 {vpn['srv6_transport']}")
      if vpn.get("default_color") is not None: lines.append(f"  default-color {vpn['default_color']}")
      if vpn.get("apply_label_per_instance"): lines.append("  apply-label per-instance")
      if vpn.get("disable_rt_filter"): lines.append("  undo policy vpn-target")
      for rt in vpn.get("import_rts", []): lines.append(f"  vpn-target {rt} import-extcommunity" + (" evpn" if vpn.get("evpn_rts") else ""))
      for rt in vpn.get("export_rts", []): lines.append(f"  vpn-target {rt} export-extcommunity" + (" evpn" if vpn.get("evpn_rts") else ""))
      if vpn.get("evpn_mpls_routing_enable"): lines.append("  evpn mpls routing-enable")
      lines.append("  quit")
      if vpn.get("vxlan_vni"): lines.append(f" vxlan vni {vpn['vxlan_vni']}")
      lines.append(" quit")
    if bgp:
      lines.extend(["#", f"bgp {bgp['as_number']}"])
      if bgp.get("router_id"): lines.append(f" router-id {bgp['router_id']}")
      for group, cfg in sorted(bgp.get("groups", {}).items()):
        lines.append(f" group {group} {cfg.get('type', 'internal')}")
        if cfg.get("connect_interface"): lines.append(f" peer {group} connect-interface {cfg['connect_interface']}")
        if cfg.get("next_hop_local"): lines.append(f" peer {group} next-hop-local")
        if cfg.get("advertise_community"): lines.append(f" peer {group} advertise-community")
      for peer, cfg in sorted(bgp.get("peers", {}).items()):
        if cfg.get("as_number") is not None: lines.append(f" peer {peer} as-number {cfg['as_number']}")
        if cfg.get("connect_interface"): lines.append(f" peer {peer} connect-interface {cfg['connect_interface']}")
        if cfg.get("password"): lines.append(f" peer {peer} password cipher {cfg['password']}")
        if cfg.get("valid_ttl_hops"): lines.append(f" peer {peer} valid-ttl-hops {cfg['valid_ttl_hops']}")
        if cfg.get("group"): lines.append(f" peer {peer} group {cfg['group']}")
        if cfg.get("next_hop_local"): lines.append(f" peer {peer} next-hop-local")
        if cfg.get("advertise_community"): lines.append(f" peer {peer} advertise-community")
        if cfg.get("import_prefix"): lines.append(f" peer {peer} ip-prefix {cfg['import_prefix']} import")
        if cfg.get("orf"): lines.append(f" peer {peer} capability-advertise orf ip-prefix {cfg['orf']}")
        for direction, policy_name in sorted(cfg.get("route_policy", {}).items()):
          lines.append(f" peer {peer} route-policy {policy_name} {direction}")
      for entry in bgp.get("networks", []):
        prefix = ipaddress.IPv4Network(f"{entry['network']}/{entry['mask']}", strict=False)
        lines.append(f" network {prefix.network_address} {prefix.prefixlen}")
      if bgp.get("ipv6_networks") or bgp.get("address_family", {}).get("ipv6"):
        lines.append(" ipv6-family unicast")
        for peer, cfg in sorted(bgp.get("peers", {}).items()):
          if cfg.get("address_families", {}).get("ipv6"):
            lines.append(f"  peer {peer} enable")
          if cfg.get("ipv6_reflect_client"):
            lines.append(f"  peer {peer} reflect-client")
          if cfg.get("ipv6_next_hop_local"):
            lines.append(f"  peer {peer} next-hop-local")
        for prefix in bgp.get("ipv6_networks", []):
          network = ipaddress.IPv6Network(prefix, strict=False)
          lines.append(f"  network {network.network_address} {network.prefixlen}")
        for item in bgp.get("ipv6_imports", []):
          lines.append(f"  import-route {item['protocol']}" + (f" {item['process']}" if item.get("process") else ""))
      if any(cfg.get("reflect_client") for cfg in bgp.get("groups", {}).values()):
        lines.append(" ipv4-family unicast")
        for group, cfg in sorted(bgp.get("groups", {}).items()):
          if cfg.get("reflect_client"): lines.append(f"  peer {group} reflect-client")
      if bgp.get("vpnv4_enabled"):
        lines.append(" ipv4-family vpnv4")
        if bgp.get("vpnv4_rt_filter_disabled"): lines.append("  undo policy vpn-target")
        for peer, cfg in sorted(bgp.get("peers", {}).items()):
          if cfg.get("vpnv4_enabled"): lines.append(f"  peer {peer} enable")
          group = cfg.get("group")
          if group and bgp.get("groups", {}).get(group, {}).get("reflect_client"):
            lines.append(f"  peer {peer} reflect-client")
        lines.append("#")
      if bgp.get("evpn_enabled"):
        lines.append(" l2vpn-family evpn")
        for peer, cfg in sorted(bgp.get("peers", {}).items()):
          if cfg.get("evpn_enabled"): lines.append(f"  peer {peer} enable")
          if cfg.get("evpn_reflect_client"): lines.append(f"  peer {peer} reflect-client")
        lines.append("#")
      for vpn_name, vpn in sorted(self.running.get("vpn_instances", {}).items()):
        if vpn.get("bgp_peers") or vpn.get("import_ospf"):
          lines.append(f" ipv4-family vpn-instance {vpn_name}")
          for peer, cfg in sorted(vpn.get("bgp_peers", {}).items()):
            if cfg.get("as_number") is not None: lines.append(f"  peer {peer} as-number {cfg['as_number']}")
            if cfg.get("connect_interface"): lines.append(f"  peer {peer} connect-interface {cfg['connect_interface']}")
            if cfg.get("substitute_as"): lines.append(f"  peer {peer} substitute-as")
            if cfg.get("allow_as_loop"): lines.append(f"  peer {peer} allow-as-loop {cfg['allow_as_loop']}")
            if cfg.get("enabled"): lines.append(f"  peer {peer} enable")
          for item in vpn.get("import_ospf", []):
            command = f"  import-route ospf {item['process']}"
            if item.get("route_policy"): command += f" route-policy {item['route_policy']}"
            lines.append(command)
          if vpn.get("advertise_l2vpn_evpn"): lines.append("  advertise l2vpn evpn")
    wlan = self.running.get("wlan", {})
    if any(wlan.get(key) for key in ("capwap_source_interface", "aps", "ap_groups", "regulatory_profiles", "security_profiles", "ssid_profiles", "vap_profiles")):
      lines.extend(["#", "wlan"])
      if wlan.get("capwap_source_interface"):
        lines.append(f" capwap source interface {wlan['capwap_source_interface']}")
      lines.append(f" ap auth-mode {wlan.get('ap_auth_mode', 'mac-auth')}")
      for name, profile in sorted(wlan.get("regulatory_profiles", {}).items()):
        lines.extend([f" regulatory-domain-profile name {name}", f"  country-code {profile.get('country_code', 'cn')}", "  quit"])
      for name, profile in sorted(wlan.get("security_profiles", {}).items()):
        lines.append(f" security-profile name {name}")
        if profile.get("passphrase"):
          lines.append(f"  security {profile.get('auth', 'wpa-wpa2')} psk pass-phrase {profile['passphrase']} {profile.get('cipher', 'aes')}")
        lines.append("  quit")
      for name, profile in sorted(wlan.get("ssid_profiles", {}).items()):
        lines.extend([f" ssid-profile name {name}"])
        if profile.get("ssid"): lines.append(f"  ssid {profile['ssid']}")
        lines.append("  quit")
      for name, profile in sorted(wlan.get("vap_profiles", {}).items()):
        lines.extend([f" vap-profile name {name}", f"  forward-mode {profile.get('forward_mode', 'direct-forward')}"])
        if profile.get("service_vlan"): lines.append(f"  service-vlan vlan-id {profile['service_vlan']}")
        for field in ("security_profile", "ssid_profile"):
          if profile.get(field): lines.append(f"  {field.replace('_', '-')} {profile[field]}")
        lines.append("  quit")
      for name, group in sorted(wlan.get("ap_groups", {}).items()):
        lines.append(f" ap-group name {name}")
        if group.get("regulatory_profile"): lines.append(f"  regulatory-domain-profile {group['regulatory_profile']}")
        for binding in group.get("vap_bindings", []):
          lines.append(f"  vap-profile {binding['vap_profile']} wlan {binding.get('wlan_id', 1)} radio {binding.get('radio', 'all')}")
        lines.append("  quit")
      for ap_id, ap in sorted(wlan.get("aps", {}).items(), key=lambda row: int(row[0])):
        lines.append(f" ap-id {ap_id} ap-mac {ap.get('mac_address', '')}")
        if ap.get("ap_name"): lines.append(f"  ap-name {ap['ap_name']}")
        if ap.get("ap_group"): lines.append(f"  ap-group {ap['ap_group']}")
        lines.append("  quit")
    for route in self.running.get("static_routes", []):
      lines.append(f"ip route-static {route['destination']} {route['mask']} {route['next_hop']}")
    for route in self.running.get("ipv6_static_routes", []):
      lines.append(f"ipv6 route-static {route['destination']} {route['prefix']} {route['next_hop']}")
    for name, evpn in sorted(self.running.get("evpn_instances", {}).items()):
      lines.extend(["#", f"evpn vpn-instance {name} bd-mode"])
      if evpn.get("rd"):
        lines.append(f" route-distinguisher {evpn['rd']}")
      for rt in evpn.get("export_rts", []):
        lines.append(f" vpn-target {rt} export-extcommunity")
      for rt in evpn.get("import_rts", []):
        lines.append(f" vpn-target {rt} import-extcommunity")
    for bd_id, bd in sorted(self.running.get("bridge_domains", {}).items(), key=lambda row: int(row[0])):
      lines.extend(["#", f"bridge-domain {bd_id}"])
      if bd.get("vxlan_vni"):
        lines.append(f" vxlan vni {bd['vxlan_vni']}")
      if bd.get("evpn_instance"):
        lines.append(f" evpn binding vpn-instance {bd['evpn_instance']}")
    if self.running.get("vcmp_role"):
      lines.append(f"vcmp role {self.running['vcmp_role']}")
    campus = self.running.get("campus") or {}
    if campus.get("fabric"):
      suffix = f" as {campus['as_number']}" if campus.get("as_number") else ""
      lines.append(f"campus fabric {campus['fabric']} role {campus.get('role') or 'extended'}{suffix}")
    for vn_name, vn in sorted(campus.get("vns", {}).items()):
      gateway = f" gateway {vn['gateway']}" if vn.get("gateway") else ""
      lines.append(f"campus vn {vn_name} bd {vn['bd']} vni {vn['vni']} vlan {vn['vlan']}{gateway}")
    nve = self.running.get("nve", {})
    if nve.get("source") or nve.get("vnis"):
      lines.extend(["#", "interface Nve1"])
      if nve.get("source"):
        lines.append(f" source {nve['source']}")
      for vni, cfg in sorted(nve.get("vnis", {}).items(), key=lambda row: int(row[0])):
        if cfg.get("protocol") == "bgp" and not cfg.get("peers"):
          lines.append(f" vni {vni} head-end peer-list protocol bgp")
        for peer in cfg.get("peers", []):
          suffix = " protocol bgp" if cfg.get("protocol") == "bgp" else ""
          lines.append(f" vni {vni} head-end peer-list {peer}{suffix}")
    for name, cfg in self.running["interfaces"].items():
      ipv6_configured = (cfg.get("ipv6_enabled") or cfg.get("ipv6_addresses") or cfg.get("ipv6_link_local_auto")
                         or cfg.get("ipv6_auto_global") or cfg.get("ipv6_ra_prefix") or not cfg.get("ipv6_ra_halt", True))
      if cfg.get("ip") or cfg.get("description") or self.is_switch or cfg.get("dhcp_mode") or cfg.get("dhcp_select") or (self.is_router and cfg.get("link_type") == "route") or ipv6_configured or cfg.get("traffic_policies") or cfg.get("nat_enabled") or cfg.get("subinterface") or cfg.get("vpn_instance") or cfg.get("mpls_enabled") or cfg.get("is_tunnel"):
        lines.extend(["#", f"interface {name}"])
        if cfg.get("link_type") == "route" and not cfg.get("is_loopback") and not cfg.get("is_vlanif"):
          if self.is_switch and cfg.get("port_link_type_removed"):
            lines.append(" undo port link-type")
          lines.append(" undo portswitch")
        if self.is_switch:
          if cfg.get("eth_trunk") is not None:
            lines.append(f" eth-trunk {cfg['eth_trunk']}")
          if int(cfg.get("lacp_priority", 32768)) != 32768:
            lines.append(f" lacp priority {cfg['lacp_priority']}")
          if cfg.get("link_type") == "access":
            lines.append(" port link-type access")
            lines.append(f" port default vlan {cfg.get('pvid', 1)}")
          elif cfg.get("link_type") == "trunk":
            lines.append(" port link-type trunk")
            if int(cfg.get("pvid", 1)) != 1:
              lines.append(f" port trunk pvid vlan {cfg['pvid']}")
            lines.append(" port trunk allow-pass vlan " + " ".join(map(str, cfg.get("allowed_vlans", [1]))))
          elif cfg.get("link_type") == "hybrid":
            lines.append(" port link-type hybrid")
            lines.append(f" port hybrid pvid vlan {cfg.get('pvid', 1)}")
            if cfg.get("untagged_vlans"):
              lines.append(" port hybrid untagged vlan " + " ".join(map(str, cfg["untagged_vlans"])))
            if cfg.get("tagged_vlans"):
              lines.append(" port hybrid tagged vlan " + " ".join(map(str, cfg["tagged_vlans"])))
          if not cfg.get("stp_enabled", True):
            lines.append(" stp disable")
          if int(cfg.get("stp_cost", 20000)) != 20000:
            lines.append(f" stp cost {cfg['stp_cost']}")
          if int(cfg.get("stp_port_priority", 128)) != 128:
            lines.append(f" stp port priority {cfg['stp_port_priority']}")
          if cfg.get("stp_edge"):
            lines.append(" stp edged-port enable")
        if cfg.get("description"):
          lines.append(f" description {cfg['description']}")
        if cfg.get("vpn_instance"):
          lines.append(f" ip binding vpn-instance {cfg['vpn_instance']}")
        if cfg.get("subinterface") and cfg.get("mode") == "l2":
          lines.append(" mode l2")
          if cfg.get("vlan_type") == "dot1q" and cfg.get("vlan_id"):
            lines.append(f" encapsulation dot1q vid {cfg['vlan_id']}")
          if cfg.get("bridge_domain"):
            lines.append(f" bridge-domain {cfg['bridge_domain']}")
        elif cfg.get("vlan_type") == "dot1q" and cfg.get("vlan_id"):
          lines.append(f" vlan-type dot1q {cfg['vlan_id']}")
        if cfg.get("mpls_enabled"): lines.append(" mpls")
        if cfg.get("mpls_ldp_enabled"): lines.append(" mpls ldp")
        if cfg.get("shutdown"):
          lines.append(" shutdown")
        if cfg.get("dhcp_mode") == "client":
          lines.append(" ip address dhcp-alloc")
        elif cfg.get("ip"):
          lines.append(f" ip address {cfg['ip']} {cfg['mask']}")
        if cfg.get("is_vbdif"):
          lines.append(f" mac-address {cfg.get('mac_address', '0000-5e00-0110')}")
          if cfg.get("arp_collect_host"):
            lines.append(" arp collect host enable")
        if cfg.get("dhcp_select"):
          lines.append(f" dhcp select {cfg['dhcp_select']}")
        if cfg.get("dhcp_server_gateway"):
          lines.append(f" dhcp server gateway-list {cfg['dhcp_server_gateway']}")
        if cfg.get("dhcp_option_43"):
          lines.append(f" dhcp server option 43 sub-option 2 ip-address {cfg['dhcp_option_43']}")
        if cfg.get("dhcp_relay_server_ip"):
          lines.append(f" dhcp relay server-ip {cfg['dhcp_relay_server_ip']}")
        if cfg.get("nat_enabled"):
          lines.append(" nat enable")
        for direction, policy_name in sorted(cfg.get("traffic_policies", {}).items()):
          lines.append(f" traffic-policy {policy_name} {direction}")
        if cfg.get("ipv6_enabled"):
          lines.append(" ipv6 enable")
        if cfg.get("ipv6_link_local_auto"):
          lines.append(" ipv6 address auto link-local")
        if cfg.get("ipv6_auto_global"):
          lines.append(" ipv6 address auto global" + (" default" if cfg.get("ipv6_auto_global_default") else ""))
        for address in cfg.get("ipv6_addresses", []):
          lines.append(f" ipv6 address {address['address']} {address['prefix']}")
        if cfg.get("ipv6_ra_prefix"):
          ra = cfg["ipv6_ra_prefix"]
          lines.append(f" ipv6 nd ra prefix {ra['prefix']} {ra.get('valid_lifetime', 3600)} {ra.get('preferred_lifetime', 1200)}")
        if cfg.get("ipv6_enabled") and not cfg.get("ipv6_ra_halt", True):
          lines.append(" ipv6 nd ra halt disable")
        if int(cfg.get("ospf_cost", 1)) != 1:
          lines.append(f" ospf cost {cfg['ospf_cost']}")
        if cfg.get("ospf_enabled"):
          lines.append(f" ospf enable {cfg.get('ospf_process', '1')} area {cfg.get('ospf_area', '0')}")
        if cfg.get("ospf_bfd"):
          lines.append(" ospf bfd enable")
        if cfg.get("ospf_bfd_params"):
          params = cfg["ospf_bfd_params"]
          lines.append(f" ospf bfd min-tx-interval {params['tx']} min-rx-interval {params['rx']} detect-multiplier {params['multiplier']}")
        if cfg.get("isis_process") is not None:
          lines.append(f" isis enable {cfg['isis_process']}")
        if cfg.get("isis_prefix_sid_index"):
          lines.append(f" isis prefix-sid index {cfg['isis_prefix_sid_index']}")
        if cfg.get("is_tunnel"):
          if cfg.get("unnumbered_interface"): lines.append(f" ip address unnumbered interface {cfg['unnumbered_interface']}")
          if cfg.get("destination"): lines.append(f" destination {cfg['destination']}")
          lines.extend([" tunnel-protocol mpls te", " mpls te signal-protocol segment-routing"])
          if cfg.get("tunnel_id"): lines.append(f" mpls te tunnel-id {cfg['tunnel_id']}")
          if cfg.get("explicit_path"): lines.append(f" mpls te path explicit-path {cfg['explicit_path']}")
        if cfg.get("isis_ipv6_process") is not None:
          lines.append(f" isis ipv6 enable {cfg['isis_ipv6_process']}")
        if cfg.get("ospfv3_process") is not None:
          lines.append(f" ospfv3 {cfg['ospfv3_process']} area {cfg.get('ospfv3_area', '0')} instance {cfg.get('ospfv3_instance', 1)}")
        if cfg.get("ospf_auth"):
          auth = cfg["ospf_auth"]
          lines.append(f" ospf authentication-mode hmac-sha256 {auth['key_id']} cipher {auth['password']}")
    lines.append("#")
    self.append_plain_text("\n".join(lines))
    self._prompt()
