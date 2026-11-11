from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPen, QPixmap
from PyQt6.QtWidgets import QGraphicsItem, QMenu, QMessageBox


_ICON_DIR = Path(__file__).resolve().parents[2] / "icon"
_ICON_PATHS = {
    "olt": "OLT.png",
    "router": "Router.png",
    "switch": "switch.png",
    "ap": "Access Point.png",
    "ont": "ONT.png",
    "pc": "PC.png",
    "otb": "OTB.png",
    "splitter": "splitter.png",
    "roset": "roset.png",
    "server": "Server.png",
    "wireshark": "wireshark.png",
}
_ICON_CACHE = {}


def _icon_key(device_type):
  kind = str(device_type or "").lower()
  if "wireshark" in kind: return "wireshark"
  if "splitter" in kind: return "splitter"
  if "roset" in kind: return "roset"
  if "otb" in kind: return "otb"
  if "ont" in kind: return "ont"
  if kind == "pc": return "pc"
  if "dhcp" in kind or "pppoe" in kind or "bras" in kind or "server" in kind: return "server"
  if "fit ap" in kind or "access point" in kind: return "ap"
  if "switch" in kind or "lsw" in kind: return "switch"
  if "router" in kind or "core router" in kind: return "router"
  if "olt" in kind: return "olt"
  return None


def _device_icon(device_type):
  key = _icon_key(device_type)
  if not key:
    return QPixmap()
  if key not in _ICON_CACHE:
    _ICON_CACHE[key] = QPixmap(str(_ICON_DIR / _ICON_PATHS[key]))
  return _ICON_CACHE[key]


