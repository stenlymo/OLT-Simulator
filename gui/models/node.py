from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPen
from PyQt6.QtWidgets import QGraphicsItem


class NetworkNodeItem(QGraphicsItem):
  """Representasi visual node perangkat jaringan pada kanvas interaktif."""

  def __init__(self, device_type, name, x, y, pc_config=None, display_name=None):
    super().__init__()
    self.device_type = device_type
    self.device_name = name
    self.display_name = display_name or name
    self.links = []
    self._device_dialogs = {}
    self.local_ip = ""
    self.wan_ip = ""
    self.wan_status = ""
    self.pc_config = {
        "ip_address": "", "subnet_mask": "", "gateway": "",
    }
    if isinstance(pc_config, dict):
      self.pc_config.update({key: str(pc_config.get(key, "")) for key in self.pc_config})

    self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
    self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
    self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
    self.setPos(x, y)
    self.width = 130
    self.height = 65

  def boundingRect(self):
    return QRectF(0, 0, self.width, self.height)

  def get_center_pos(self):
    return QPointF(
        self.pos().x() + self.width / 2, self.pos().y() + self.height / 2
    )

  def add_link(self, link):
    self.links.append(link)

  def itemChange(self, change, value):
    if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
      for link in self.links:
        link.update_position()
    return super().itemChange(change, value)

  def paint(self, painter, option, widget):
    if "OLT" in self.device_type:
      bg_color = QColor("#004d99")
    elif "Splitter" in self.device_type:
      bg_color = QColor("#b35900")
    elif "OTB" in self.device_type or "Roset" in self.device_type:
      bg_color = QColor("#4a148c")
    elif self.device_type.lower() == "pc":
      bg_color = QColor("#37474f")
    elif "DHCP" in self.device_type:
      bg_color = QColor("#00695c")
    elif "PPPoE" in self.device_type or "BRAS" in self.device_type:
      bg_color = QColor("#8e0000")
    elif "Wireshark" in self.device_type:
      bg_color = QColor("#6a1b9a")
    else:  # ONT / Router
      bg_color = QColor("#006600")

    border_color = (
        QColor("#ffffff") if self.isSelected() else bg_color.lighter(130)
    )

    painter.setBrush(QBrush(bg_color))
    painter.setPen(QPen(border_color, 2))
    painter.drawRoundedRect(0, 0, self.width, self.height, 8, 8)

    painter.setPen(QColor("#ffffff"))
    painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
    painter.drawText(
        QRectF(2, 6, self.width - 4, 25),
        Qt.AlignmentFlag.AlignCenter,
        self.device_type,
    )

    painter.setFont(QFont("Segoe UI", 8))
    painter.setPen(QColor("#dddddd"))
    painter.drawText(
        QRectF(2, 34, self.width - 4, 20),
        Qt.AlignmentFlag.AlignCenter,
        self.display_name,
    )

    if self.device_type.lower() == "pc" and self.pc_config.get("ip_address"):
      painter.setFont(QFont("Segoe UI", 7))
      painter.setPen(QColor("#b2dfdb"))
      painter.drawText(
          QRectF(2, 48, self.width - 4, 14),
          Qt.AlignmentFlag.AlignCenter,
          self.pc_config["ip_address"],
      )

    if (
        "DHCP" in self.device_type
        or "PPPoE" in self.device_type
        or "BRAS" in self.device_type
    ) and self.local_ip:
      painter.setFont(QFont("Segoe UI", 7))
      painter.setPen(QColor("#b2dfdb"))
      painter.drawText(
          QRectF(2, 48, self.width - 4, 14),
          Qt.AlignmentFlag.AlignCenter,
          self.local_ip,
      )

    if "ONT" in self.device_type and self.wan_status:
      painter.setFont(QFont("Segoe UI", 7))
      if self.wan_status == "online" and self.wan_ip:
        label = f"Online {self.wan_ip}"
        color = QColor("#a5d6a7")
      elif self.wan_status == "auth-failed":
        label = "Auth failed"
        color = QColor("#ef9a9a")
      else:
        label = "Disconnected"
        color = QColor("#b0bec5")
      painter.setPen(color)
      painter.drawText(
          QRectF(2, 48, self.width - 4, 14),
          Qt.AlignmentFlag.AlignCenter,
          label,
      )

    # Indikator Lampu LED Status Port
    led_color = QColor("#555555")
    if self.links:
      worst_rx = min(link.rx_power for link in self.links)
      if worst_rx < -30.0:
        is_blink = any(getattr(l, "blink_state", True) for l in self.links)
        led_color = QColor("#ff0000") if is_blink else QColor("#330000")
      elif worst_rx >= -27.0:
        led_color = QColor("#00ff00")
      else:
        led_color = QColor("#ffaa00")

    painter.setBrush(QBrush(led_color))
    painter.setPen(QPen(QColor("#ffffff"), 1))
    painter.drawEllipse(self.width - 14, 8, 8, 8)

  def open_device_config(self):
    """Buka dialog sesuai tipe perangkat. Return True bila ditangani."""
    def show_modeless(key, factory):
      dialog = self._device_dialogs.get(key)
      if dialog is None:
        dialog = factory()
        self._device_dialogs[key] = dialog
      # Keep device property windows as independent, non-modal top-level
      # windows owned by the simulator. This lets the user click the canvas,
      # open another device (PC/BRAS/Wireshark), and return to this window
      # without Qt entering a blocking dialog event loop.
      views = self.scene().views() if self.scene() else []
      main_window = views[0].window() if views else None
      if main_window and dialog.parentWidget() is not main_window:
        dialog.setParent(main_window, Qt.WindowType.Window)
      dialog.setModal(False)
      dialog.setWindowModality(Qt.WindowModality.NonModal)
      dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
      # Refresh live tables/status whenever the user switches back to a window.
      refresh = getattr(dialog, "refresh", None)
      if callable(refresh):
        try:
          refresh()
        except TypeError:
          pass
      dialog.show()
      dialog.raise_()
      dialog.activateWindow()

    if "OLT" in self.device_type:
      views = self.scene().views() if self.scene() else []
      if views:
        main_window = views[0].window()
        if hasattr(main_window, "open_olt_console"):
          main_window.open_olt_console(self.device_name)
      return True
    if "Wireshark" in self.device_type:
      from gui.dialogs.wireshark import WiresharkDialog
      show_modeless(
          "wireshark",
          lambda: WiresharkDialog(node_item=self, username="admin"),
      )
      return True
    if "ONT" in self.device_type:
      from gui.dialogs.ont_props import ONTPropertiesDialog
      show_modeless(
          "ont",
          lambda: ONTPropertiesDialog(node_item=self, username="admin"),
      )
      return True
    if self.device_type.lower() == "pc":
      from gui.dialogs.pc_config import PCConfigDialog
      show_modeless("pc", lambda: PCConfigDialog(node_item=self))
      return True
    if "DHCP" in self.device_type:
      from gui.dialogs.dhcp_server import DHCPServerDialog
      show_modeless(
          "dhcp",
          lambda: DHCPServerDialog(node_item=self, username="admin"),
      )
      return True
    if "PPPoE" in self.device_type or "BRAS" in self.device_type:
      from gui.dialogs.bras_server import BRASServerDialog
      show_modeless(
          "bras",
          lambda: BRASServerDialog(node_item=self, username="admin"),
      )
      return True
    return False

  def mouseDoubleClickEvent(self, event):
    self.open_device_config()
    super().mouseDoubleClickEvent(event)

  def to_dict(self):
    result = {
        "device_type": self.device_type,
        "device_name": self.device_name,
        "display_name": self.display_name,
        "x": self.pos().x(),
        "y": self.pos().y(),
        "local_ip": self.local_ip,
        "wan_ip": self.wan_ip,
        "wan_status": self.wan_status,
    }
    if self.device_type.lower() == "pc":
      result["pc_config"] = dict(self.pc_config)
    return result

  def set_display_name(self, name):
    self.display_name = name or self.device_name
    self.update()
