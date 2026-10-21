"""Dialog konfigurasi PPPoE Server / BRAS (double-click pada node BRAS)."""

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
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modules import services


class BRASServerDialog(QDialog):
  """Kelola akun PPPoE dan lihat sesi aktif."""

  def __init__(self, node_item=None, username="admin", parent=None):
    super().__init__(parent)
    self.username = username
    self.node_item = node_item
    self.node_name = getattr(node_item, "device_name", "BRAS_1")
    self.setWindowTitle(f"PPPoE Server / BRAS — {self.node_name}")
    self.resize(640, 500)
    self.init_ui()
    self.refresh()

  # ------------------------------------------------------------------ #
  def init_ui(self):
    layout = QVBoxLayout(self)
    layout.addWidget(
        QLabel(
            f"<b>{self.node_name}</b> — akun PPPoE yang boleh dial oleh ONT"
        )
    )

    ip_row = QHBoxLayout()
    ip_row.addWidget(QLabel("Local IP server"))
    self.local_ip = QLineEdit()
    self.local_ip.setPlaceholderText("10.20.0.253")
    ip_row.addWidget(self.local_ip, 1)
    self.btn_save_ip = QPushButton("Simpan IP")
    self.btn_save_ip.clicked.connect(self.save_local_ip)
    ip_row.addWidget(self.btn_save_ip)
    layout.addLayout(ip_row)

    tabs = QTabWidget()
    layout.addWidget(tabs)

    # --- tab akun ---
    accounts = QWidget()
    accounts_layout = QVBoxLayout(accounts)

    self.table = QTableWidget(0, 4)
    self.table.setHorizontalHeaderLabels(["Username", "Password", "Profile", "VLAN"])
    self.table.horizontalHeader().setSectionResizeMode(
        QHeaderView.ResizeMode.Stretch
    )
    self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    accounts_layout.addWidget(self.table)

    form = QFormLayout()
    self.user = QLineEdit()
    self.user.setPlaceholderText("budi@isp")
    self.password = QLineEdit()
    self.profile = QLineEdit("20M")
    self.vlan = QSpinBox()
    self.vlan.setRange(0, 4094)
    self.vlan.setValue(20)
    form.addRow("Username", self.user)
    form.addRow("Password", self.password)
    form.addRow("Profile", self.profile)
    form.addRow("VLAN", self.vlan)
    accounts_layout.addLayout(form)

    buttons = QHBoxLayout()
    self.btn_add = QPushButton("Tambah / Update")
    self.btn_add.clicked.connect(self.add)
    self.btn_delete = QPushButton("Hapus terpilih")
    self.btn_delete.clicked.connect(self.delete_selected)
    buttons.addWidget(self.btn_add)
    buttons.addWidget(self.btn_delete)
    buttons.addStretch(1)
    accounts_layout.addLayout(buttons)

    self.status = QLabel("")
    accounts_layout.addWidget(self.status)
    tabs.addTab(accounts, "Akun PPPoE")

    # --- tab sesi ---
    sessions = QWidget()
    sessions_layout = QVBoxLayout(sessions)
    self.sessions = QTableWidget(0, 8)
    self.sessions.setHorizontalHeaderLabels(
        ["Type", "User/MAC", "ONT", "IP", "VLAN", "Circuit-ID", "Remote-ID", "Since"]
    )
    self.sessions.horizontalHeader().setSectionResizeMode(
        QHeaderView.ResizeMode.Stretch
    )
    self.sessions.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    sessions_layout.addWidget(self.sessions)
    refresh_button = QPushButton("Muat ulang")
    refresh_button.clicked.connect(self.refresh)
    sessions_layout.addWidget(refresh_button)
    tabs.addTab(sessions, "Sesi Aktif")

    close_row = QHBoxLayout()
    close_button = QPushButton("Tutup")
    close_button.clicked.connect(self.accept)
    close_row.addStretch(1)
    close_row.addWidget(close_button)
    layout.addLayout(close_row)

  # ------------------------------------------------------------------ #
  def refresh(self):
    settings = services.server_settings(self.username, self.node_name)
    if settings.get("local_ip"):
      self.local_ip.setText(settings["local_ip"])

    users = services.bras_users(self.username, self.node_name)
    self.table.setRowCount(len(users))
    for index, row in enumerate(users):
      for column, value in enumerate(
          (row["pppoe_user"], row["pppoe_pass"], row["profile"], row["vlan"])
      ):
        self.table.setItem(index, column, QTableWidgetItem(str(value)))
    self.status.setText(f"{len(users)} akun terdaftar.")

    active = services.active_sessions(self.username)
    self.sessions.setRowCount(len(active))
    for index, row in enumerate(active):
      for column, value in enumerate(
          (
              row["type"],
              row["user"],
              row["ont"],
              row["ip"],
              row["vlan"] if row["vlan"] is not None else "-",
              row["circuit_id"] or "-",
              row["remote_id"] or "-",
              row["since"],
          )
      ):
        self.sessions.setItem(index, column, QTableWidgetItem(str(value)))

    pppoe_count = sum(1 for row in active if row["type"] == "PPPoE")
    ipoe_count = sum(1 for row in active if row["type"] == "IPoE")
    self.status.setText(
        f"{len(users)} akun terdaftar · {pppoe_count} sesi PPPoE ·"
        f" {ipoe_count} sesi IPoE"
    )

  # ------------------------------------------------------------------ #
  def save_local_ip(self):
    local_ip = self.local_ip.text().strip()
    services.configure_server(self.username, self.node_name, "bras", local_ip)
    if self.node_item is not None and hasattr(self.node_item, "local_ip"):
      self.node_item.local_ip = local_ip
      self.node_item.update()
    self.status.setText(f"Local IP {local_ip or '-'} tersimpan.")
    return True

  def add(self):
    user = self.user.text().strip()
    password = self.password.text().strip()
    if not user or not password:
      self.status.setText("Username dan password wajib diisi.")
      return False
    try:
      services.add_bras_user(
          self.username,
          self.node_name,
          user,
          password,
          self.profile.text().strip() or "default",
          int(self.vlan.value()),
      )
    except Exception as exc:  # noqa: BLE001
      self.status.setText(f"Gagal: {exc}")
      return False
    self.status.setText(f"Akun {user} tersimpan.")
    self.user.clear()
    self.password.clear()
    self.refresh()
    return True

  def delete_selected(self):
    row = self.table.currentRow()
    if row < 0:
      self.status.setText("Pilih akun yang ingin dihapus.")
      return False
    item = self.table.item(row, 0)
    if item is None:
      return False
    services.delete_bras_user(self.username, self.node_name, item.text())
    self.status.setText(f"Akun {item.text()} dihapus.")
    self.refresh()
    return True
