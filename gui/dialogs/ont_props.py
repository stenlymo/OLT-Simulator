from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from modules.database import get_connection
from modules import services


class ONTPropertiesDialog(QDialog):
  """Jendela Dialog Properti ONT dengan Kalkulasi End-to-End FTTH Link Budget Lengkap."""

  SFP_POWER = {"Class B+": 3.0, "Class C+": 5.0, "Class D": 7.0}
  ONT_TX_POWER_DBM = 2.26  # nominal value from the Huawei opticalinfo example
  CONNECTOR_PAIR_LOSS_DB = 0.35
  ONT_RX_SENSITIVITY_DBM = -27.0
  ONT_RX_OVERLOAD_DBM = -8.0
  SPLITTER_LOSS = {
      "1:2": 3.5,
      "1:4": 7.25,
      "1:8": 10.5,
      "1:16": 14.0,
      "1:32": 17.5,
  }

  def __init__(self, node_item, username="admin", parent=None):
    super().__init__(parent)
    self.node_item = node_item
    self.username = username
    self.setWindowTitle(f"ONT Properties - {self.node_item.device_name}")
    self.resize(560, 780)
    self.init_ui()

  def trace_path_to_olt(self):
    current_node = self.node_item
    visited_nodes = {current_node}
    incoming_port = None

    total_distance_km = 0.0
    total_splitter_loss = 0.0
    total_connector_loss = 0.0
    path_nodes = [current_node.device_name]
    sfp_class = "Class C+"
    olt_node = None

    while current_node:
      connected_link = None
      next_node = None
      for link in current_node.links:
        if not link.is_up:
          continue
        local_port = link.source_port if link.source_node is current_node else link.target_port
        if "OTB" in current_node.device_type and incoming_port is not None:
          if incoming_port.startswith("IN-"):
            paired_port = "OUT-" + incoming_port[3:]
          elif incoming_port.startswith("OUT-"):
            paired_port = "IN-" + incoming_port[4:]
          else:
            paired_port = None
          if local_port != paired_port:
            continue
        other = (
            link.target_node
            if link.source_node == current_node
            else link.source_node
        )
        if other not in visited_nodes:
          connected_link = link
          next_node = other
          break
      if not connected_link or not next_node:
        break

      outgoing_port = (
          connected_link.target_port
          if connected_link.source_node is current_node
          else connected_link.source_port
      )

      visited_nodes.add(next_node)
      path_nodes.append(next_node.device_name)
      total_distance_km += connected_link.length_km

      for dev_type, loss in self.SPLITTER_LOSS.items():
        if dev_type in current_node.device_type:
          total_splitter_loss += loss
      # Count one mated connector pair per cable segment. Previously only OTB
      # and roset nodes counted, making OLT-splitter-ONT paths show 0 dB.
      total_connector_loss += self.CONNECTOR_PAIR_LOSS_DB

      if "OLT" in next_node.device_type:
        olt_node = next_node
        sfp_class = connected_link.sfp_class
        break
      current_node = next_node
      incoming_port = outgoing_port

    fiber_loss = total_distance_km * 0.35
    safety_margin = 2.0
    path_loss = fiber_loss + total_splitter_loss + total_connector_loss
    total_loss = path_loss + safety_margin
    olt_tx_power = self.SFP_POWER.get(sfp_class, 3.0)
    ont_rx_power = olt_tx_power - total_loss
    ont_tx_power = self.ONT_TX_POWER_DBM
    olt_rx_power = ont_tx_power - path_loss

    return {
        "olt_node": olt_node,
        "total_distance_km": total_distance_km,
        "fiber_loss": fiber_loss,
        "splitter_loss": total_splitter_loss,
        "connector_loss": total_connector_loss,
        "safety_margin": safety_margin,
        "total_loss": total_loss,
        "tx_power": ont_tx_power,
        "rx_power": ont_rx_power,
        "olt_rx_power": olt_rx_power,
        "path_loss": path_loss,
        "sfp_class": sfp_class,
        # Traversal starts at the ONT, but the UI presents the path in signal
        # direction so the OLT is the first hop.
        "path_str": " → ".join(reversed(path_nodes)),
    }

  def get_database_ont_info(self):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT port, ont_id, sn, run_state, config_state, match_state,"
        " line_profile_id, service_profile_id FROM olt_onts WHERE username = ?"
        " AND ont_name = ? LIMIT 1",
        (self.username, self.node_item.device_name),
    )
    row = cursor.fetchone()
    conn.close()
    if row:
      return {
          "port": row[0],
          "ont_id": row[1],
          "sn": row[2],
          "run_state": row[3],
          "config_state": row[4],
          "match_state": row[5],
          "line_profile": row[6],
          "service_profile": row[7],
      }
    return None

  def _persist_optical_readings(self, path_info):
    """Keep the CLI readings aligned with the currently displayed topology."""
    info = self.get_database_ont_info()
    if not info or not path_info.get("olt_node"):
      return
    conn = get_connection()
    try:
      conn.execute(
          "UPDATE olt_onts SET rx_power=?, tx_power=?, olt_rx_power=? "
          "WHERE username=? AND port=? AND ont_id=?",
          (path_info["rx_power"], path_info["tx_power"],
           path_info["olt_rx_power"], self.username, info["port"], info["ont_id"]),
      )
      conn.commit()
    finally:
      conn.close()

  def init_ui(self):
    main_layout = QVBoxLayout(self)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    layout = QVBoxLayout(content)

    path_info = self.trace_path_to_olt()
    self.path_info = path_info

    # Hardware Info
    dev_group = QGroupBox("Hardware & FTTH Path")
    dev_layout = QFormLayout()
    dev_layout.addRow(
        "Device Name:", QLabel(f"<b>{self.node_item.device_name}</b>")
    )
    if path_info["olt_node"]:
      dev_layout.addRow(
          "Connected OLT:",
          QLabel(f"<b>{path_info['olt_node'].device_name}</b>"),
      )
      dev_layout.addRow(
          "Full Path:", QLabel(f"<small>{path_info['path_str']}</small>")
      )
    dev_group.setLayout(dev_layout)
    layout.addWidget(dev_group)

    # Optical Budget
    opt_group = QGroupBox("Link Budget Simulation (End-to-End)")
    opt_layout = QFormLayout()
    rx_power = path_info["rx_power"]
    if path_info["olt_node"]:
      opt_layout.addRow(
          "Total Fiber Distance:",
          QLabel(f"<b>{path_info['total_distance_km']:.3f} km</b>"),
      )
      opt_layout.addRow(
          "Fiber Loss (0.35 dB/km):",
          QLabel(f"-{path_info['fiber_loss']:.2f} dB"),
      )
      opt_layout.addRow(
          "Splitters Loss:", QLabel(f"-{path_info['splitter_loss']:.2f} dB")
      )
      opt_layout.addRow(
          "Connectors Loss:", QLabel(f"-{path_info['connector_loss']:.2f} dB")
      )
      opt_layout.addRow(
          "ONT Rx power (1490 nm):", QLabel(f"<b>{rx_power:.2f} dBm</b>")
      )
      opt_layout.addRow(
          "ONT Tx power (1310 nm):",
          QLabel(f"<b>{path_info['tx_power']:.2f} dBm</b>"),
      )
      opt_layout.addRow(
          "Calculated OLT Rx (1310 nm):",
          QLabel(f"<b>{path_info['olt_rx_power']:.2f} dBm</b>"),
      )
      opt_layout.addRow(
          "ONT Rx operating range:",
          QLabel(f"{self.ONT_RX_SENSITIVITY_DBM:.0f} to {self.ONT_RX_OVERLOAD_DBM:.0f} dBm (Class B+)"),
      )
      self._persist_optical_readings(path_info)
    opt_group.setLayout(opt_layout)
    layout.addWidget(opt_group)

    # GPON Database Info
    db_info = self.get_database_ont_info()
    gpon_group = QGroupBox("GPON Registration & Serial Number (DB)")
    gpon_layout = QFormLayout()
    if db_info and path_info["olt_node"] and rx_power >= -30.0:
      gpon_layout.addRow(
          "Serial Number (SN):", QLabel(f"<b>{db_info['sn']}</b>")
      )
      gpon_layout.addRow(
          "Run State:",
          QLabel(f"<font color='#00ff00'><b>{db_info['run_state']}</b></font>"),
      )
    else:
      gpon_layout.addRow(
          "Registration Status:",
          QLabel("<font color='gray'>Unregistered / Offline in OLT</font>"),
      )
    gpon_group.setLayout(gpon_layout)
    layout.addWidget(gpon_group)

    # WAN Client (PPPoE / IPoE) — konfigurasi & status koneksi
    pppoe_group = QGroupBox("WAN Client (PPPoE / IPoE)")
    pppoe_layout = QFormLayout()
    self.wan_mode = QComboBox()
    self.wan_mode.addItems(["PPPoE", "IPoE"])
    self.wan_mode.currentTextChanged.connect(self._apply_wan_mode)
    self.pppoe_user = QLineEdit()
    self.pppoe_user.setPlaceholderText("budi@isp")
    self.pppoe_pass = QLineEdit()
    self.pppoe_pass.setEchoMode(QLineEdit.EchoMode.Password)
    self.mac_address = QLineEdit()
    self.mac_address.setPlaceholderText("00:11:22:33:44:55")
    self.pppoe_vlan = QSpinBox()
    self.pppoe_vlan.setRange(0, 4094)
    self.pppoe_vlan.setValue(20)
    self.pppoe_status = QLabel("<font color='gray'>Belum terkonfigurasi</font>")

    pppoe_layout.addRow("Mode:", self.wan_mode)
    pppoe_layout.addRow("Username:", self.pppoe_user)
    pppoe_layout.addRow("Password:", self.pppoe_pass)
    pppoe_layout.addRow("MAC address:", self.mac_address)
    pppoe_layout.addRow("VLAN:", self.pppoe_vlan)
    pppoe_layout.addRow("Status:", self.pppoe_status)

    btn_row = QHBoxLayout()
    self.btn_save_pppoe = QPushButton("Simpan")
    self.btn_save_pppoe.clicked.connect(lambda: self.save_pppoe())
    self.btn_connect = QPushButton("Connect")
    self.btn_connect.clicked.connect(lambda: self.connect_pppoe())
    self.btn_disconnect = QPushButton("Disconnect")
    self.btn_disconnect.clicked.connect(lambda: self.disconnect_pppoe())
    btn_row.addWidget(self.btn_save_pppoe)
    btn_row.addWidget(self.btn_connect)
    btn_row.addWidget(self.btn_disconnect)
    pppoe_layout.addRow(btn_row)
    pppoe_group.setLayout(pppoe_layout)
    layout.addWidget(pppoe_group)

    scroll.setWidget(content)
    main_layout.addWidget(scroll)

    btn_close = QPushButton("Close")
    btn_close.clicked.connect(self.accept)
    main_layout.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignRight)

    self._apply_wan_mode()
    self.refresh_pppoe_status()

  # ------------------------------------------------------------------ #
  def _olt_name(self):
    olt = self.path_info.get("olt_node") if hasattr(self, "path_info") else None
    return olt.device_name if olt else ""

  def _ont_key(self):
    info = self.get_database_ont_info()
    if not info:
      return None
    return info["port"], info["ont_id"]

  def _apply_wan_mode(self, mode=None):
    ipoe = (mode or self.wan_mode.currentText()) == "IPoE"
    self.pppoe_user.setEnabled(not ipoe)
    self.pppoe_pass.setEnabled(not ipoe)
    self.mac_address.setEnabled(ipoe)

  def refresh_pppoe_status(self):
    key = self._ont_key()
    if not key:
      self.pppoe_status.setText(
          "<font color='gray'>ONT belum terdaftar di OLT</font>"
      )
      return None
    port, ont_id = key
    status = services.wan_status(self.username, port, ont_id)
    if not status:
      return None
    if status.get("mode") == "ipoe":
      self.wan_mode.setCurrentText("IPoE")
    else:
      self.wan_mode.setCurrentText("PPPoE")
    self._apply_wan_mode()
    if status["pppoe_user"]:
      self.pppoe_user.setText(status["pppoe_user"])
    if status.get("pppoe_pass"):
      self.pppoe_pass.setText(status["pppoe_pass"])
    if status.get("mac"):
      self.mac_address.setText(status["mac"])
    if status["vlan"] is not None:
      self.pppoe_vlan.setValue(int(status["vlan"]))

    self.node_item.wan_ip = status["ip"] or ""
    self.node_item.wan_status = status["status"] or ""
    self.node_item.update()

    if status["status"] == "online" and status["ip"]:
      self.pppoe_status.setText(
          f"<font color='#008000'><b>Connected</b></font> — IP {status['ip']}"
          f" ({status['mode'].upper()})"
      )
    elif status["status"] == "auth-failed":
      self.pppoe_status.setText(
          "<font color='#c00000'><b>Disconnected</b></font> — autentikasi gagal"
      )
    else:
      self.pppoe_status.setText(
          "<font color='gray'><b>Disconnected</b></font>"
      )
    return status

  def save_pppoe(self):
    key = self._ont_key()
    if not key:
      self.pppoe_status.setText("ONT belum terdaftar di OLT.")
      return False
    port, ont_id = key
    try:
      if self.wan_mode.currentText() == "IPoE":
        services.set_ont_ipoe(
            self.username,
            self._olt_name(),
            port,
            ont_id,
            int(self.pppoe_vlan.value()),
            self.mac_address.text().strip(),
        )
      else:
        services.set_ont_pppoe(
            self.username,
            self._olt_name(),
            port,
            ont_id,
            self.pppoe_user.text().strip(),
            self.pppoe_pass.text(),
            int(self.pppoe_vlan.value()),
        )
    except ValueError as exc:
      self.pppoe_status.setText(f"Gagal: {exc}")
      return False
    self.refresh_pppoe_status()
    return True

  def connect_pppoe(self):
    key = self._ont_key()
    if not key:
      self.pppoe_status.setText("ONT belum terdaftar di OLT.")
      return False
    port, ont_id = key
    result = services.connect(self.username, self._olt_name(), port, ont_id)
    if not result.get("ok"):
      self.pppoe_status.setText(f"Gagal: {result.get('message')}")
      self.refresh_pppoe_status()
      return False
    self.node_item.wan_ip = result["ip"]
    self.node_item.wan_status = "online"
    self.node_item.update()
    self.pppoe_status.setText(
        f"<font color='#008000'><b>Connected</b></font> — IP {result['ip']}"
        f" ({result.get('mode', 'pppoe').upper()})"
    )
    return True

  def disconnect_pppoe(self):
    key = self._ont_key()
    if not key:
      return False
    port, ont_id = key
    services.disconnect(self.username, self._olt_name(), port, ont_id)
    self.refresh_pppoe_status()
    return True
