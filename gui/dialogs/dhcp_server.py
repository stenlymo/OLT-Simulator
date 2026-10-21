"""Dialog konfigurasi DHCP Server (double-click pada node DHCP)."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from modules import services


class DHCPServerDialog(QDialog):
  """Form pool DHCP + daftar lease aktif."""

  def __init__(self, node_item=None, username="admin", parent=None):
    super().__init__(parent)
    self.username = username
    self.node_item = node_item
    self.node_name = getattr(node_item, "device_name", "DHCP_1")
    self.setWindowTitle(f"DHCP Server — {self.node_name}")
    self.resize(560, 460)
    self.init_ui()
    self.refresh()

  # ------------------------------------------------------------------ #
  def init_ui(self):
    layout = QVBoxLayout(self)

    title = QLabel(
        f"<b>{self.node_name}</b> — pool alamat untuk ONT pelanggan"
    )
    layout.addWidget(title)

    form = QFormLayout()
    self.local_ip = QLineEdit("10.20.0.254")
    self.pool_name = QLineEdit("POOL1")
    self.start_ip = QLineEdit("10.20.0.10")
    self.end_ip = QLineEdit("10.20.0.100")
    self.gateway = QLineEdit("10.20.0.1")
    self.dns = QLineEdit("8.8.8.8")
    self.vlan = QSpinBox()
    self.vlan.setRange(0, 4094)
    self.vlan.setValue(20)
    form.addRow("Local IP server", self.local_ip)
    form.addRow("Pool name", self.pool_name)
    form.addRow("Start IP", self.start_ip)
    form.addRow("End IP", self.end_ip)
    form.addRow("Gateway", self.gateway)
    form.addRow("DNS", self.dns)
    form.addRow("VLAN", self.vlan)
    layout.addLayout(form)

    buttons = QHBoxLayout()
    self.btn_save = QPushButton("Simpan")
    self.btn_save.clicked.connect(self.save)
    self.btn_close = QPushButton("Tutup")
    self.btn_close.clicked.connect(self.accept)
    buttons.addWidget(self.btn_save)
    buttons.addWidget(self.btn_close)
    buttons.addStretch(1)
    layout.addLayout(buttons)

    self.status = QLabel("")
    layout.addWidget(self.status)

    layout.addWidget(QLabel("Lease aktif"))
    self.table = QTableWidget(0, 4)
    self.table.setHorizontalHeaderLabels(["IP", "ONT", "State", "Mulai"])
    self.table.horizontalHeader().setSectionResizeMode(
        QHeaderView.ResizeMode.Stretch
    )
    self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    layout.addWidget(self.table)

  # ------------------------------------------------------------------ #
  def refresh(self):
    settings = services.server_settings(self.username, self.node_name)
    if settings.get("local_ip"):
      self.local_ip.setText(settings["local_ip"])

    pool = services.dhcp_pool(self.username, self.node_name)
    if pool:
      self.pool_name.setText(pool["pool_name"])
      self.start_ip.setText(pool["start_ip"])
      self.end_ip.setText(pool["end_ip"])
      self.gateway.setText(pool.get("gateway") or "")
      self.dns.setText(pool.get("dns") or "")
      self.vlan.setValue(int(pool.get("vlan") or 0))
      self.status.setText(
          f"Pool tersimpan: {pool['start_ip']} - {pool['end_ip']}"
      )

    rows = [row for row in services.leases(self.username, self.node_name)]
    active = [row for row in rows if row["state"] == "active"]
    self.table.setRowCount(len(active))
    for index, row in enumerate(active):
      for column, value in enumerate(
          (row["ip"], row["ont_name"], row["state"], row["started_at"])
      ):
        self.table.setItem(index, column, QTableWidgetItem(str(value)))

  # ------------------------------------------------------------------ #
  def save(self):
    local_ip = self.local_ip.text().strip()
    try:
      services.configure_dhcp(
          self.username,
          self.node_name,
          self.start_ip.text().strip(),
          self.end_ip.text().strip(),
          self.gateway.text().strip(),
          self.dns.text().strip(),
          int(self.vlan.value()),
          self.pool_name.text().strip() or "POOL1",
          local_ip=local_ip,
      )
    except Exception as exc:  # noqa: BLE001
      self.status.setText(f"Gagal: {exc}")
      return False
    if self.node_item is not None and hasattr(self.node_item, "local_ip"):
      self.node_item.local_ip = local_ip
      self.node_item.update()
    self.status.setText("Konfigurasi DHCP tersimpan.")
    self.refresh()
    return True
