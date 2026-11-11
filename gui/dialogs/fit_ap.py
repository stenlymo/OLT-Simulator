from PyQt6.QtWidgets import QDialog, QFormLayout, QLabel, QVBoxLayout
from modules.database import get_connection
from modules.datacom_wlan import access_point_status, scan_networks


class FitAPDialog(QDialog):
  """Live status panel for a topology Fit AP; provisioning is done from WAC CLI."""
  def __init__(self, node_item, parent=None):
    super().__init__(parent)
    self.node_item = node_item
    self.setWindowTitle(f"Fit AP Properties - {node_item.device_name}")
    self.setMinimumWidth(360)
    layout = QVBoxLayout(self)
    form = QFormLayout()
    self.mac = QLabel()
    self.state = QLabel()
    self.controller = QLabel()
    self.address = QLabel()
    self.ap_group = QLabel()
    self.networks = QLabel()
    for label, widget in (("AP name:", QLabel(node_item.display_name)), ("MAC address:", self.mac),
                          ("Join state:", self.state), ("WAC:", self.controller),
                          ("Management IP:", self.address), ("AP group:", self.ap_group),
                          ("Published SSID:", self.networks)):
      form.addRow(label, widget)
    layout.addLayout(form)
    self.refresh()

  def refresh(self):
    conn = get_connection()
    try:
      result = access_point_status(conn, "admin", self.node_item)
      networks = scan_networks(conn, "admin", self.node_item.scene())
    finally:
      conn.close()
    self.mac.setText(result.get("mac", "-"))
    self.state.setText(result.get("state", "fault"))
    self.controller.setText(result.get("wac") or "-")
    self.address.setText(result.get("ip") or "-")
    self.ap_group.setText(result.get("group") or "-")
    names = sorted({row["ssid"] for row in networks if row["ap_node"] == self.node_item.device_name})
    self.networks.setText(", ".join(names) if names else "-")
