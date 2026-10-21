import ipaddress
import json
import socket
import subprocess

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QDialog,
    QDialogButtonBox,
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
          reason = "Destination is outside the local subnet; configure and test the gateway/route first."
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
            reason = "Destination PC is not in the same subnet; routed PC ping is unavailable."
          elif self._find_path(self.node_item, resolved):
            reachable = True
            reason = ""
          else:
            reason = "No active physical link path to the destination PC."
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