class NetworkNodeItem(QGraphicsItem):
  """Representasi visual node perangkat jaringan pada kanvas interaktif."""

  def __init__(self, device_type, name, x, y, pc_config=None, display_name=None, ap_config=None):
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
    self.ap_config = {"mac_address": "", "management_ip": "", "online_state": "", "wac_name": "", "ap_id": "", "ap_group": "default"}
    if isinstance(ap_config, dict):
      self.ap_config.update(ap_config)
    if "Fit AP" in device_type and not self.ap_config.get("mac_address"):
      from modules.datacom_wlan import mac_for_node
      self.ap_config["mac_address"] = mac_for_node(name)
    if "Splitter" in device_type:
      ratio = device_type.partition("1:")[2].split(")", 1)[0]
      output_count = int(ratio) if ratio.isdigit() else 0
      self.setToolTip(
          f"Optical splitter: {device_type}\n"
          f"Input: IN-1\nOutputs: OUT-1 through OUT-{output_count}"
      )

    self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
    self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
    self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
    self.setPos(x, y)
    self.width = 142 if "(eNSP-" in device_type else 126
    self.height = 94

  def boundingRect(self):
    return QRectF(0, 0, self.width, self.height)

  def get_center_pos(self):
    return QPointF(
        self.pos().x() + self.width / 2, self.pos().y() + 31
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
    elif "Router (eNSP-AR)" in self.device_type:
      bg_color = QColor("#1565c0")
    elif "Switch (eNSP-LSW)" in self.device_type:
      bg_color = QColor("#00838f")
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
    elif "Fit AP" in self.device_type:
      bg_color = QColor("#00796b")
    else:  # ONT / Router
      bg_color = QColor("#006600")

    icon = _device_icon(self.device_type)
    icon_area = QRectF(14, 0, self.width - 28, 56)
    if not icon.isNull():
      scaled = icon.scaled(
          QSize(round(icon_area.width()), round(icon_area.height())),
          Qt.AspectRatioMode.KeepAspectRatio,
          Qt.TransformationMode.SmoothTransformation,
      )
      x = (self.width - scaled.width()) / 2
      y = icon_area.y() + (icon_area.height() - scaled.height()) / 2
      painter.drawPixmap(QRectF(x, y, scaled.width(), scaled.height()), scaled, QRectF(scaled.rect()))
    else:
      painter.setBrush(QBrush(bg_color))
      painter.setPen(QPen(bg_color.lighter(140), 2))
      painter.drawEllipse(QRectF(self.width / 2 - 23, 5, 46, 46))
      painter.setPen(QColor("#ffffff"))
      painter.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
      painter.drawText(QRectF(self.width / 2 - 23, 5, 46, 46), Qt.AlignmentFlag.AlignCenter,
                       (self.device_type[:1] or "?").upper())

    if self.isSelected():
      painter.setBrush(Qt.BrushStyle.NoBrush)
      painter.setPen(QPen(QColor("#42a5f5"), 2, Qt.PenStyle.DashLine))
      painter.drawRoundedRect(QRectF(4, 1, self.width - 8, self.height - 3), 8, 8)

    painter.setPen(QColor("#f5f7fa"))
    if "Splitter" in self.device_type:
      # Show the selected splitter ratio as its primary label; keep the unique
      # instance name as a second line so parallel splitters remain identifiable.
      painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
      name_metrics = QFontMetrics(painter.font())
      splitter_type = name_metrics.elidedText(self.device_type, Qt.TextElideMode.ElideMiddle, self.width - 6)
      painter.drawText(QRectF(3, 56, self.width - 6, 17), Qt.AlignmentFlag.AlignCenter, splitter_type)
      painter.setFont(QFont("Segoe UI", 8))
      instance_name = QFontMetrics(painter.font()).elidedText(
          self.display_name, Qt.TextElideMode.ElideMiddle, self.width - 6
      )
      painter.drawText(QRectF(3, 72, self.width - 6, 16), Qt.AlignmentFlag.AlignCenter, instance_name)
    else:
      painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
      name_metrics = QFontMetrics(painter.font())
      shown_name = name_metrics.elidedText(self.display_name, Qt.TextElideMode.ElideMiddle, self.width - 6)
      painter.drawText(QRectF(3, 57, self.width - 6, 17), Qt.AlignmentFlag.AlignCenter, shown_name)

    subtitle = ""
    subtitle_color = QColor("#c5d2df")
    if self.device_type.lower() == "pc":
      subtitle = self.pc_config.get("ip_address", "")
    elif "DHCP" in self.device_type or "PPPoE" in self.device_type or "BRAS" in self.device_type:
      subtitle = self.local_ip

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
      subtitle, subtitle_color = label, color

    if "Fit AP" in self.device_type:
      painter.setFont(QFont("Segoe UI", 7))
      painter.setPen(QColor("#b2dfdb"))
      label = f"{self.ap_config.get('online_state') or 'Not joined'} {self.ap_config.get('management_ip') or ''}".strip()
      subtitle, subtitle_color = label, QColor("#b2dfdb")

    if subtitle:
      painter.setFont(QFont("Segoe UI", 7))
      painter.setPen(subtitle_color)
      painter.drawText(QRectF(2, 75, self.width - 4, 14), Qt.AlignmentFlag.AlignCenter,
                       QFontMetrics(painter.font()).elidedText(subtitle, Qt.TextElideMode.ElideMiddle, self.width - 8))

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
    if "Router (eNSP-AR)" in self.device_type or "Switch (eNSP-LSW)" in self.device_type:
      views = self.scene().views() if self.scene() else []
      if views:
        views[0].window().open_datacom_console(self.device_name, self.device_type)
      return True
    if "Wireshark" in self.device_type:
      from gui.dialogs.wireshark import WiresharkDialog
      show_modeless(
          "wireshark",
          lambda: WiresharkDialog(node_item=self, username="admin"),
      )
      return True
    if "Fit AP" in self.device_type:
      from gui.dialogs.fit_ap import FitAPDialog
      show_modeless("fit_ap", lambda: FitAPDialog(node_item=self))
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

  def contextMenuEvent(self, event):
    if "Router (eNSP-AR)" not in self.device_type and "Switch (eNSP-LSW)" not in self.device_type:
      super().contextMenuEvent(event)
      return
    menu = QMenu()
    open_cli = menu.addAction("Open CLI")
    reset_action = menu.addAction("Reset device to factory defaults")
    selected = menu.exec(event.screenPos())
    if selected == open_cli:
      self.open_device_config()
    elif selected == reset_action:
      answer = QMessageBox.question(None, "Reset device", f"Reset {self.device_name}? Its password and configuration will be erased.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
      if answer == QMessageBox.StandardButton.Yes:
        views = self.scene().views() if self.scene() else []
        if views:
          views[0].window().reset_datacom_device(self.device_name)

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
    if "Fit AP" in self.device_type:
      result["ap_config"] = dict(self.ap_config)
    return result

  def set_display_name(self, name):
    self.display_name = name or self.device_name
    self.update()
