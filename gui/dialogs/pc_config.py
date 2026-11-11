import ipaddress
import json
import socket
import subprocess

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)
from modules.database import get_connection
from modules import services
from gui.models.node import NetworkNodeItem


class PCConfigDialog(QDialog):
  """Configure a simulated PC's static IPv4 settings."""

  def __init__(self, node_item, parent=None):
    super().__init__(parent)
    self.node_item = node_item
    self.setWindowTitle(f"PC Network Settings - {node_item.device_name}")
    self.setMinimumWidth(360)

    layout = QVBoxLayout(self)
    layout.addWidget(QLabel("Configure the PC's static IPv4 address."))
    form = QFormLayout()
    self.ip_address = QLineEdit(node_item.pc_config.get("ip_address", ""))
    self.subnet_mask = QLineEdit(node_item.pc_config.get("subnet_mask", ""))
    self.gateway = QLineEdit(node_item.pc_config.get("gateway", ""))
    self.ip_address.setPlaceholderText("192.168.10.10")
    self.subnet_mask.setPlaceholderText("255.255.255.0 or 24")
    self.gateway.setPlaceholderText("192.168.10.1")
    form.addRow("IP address:", self.ip_address)
    form.addRow("Subnet mask:", self.subnet_mask)
    form.addRow("Default gateway:", self.gateway)
    layout.addLayout(form)

    wired_group = QGroupBox("Wired LAN (DHCP)")
    wired_form = QFormLayout(wired_group)
    self.wired_status = QLabel("No DHCP lease")
    wired_form.addRow("Status:", self.wired_status)
    wired_buttons = QHBoxLayout()
    self.wired_dhcp_button = QPushButton("Obtain DHCP")
    self.wired_dhcp_button.clicked.connect(self.obtain_wired_dhcp)
    self.wired_release_button = QPushButton("Release")
    self.wired_release_button.clicked.connect(self.release_wired_dhcp)
    wired_buttons.addWidget(self.wired_dhcp_button)
    wired_buttons.addWidget(self.wired_release_button)
    wired_form.addRow(wired_buttons)
    layout.addWidget(wired_group)

    ping_form = QHBoxLayout()
    self.ping_target = QLineEdit(node_item.pc_config.get("gateway", ""))
    self.ping_target.setPlaceholderText("Destination IPv4 address")
    self.ping_button = QPushButton("Ping")
    self.ping_button.clicked.connect(self.run_ping)
    ping_form.addWidget(QLabel("Ping target:"))
    ping_form.addWidget(self.ping_target, 1)
    ping_form.addWidget(self.ping_button)
    layout.addLayout(ping_form)
    self.ping_output = QTextEdit()
    self.ping_output.setReadOnly(True)
    self.ping_output.setMaximumHeight(150)
    self.ping_output.setPlaceholderText("Ping results will appear here.")
    layout.addWidget(self.ping_output)

    wlan_group = QGroupBox("WLAN client (Fit AP)")
    wlan_form = QFormLayout(wlan_group)
    self.wlan_network = QComboBox()
    self.wlan_password = QLineEdit()
    self.wlan_password.setEchoMode(QLineEdit.EchoMode.Password)
    self.wlan_status = QLabel("Disconnected")
    wlan_form.addRow("Available network:", self.wlan_network)
    wlan_form.addRow("Password:", self.wlan_password)
    wlan_form.addRow("Status:", self.wlan_status)
    wlan_buttons = QHBoxLayout()
    self.wlan_scan_button = QPushButton("Scan")
    self.wlan_scan_button.clicked.connect(self.refresh_wlan)
    self.wlan_connect_button = QPushButton("Connect")
    self.wlan_connect_button.clicked.connect(self.connect_wlan)
    self.wlan_disconnect_button = QPushButton("Disconnect")
    self.wlan_disconnect_button.clicked.connect(self.disconnect_wlan)
    wlan_buttons.addWidget(self.wlan_scan_button)
    wlan_buttons.addWidget(self.wlan_connect_button)
    wlan_buttons.addWidget(self.wlan_disconnect_button)
    wlan_form.addRow(wlan_buttons)
    layout.addWidget(wlan_group)

    # PPPoE client: PC dial melalui ONT bridge ke BRAS
    pppoe_group = QGroupBox("PPPoE Client (dial from this PC)")
    pppoe_form = QFormLayout()
    self.pppoe_user = QLineEdit()
    self.pppoe_user.setPlaceholderText("budi@isp")
    self.pppoe_pass = QLineEdit()
    self.pppoe_pass.setEchoMode(QLineEdit.EchoMode.Password)
    self.pppoe_status = QLabel("<font color='gray'>Belum dikonfigurasi</font>")
    pppoe_form.addRow("Username:", self.pppoe_user)
    pppoe_form.addRow("Password:", self.pppoe_pass)
    pppoe_form.addRow("Status:", self.pppoe_status)
    pppoe_buttons = QHBoxLayout()
    self.btn_save_pppoe = QPushButton("Simpan")
    self.btn_save_pppoe.clicked.connect(lambda: self.save_pppoe())
    self.btn_connect_pppoe = QPushButton("Connect")
    self.btn_connect_pppoe.clicked.connect(lambda: self.connect_pppoe())
    self.btn_disconnect_pppoe = QPushButton("Disconnect")
    self.btn_disconnect_pppoe.clicked.connect(lambda: self.disconnect_pppoe())
    pppoe_buttons.addWidget(self.btn_save_pppoe)
    pppoe_buttons.addWidget(self.btn_connect_pppoe)
    pppoe_buttons.addWidget(self.btn_disconnect_pppoe)
    pppoe_form.addRow(pppoe_buttons)
    pppoe_group.setLayout(pppoe_form)
    layout.addWidget(pppoe_group)

    self.error_label = QLabel("")
    self.error_label.setStyleSheet("color: #c62828;")
    layout.addWidget(self.error_label)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(self.save_settings)
    buttons.rejected.connect(self.reject)
    layout.addWidget(buttons)
    self.refresh_pppoe()
    self.refresh_wired_dhcp()
    self.refresh_wlan()

  def refresh_wlan(self):
    from modules.datacom_wlan import scan_networks, station_info
    self.wlan_network.clear()
    scene = self.node_item.scene()
    conn = get_connection()
    try:
      networks = scan_networks(conn, "admin", scene)
      station = station_info(conn, "admin", self.node_item.device_name)
    finally:
      conn.close()
    for network in networks:
      label = f"{network['ssid']} — {network['ap_name']} ({network['ap_node']})"
      self.wlan_network.addItem(label, network)
    if station:
      self.wlan_status.setText(f"Connected to {station['ssid']} via {station['ap_node']} — {station['ip']}")
      self.node_item.pc_config.update({"ip_address": station["ip"], "subnet_mask": "255.255.255.0"})
      # Restore the exact DHCP mask/gateway from the configured WAC VLANIF.
      config_conn = get_connection()
      try:
        row = config_conn.execute("SELECT running_json FROM olt_datacom_devices WHERE username=? AND node_name=?", ("admin", station.get("dhcp_node") or station["wac"])).fetchone()
        cfg = json.loads(row[0]) if row else {}
        iface = (cfg.get("interfaces") or {}).get(station["vlanif"], {})
        self.node_item.pc_config.update({"subnet_mask": iface.get("mask", "255.255.255.0"), "gateway": iface.get("ip", "")})
      except (TypeError, ValueError):
        pass
      finally:
        config_conn.close()
      self.ip_address.setText(self.node_item.pc_config["ip_address"])
      self.subnet_mask.setText(self.node_item.pc_config["subnet_mask"])
      self.gateway.setText(self.node_item.pc_config["gateway"])
      self.ping_target.setText(self.node_item.pc_config["gateway"])
    else:
      self.wlan_status.setText("Disconnected")
    return networks

  def refresh_wired_dhcp(self):
    from modules.datacom_wlan import wired_pc_info
    conn = get_connection()
    try:
      lease = wired_pc_info(conn, "admin", self.node_item.device_name)
      if lease:
        row = conn.execute("SELECT running_json FROM olt_datacom_devices WHERE username=? AND node_name=?",
                           ("admin", lease["dhcp_node"])).fetchone()
        cfg = json.loads(row[0]) if row else {}
        iface = (cfg.get("interfaces") or {}).get(lease["vlanif"], {})
    except (TypeError, ValueError):
      lease, iface = None, {}
    finally:
      conn.close()
    if lease:
      self.node_item.pc_config.update({"ip_address": lease["ip"], "subnet_mask": iface.get("mask", "255.255.255.0"),
                                       "gateway": iface.get("dhcp_server_gateway") or iface.get("ip", "")})
      self.ip_address.setText(self.node_item.pc_config["ip_address"])
      self.subnet_mask.setText(self.node_item.pc_config["subnet_mask"])
      self.gateway.setText(self.node_item.pc_config["gateway"])
      self.wired_status.setText(f"Bound to {lease['dhcp_node']} {lease['vlanif']}: {lease['ip']}")
    return lease

  def obtain_wired_dhcp(self):
    from modules.datacom_wlan import acquire_wired_pc, mac_for_node
    conn = get_connection()
    try:
      result = acquire_wired_pc(conn, "admin", self.node_item,
                                mac_for_node(self.node_item.device_name, "001b"),
                                self.node_item.scene())
    finally:
      conn.close()
    if not result.get("ok"):
      self.wired_status.setText(result.get("message", "DHCP request failed."))
      return False
    self.node_item.pc_config.update({"ip_address": result["ip"], "subnet_mask": result["mask"], "gateway": result["gateway"]})
    self.node_item.update()
    self.ip_address.setText(result["ip"])
    self.subnet_mask.setText(result["mask"])
    self.gateway.setText(result["gateway"])
    self.ping_target.setText(result["gateway"])
    self.wired_status.setText(f"Bound to {result['dhcp_node']} {result['vlanif']}: {result['ip']}")
    return True

  def release_wired_dhcp(self):
    from modules.datacom_wlan import release_pc
    conn = get_connection()
    try:
      release_pc(conn, "admin", self.node_item.device_name)
    finally:
      conn.close()
    self.node_item.pc_config.update({"ip_address": "", "subnet_mask": "", "gateway": ""})
    self.node_item.update()
    self.ip_address.clear(); self.subnet_mask.clear(); self.gateway.clear()
    self.wired_status.setText("DHCP lease released")
    return True

  def connect_wlan(self):
    network = self.wlan_network.currentData()
    if not network:
      self.wlan_status.setText("No WLAN is available. Check AP registration, VLAN trunk, and DHCP.")
      return False
    from modules.datacom_wlan import connect_station, mac_for_node
    conn = get_connection()
    try:
      result = connect_station(conn, "admin", self.node_item.device_name, mac_for_node(self.node_item.device_name, "001b"),
                               network["ap_node"], network["ssid"], self.wlan_password.text(), self.node_item.scene())
    finally:
      conn.close()
    if not result.get("ok"):
      self.wlan_status.setText(result.get("message", "WLAN connection failed."))
      return False
    self.node_item.pc_config.update({"ip_address": result["ip"], "subnet_mask": result["mask"], "gateway": result["gateway"]})
    self.node_item.update()
    self.ip_address.setText(result["ip"])
    self.subnet_mask.setText(result["mask"])
    self.gateway.setText(result["gateway"])
    self.ping_target.setText(result["gateway"])
    self.wlan_status.setText(f"Connected to {result['ssid']} via {result['ap_name']} — {result['ip']}")
    return True

  def disconnect_wlan(self):
    from modules.datacom_wlan import disconnect_station
    conn = get_connection()
    try: disconnect_station(conn, "admin", self.node_item.device_name)
    finally: conn.close()
    self.node_item.pc_config.update({"ip_address":"", "subnet_mask":"", "gateway":""})
    self.node_item.update()
    self.ip_address.clear(); self.subnet_mask.clear(); self.gateway.clear()
    self.wlan_status.setText("Disconnected")
    return True

  # ------------------------------------------------------------------ #
  # PPPoE client
  # ------------------------------------------------------------------ #
  def refresh_pppoe(self):
    status = services.pc_wan_status("admin", self.node_item.device_name)
    if status["pppoe_user"]:
      self.pppoe_user.setText(status["pppoe_user"])
    if status["pppoe_pass"]:
      self.pppoe_pass.setText(status["pppoe_pass"])
    if status["status"] == "online" and status["ip"]:
      self.pppoe_status.setText(
          f"<font color='#008000'><b>Connected</b></font> — IP {status['ip']}"
          f" (VLAN {status['vlan']})"
      )
      self.node_item.pc_config["ip_address"] = status["ip"]
      self.node_item.update()
    elif status["status"] == "configured":
      self.pppoe_status.setText(
          "<font color='gray'>Terkonfigurasi, belum dial</font>"
      )
    else:
      self.pppoe_status.setText(
          "<font color='gray'><b>Disconnected</b></font>"
      )
    return status

  def save_pppoe(self):
    user = self.pppoe_user.text().strip()
    password = self.pppoe_pass.text()
    if not user or not password:
      self.pppoe_status.setText("Username dan password wajib diisi.")
      return False
    services.set_pc_pppoe("admin", self.node_item.device_name, user, password)
    self.refresh_pppoe()
    return True

  def connect_pppoe(self):
    if not self.save_pppoe():
      return False
    result = services.connect_pc("admin", self.node_item.device_name)
    if not result.get("ok"):
      self.pppoe_status.setText(f"Gagal: {result.get('message')}")
      return False
    self.node_item.pc_config["ip_address"] = result["ip"]
    self.node_item.pc_config["subnet_mask"] = "255.255.255.0"
    self.node_item.pc_config["gateway"] = result["gateway"]
    self.node_item.update()
    self.ip_address.setText(result["ip"])
    self.subnet_mask.setText("255.255.255.0")
    self.gateway.setText(result["gateway"])
    self.pppoe_status.setText(
        f"<font color='#008000'><b>Connected</b></font> — IP {result['ip']}"
        f" (VLAN {result['vlan']})"
    )
    return True

  def disconnect_pppoe(self):
    services.disconnect_pc("admin", self.node_item.device_name)
    self.node_item.pc_config["ip_address"] = ""
    self.node_item.pc_config["gateway"] = ""
    self.node_item.update()
    self.ip_address.clear()
    self.gateway.clear()
    self.refresh_pppoe()
    return True

  def save_settings(self):
    address = self.ip_address.text().strip()
    mask = self.subnet_mask.text().strip()
    gateway = self.gateway.text().strip()
    if not address and not mask and not gateway:
      self.node_item.pc_config = {"ip_address": "", "subnet_mask": "", "gateway": ""}
      self.node_item.update()
      self.accept()
      return
    if not address or not mask:
      self.error_label.setText("Enter both an IP address and subnet mask.")
      return
    try:
      ip = ipaddress.IPv4Address(address)
      network = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError):
      self.error_label.setText("Enter a valid IPv4 address and subnet mask/prefix.")
      return
    if network.prefixlen < 31 and ip in (network.network_address, network.broadcast_address):
      self.error_label.setText("The PC address cannot be the network or broadcast address.")
      return
    if gateway:
      try:
        gateway_ip = ipaddress.IPv4Address(gateway)
      except ipaddress.AddressValueError:
        self.error_label.setText("Enter a valid IPv4 default gateway.")
        return
      if gateway_ip not in network or gateway_ip == ip:
        self.error_label.setText("The gateway must be a different address in the PC's subnet.")
        return
    self.node_item.pc_config = {
        "ip_address": str(ip),
        "subnet_mask": str(network.netmask),
        "gateway": str(ipaddress.IPv4Address(gateway)) if gateway else "",
    }
    self.node_item.update()
    self.accept()

  def _find_path(self, start_node, target_node):
    from modules.datacom_network import stp_blocked_link_keys, datacom_active_physical_link_keys
    conn = get_connection()
    try:
      blocked_links = stp_blocked_link_keys(conn, "admin")
      active_links = datacom_active_physical_link_keys(conn, "admin")
    finally:
      conn.close()
    if start_node is target_node:
      return True
    pending = [(start_node, None)]
    visited = {(start_node, None)}
    while pending:
      current, incoming_port = pending.pop(0)
      for link in current.links:
        if not link.is_up:
          continue
        local_port = link.source_port if link.source_node is current else link.target_port
        edge_key = tuple(sorted(((link.source_node.device_name, link.source_port), (link.target_node.device_name, link.target_port))))
        if edge_key not in active_links or edge_key in blocked_links:
          continue
        if "OTB" in current.device_type and incoming_port is not None:
          if incoming_port.startswith("IN-"):
            paired_port = "OUT-" + incoming_port[3:]
          elif incoming_port.startswith("OUT-"):
            paired_port = "IN-" + incoming_port[4:]
          else:
            paired_port = None
          if local_port != paired_port:
            continue
        neighbor = link.target_node if link.source_node is current else link.source_node
        if neighbor is target_node:
          return True
        neighbor_port = link.target_port if link.source_node is current else link.source_port
        state = (neighbor, neighbor_port)
        if state not in visited:
          visited.add(state)
          pending.append(state)
    return False

  def _find_datacom_router(self, address, *, exact=False, endpoint=None):
    """Find an active router interface that can serve as a routed PC gateway/edge."""
    try:
      target_ip = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
      return None
    conn = get_connection()
    try:
      rows = conn.execute(
          "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?",
          ("admin",),
      ).fetchall()
    finally:
      conn.close()
    scene = self.node_item.scene()
    if scene is None:
      return None
    topology_nodes = [item for item in scene.items() if isinstance(item, NetworkNodeItem)]
    for name, kind, raw in rows:
      if "router" not in (kind or "").lower():
        continue
      node_item = next((item for item in topology_nodes if item.device_name == name), None)
      if node_item is None or (endpoint is not None and not self._find_path(endpoint, node_item)):
        continue
      try:
        cfg = json.loads(raw)
      except (TypeError, ValueError):
        continue
      for ifname, iface in (cfg.get("interfaces") or {}).items():
        if not iface.get("ip") or iface.get("shutdown"):
          continue
        if exact:
          matched = iface["ip"] == str(target_ip)
        else:
          try:
            network = ipaddress.IPv4Network(f"{iface['ip']}/{iface.get('mask') or '255.255.255.0'}", strict=False)
            matched = target_ip in network
          except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
            matched = False
        if matched:
          return name, node_item, ifname
    return None

  def _has_ont_olt_path(self):
    """Require an active simulated PC-to-ONT-to-OLT path before host probing."""
    scene = self.node_item.scene()
    if scene is None:
      return False
    onts = []
    for link in self.node_item.links:
      if not link.is_up:
        continue
      peer = link.target_node if link.source_node is self.node_item else link.source_node
      if "ONT" in peer.device_type:
        onts.append(peer)
    olts = [
        item for item in scene.items()
        if isinstance(item, NetworkNodeItem) and "OLT" in item.device_type
    ]
    return any(self._find_path(ont, olt) for ont in onts for olt in olts)

  def _run_host_backed_ping(self, destination, target_ip):
    """Run a real host-network ICMP probe after the simulated access path check."""
    try:
      result = subprocess.run(
          ["ping", "-n", "4", "-w", "1000", str(target_ip)],
          capture_output=True,
          text=True,
          errors="replace",
          timeout=8,
          check=False,
      )
    except FileNotFoundError:
      self.ping_output.setPlainText("Failure: The host operating system's ping utility was not found.")
      return False
    except subprocess.TimeoutExpired:
      self.ping_output.setPlainText(
          f"Host-backed ping to {destination} ({target_ip}) timed out while using the laptop's network."
      )
      return False
    except OSError as exc:
      self.ping_output.setPlainText(f"Failure: Could not run the host ping utility: {exc}")
      return False

    output = [
        f"Host-backed internet test via laptop WLAN: {destination} ({target_ip})",
        "The simulated PC-to-ONT-to-OLT link was checked; ICMP is sent by the laptop host.",
        "",
        (result.stdout or result.stderr).strip(),
    ]
    self.ping_output.setPlainText("\n".join(line for line in output if line is not None))
    return result.returncode == 0

  def _emit_ping_capture(self, destination, target_ip, reachable):
    """Publish representative ARP/ICMP events to active OLT mirror sessions."""
    scene = self.node_item.scene()
    if scene is None:
      return
    olts = [item for item in scene.items() if isinstance(item, NetworkNodeItem) and "OLT" in item.device_type and self._find_path(self.node_item, item)]
    if not olts:
      return
    from modules.commands.mirror import emit
    source = self.node_item.pc_config.get("ip_address", "PC")
    gateway = self.node_item.pc_config.get("gateway") or str(target_ip)
    for olt in olts:
      for link in olt.links:
        if not link.is_up:
          continue
        port = link.source_port if link.source_node is olt else link.target_port
        if not port.startswith("0/2/"):
          continue
        emit("admin", olt.device_name, port, "egress", "ARP", source, "ff:ff:ff:ff:ff:ff", f"Who has {gateway}? Tell {source}")
        emit("admin", olt.device_name, port, "ingress", "ARP", gateway, source, f"ARP reply: {gateway} is at simulated-gateway")
        emit("admin", olt.device_name, port, "egress", "ICMP", source, str(target_ip), "Echo (ping) request")
        if reachable:
          emit("admin", olt.device_name, port, "ingress", "ICMP", str(target_ip), source, "Echo (ping) reply")

  def _resolve_ip_target(self, destination):
    scene = self.node_item.scene()
    for item in scene.items():
      if isinstance(item, NetworkNodeItem) and item.device_type.lower() == "pc":
        if item.pc_config.get("ip_address") == destination:
          return item, None

    conn = get_connection()
    try:
      row = conn.execute(
          "SELECT node_name,vlan_id,address,mask FROM olt_node_vlanif_addresses "
          "WHERE username = ? AND address = ? LIMIT 1",
          ("admin", destination),
      ).fetchone()
      return row, conn
    except Exception:
      conn.close()
      raise

  def _vxlan_pc_reachable(self, destination_node, source_ip, destination_ip, gateway="", source_mask="", destination_mask=""):
    """Try the HCIE VXLAN overlay when the PCs have no direct L2 cable path."""
    conn = get_connection()
    try:
      from modules.datacom_vxlan import vxlan_pc_ping_reachable
      return vxlan_pc_ping_reachable(conn, "admin", self.node_item.device_name,
                                     destination_node.device_name, str(source_ip),
                                     str(destination_ip), str(gateway or ""),
                                     str(source_mask or ""), str(destination_mask or ""))
    finally:
      conn.close()

  def _check_ont_service_path(self, olt_node_name, vlan_id):
    """Require a service-port for the ONT connected to this PC on the gateway VLAN."""
    pc_links = [link for link in self.node_item.links if link.is_up]
    ont_nodes = []
    for link in pc_links:
      other = link.target_node if link.source_node is self.node_item else link.source_node
      if "ONT" in other.device_type:
        ont_nodes.append(other)
    if not ont_nodes:
      return False, "PC is not connected to an ONT by an active Ethernet link."

    conn = get_connection()
    try:
      for ont_node in ont_nodes:
        ont = conn.execute(
            "SELECT port,ont_id FROM olt_onts WHERE username = ? AND ont_name = ?",
            ("admin", ont_node.device_name),
        ).fetchone()
        if not ont:
          continue
        service = conn.execute(
            "SELECT v.vlan_attr, v.bound_port, s.options_json "
            "FROM olt_service_ports s JOIN olt_vlan v "
            "ON v.username=s.username AND v.vlan_id=s.vlan "
            "WHERE s.username = ? AND s.port = ? AND s.ont_id = ? "
            "AND s.vlan = ? AND s.state = 'up' LIMIT 1",
            ("admin", ont[0], ont[1], vlan_id),
        ).fetchone()
        if service:
          if (service[0] or "common").lower() == "q-in-q":
            if not service[1]:
              return False, f"QinQ outer VLAN {vlan_id} has no configured uplink port."
            try:
              options = json.loads(service[2] or "{}")
            except (TypeError, ValueError):
              options = {}
            if options.get("vlan_attribute") != "q-in-q" or options.get("qinq_outer_vlan") != vlan_id:
              return False, f"QinQ service-port for VLAN {vlan_id} has no outer-tag configuration."
            if options.get("qinq_inner_vlan") is None:
              return False, f"QinQ service-port for VLAN {vlan_id} has no inner VLAN from its classifier or ONT GEM mapping."
          return True, ""
      return False, f"No service-port was found for this ONT on VLAN {vlan_id}."
    finally:
      conn.close()

  def run_ping(self):
    destination = self.ping_target.text().strip()
    address = self.ip_address.text().strip()
    mask = self.subnet_mask.text().strip()
    gateway = self.gateway.text().strip()
    self.ping_output.clear()
    if not address or not mask:
      self.ping_output.setPlainText("Failure: Configure the PC IP address and subnet mask first.")
      return
    try:
      source = ipaddress.IPv4Address(address)
      network = ipaddress.IPv4Network(f"{source}/{mask}", strict=False)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError):
      self.ping_output.setPlainText("Failure: Enter a valid PC IPv4 address and subnet mask.")
      return
    try:
      target_ip = ipaddress.IPv4Address(destination)
    except ipaddress.AddressValueError:
      try:
        resolved_addresses = socket.getaddrinfo(
            destination, None, socket.AF_INET, socket.SOCK_DGRAM
        )
        target_ip = ipaddress.IPv4Address(resolved_addresses[0][4][0])
      except (socket.gaierror, IndexError, ipaddress.AddressValueError):
        self.ping_output.setPlainText(f"Failure: Could not resolve hostname '{destination}'.")
        return
    if network.prefixlen < 31 and source in (network.network_address, network.broadcast_address):
      self.ping_output.setPlainText("Failure: The PC address cannot be the network or broadcast address.")
      return
    if gateway:
      try:
        gateway_ip = ipaddress.IPv4Address(gateway)
      except ipaddress.AddressValueError:
        self.ping_output.setPlainText("Failure: Enter a valid IPv4 default gateway.")
        return
      if gateway_ip not in network or gateway_ip == source:
        self.ping_output.setPlainText("Failure: The gateway must be a different address in the PC's subnet.")
        return
    self.node_item.pc_config = {
        "ip_address": str(source),
        "subnet_mask": str(network.netmask),
        "gateway": str(ipaddress.IPv4Address(gateway)) if gateway else "",
    }
    self.node_item.update()
    config = self.node_item.pc_config

    wlan_conn = get_connection()
    try:
      from modules.datacom_wlan import station_info, station_ping
      station = station_info(wlan_conn, "admin", self.node_item.device_name)
      if station:
        reachable = station_ping(wlan_conn, "admin", self.node_item.device_name, str(target_ip))
        self.ping_output.setPlainText(
            f"PING {destination} ({target_ip}) from {source}\n"
            + ("Reply received through WLAN.\nSuccess: 1/1 packets received." if reachable
               else "Request timed out.\nFailure: Destination is unreachable through the simulated WLAN topology.")
        )
        return bool(reachable)
    finally:
      wlan_conn.close()

    lease_conn = get_connection()
    try:
      from modules.datacom_wlan import wired_pc_info
      wired_lease = wired_pc_info(lease_conn, "admin", self.node_item.device_name)
      if wired_lease:
        reachable = str(target_ip) == config.get("gateway")
        if not reachable:
          rows = lease_conn.execute("SELECT node_name,running_json FROM olt_datacom_devices WHERE username=?", ("admin",)).fetchall()
          owner = None
          for device_name, raw in rows:
            try:
              device_cfg = json.loads(raw or "{}")
            except (TypeError, ValueError):
              continue
            if any(item.get("ip") == str(target_ip) for item in (device_cfg.get("interfaces") or {}).values()):
              owner = device_name
              break
          if owner:
            from modules.datacom_network import routed_ping_reachable
            reachable = routed_ping_reachable(lease_conn, "admin", wired_lease["dhcp_node"], owner,
                                               str(target_ip), source_address=str(source))
          else:
            pc_owner = next((item for item in self.node_item.scene().items()
                             if isinstance(item, NetworkNodeItem) and item.device_type.lower() == "pc"
                             and item.pc_config.get("ip_address") == str(target_ip)), None)
            if pc_owner:
              remote_gateway = pc_owner.pc_config.get("gateway", "")
              router = self._find_datacom_router(remote_gateway, exact=True, endpoint=pc_owner) if remote_gateway else None
              reachable = bool(router and routed_ping_reachable(lease_conn, "admin", wired_lease["dhcp_node"],
                                                                  router[0], str(target_ip), source_address=str(source)))
        output = f"PING {destination} ({target_ip}) from {source}\\n" + (
            "Reply received through wired DHCP client.\\nSuccess: 1/1 packets received."
            if reachable else "Request timed out.\\nFailure: Destination is unreachable through the simulated routed network.")
        self.ping_output.setPlainText(output)
        self._emit_ping_capture(destination, target_ip, reachable)
        return bool(reachable)
    finally:
      lease_conn.close()

    reachable = False
    reason = "Destination is not present in the simulator topology."
    destination_node = None
    destination_vlan = None
    lookup = None
    if target_ip == source:
      reachable = True
      reason = ""
    else:
      resolved, lookup = self._resolve_ip_target(str(target_ip))
      if isinstance(resolved, tuple) and resolved:
        node_name, destination_vlan, remote_address, remote_mask = resolved
        remote_network = ipaddress.IPv4Network(f"{remote_address}/{remote_mask}", strict=False)
        if target_ip not in network and str(target_ip) != config.get("gateway"):
          gateway_router = self._find_datacom_router(config.get("gateway", ""), exact=True, endpoint=self.node_item) if gateway else None
          if gateway_router:
            route_conn = get_connection()
            try:
              from modules.datacom_network import routed_ping_reachable
              reachable = routed_ping_reachable(route_conn, "admin", gateway_router[0], node_name, str(target_ip), source_address=str(source))
            finally:
              route_conn.close()
            reason = "" if reachable else "No active direct or static route, or the return route is missing."
          else:
            reason = "Destination is outside the local subnet; configure a reachable router as the PC gateway."
        elif target_ip in network and remote_network != network:
          reason = "Destination interface subnet does not match this PC's local subnet."
        else:
          scene = self.node_item.scene()
          destination_node = next((item for item in scene.items() if isinstance(item, NetworkNodeItem) and item.device_name == node_name), None)
          if destination_node and self._find_path(self.node_item, destination_node):
            reachable = True
            reason = ""
            if str(target_ip) == config.get("gateway"):
              reachable, reason = self._check_ont_service_path(node_name, destination_vlan)
          else:
            reason = "No active physical link path to the destination."
      elif isinstance(resolved, NetworkNodeItem):
        remote_config = resolved.pc_config
        try:
          remote_network = ipaddress.IPv4Network(f"{remote_config['ip_address']}/{remote_config['subnet_mask']}", strict=False)
        except (KeyError, ValueError):
          reason = "The destination PC has no valid IP configuration."
        else:
          if target_ip not in network or remote_network != network:
            reachable = self._vxlan_pc_reachable(resolved, source, target_ip, gateway,
                config.get("subnet_mask", ""), remote_config.get("subnet_mask", ""))
            if reachable:
              reason = ""
            else:
              gateway_router = self._find_datacom_router(gateway, exact=True, endpoint=self.node_item) if gateway else None
              destination_router = self._find_datacom_router(target_ip, endpoint=resolved)
              if gateway_router and destination_router:
                route_conn = get_connection()
                try:
                  from modules.datacom_network import routed_ping_reachable
                  reachable = routed_ping_reachable(route_conn, "admin", gateway_router[0], destination_router[0], str(target_ip), source_address=str(source))
                finally:
                  route_conn.close()
                reason = "" if reachable else "No active direct or static route, or the return route is missing."
              else:
                reason = "Configure an active router gateway and a route to the destination PC's subnet."
          elif self._find_path(self.node_item, resolved):
            reachable = True
            reason = ""
          else:
            reachable = self._vxlan_pc_reachable(resolved, source, target_ip, gateway,
                config.get("subnet_mask", ""), remote_config.get("subnet_mask", ""))
            reason = "" if reachable else "No active physical or VXLAN path to the destination PC."
      elif target_ip.is_global:
        if not gateway:
          reason = "Configure the simulated PC's default gateway before testing internet access."
        elif not self._has_ont_olt_path():
          reason = "No active simulated path from this PC through an ONT to an OLT."
        else:
          host_reachable = self._run_host_backed_ping(destination, target_ip)
          self._emit_ping_capture(destination, target_ip, host_reachable)
          return
      if lookup:
        lookup.close()

    output = [f"Pinging {target_ip} from {source} with 32 bytes of data:"]
    for _ in range(4):
      output.append(f"Reply from {target_ip}: bytes=32 time<1ms TTL=64" if reachable else f"Request timed out ({reason})")
    received = 4 if reachable else 0
    output.extend([
        "",
        f"Ping statistics for {target_ip}:",
        f"    Packets: Sent = 4, Received = {received}, Lost = {4 - received} ({(4 - received) * 25}% loss)",
    ])
    self.ping_output.setPlainText("\n".join(output))
    self._emit_ping_capture(destination, target_ip, reachable)
