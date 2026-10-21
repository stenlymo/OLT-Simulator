"""Compact simulated Wireshark capture window for mirrored packets."""

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from modules.commands import mirror


class WiresharkDialog(QDialog):
  def __init__(self, node_item, username="admin", parent=None):
    super().__init__(parent)
    self.node_item = node_item
    self.username = username
    self.setWindowTitle(f"Wireshark — {node_item.display_name}")
    self.resize(850, 420)
    layout = QVBoxLayout(self)
    controls = QHBoxLayout()
    self.filter = QComboBox()
    self.filter.addItems(["All simulated packets", "PPPoE", "DHCP", "ARP", "ICMP"])
    self.filter.currentTextChanged.connect(self.refresh)
    clear = QPushButton("Clear capture")
    clear.clicked.connect(self.clear)
    controls.addWidget(self.filter)
    controls.addWidget(clear)
    controls.addStretch(1)
    layout.addLayout(controls)
    self.table = QTableWidget(0, 5)
    self.table.setHorizontalHeaderLabels(["Time", "Protocol", "Source", "Destination", "Info"])
    self.table.setColumnWidth(0, 170)
    self.table.setColumnWidth(1, 100)
    self.table.setColumnWidth(2, 150)
    self.table.setColumnWidth(3, 150)
    self.table.horizontalHeader().setStretchLastSection(True)
    self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    layout.addWidget(self.table)
    self.timer = QTimer(self)
    self.timer.timeout.connect(self.refresh)
    self.timer.start(500)
    self.refresh()

  def refresh(self, *_):
    wanted = self.filter.currentText()
    rows = mirror.packets(self.username, self.node_item.device_name)
    if wanted != "All simulated packets":
      rows = [row for row in rows if row[1].upper() == wanted.upper()]
    rows = list(reversed(rows))
    self.table.setRowCount(len(rows))
    for r, values in enumerate(rows):
      for c, value in enumerate(values):
        self.table.setItem(r, c, QTableWidgetItem(str(value)))
    self.table.scrollToBottom()

  def clear(self):
    mirror.clear_packets(self.username, self.node_item.device_name)
    self.refresh()

  def showEvent(self, event):
    self.timer.start(500)
    self.refresh()
    super().showEvent(event)
