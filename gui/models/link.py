from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainterPath, QPainterPathStroker, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsLineItem, QGraphicsTextItem


class OpticalLinkItem(QGraphicsLineItem):
  """Representasi garis kabel optik antar perangkat berbasis panjang kustom dan kalkulasi redaman."""

  SFP_POWER = {"Class B+": 3.0, "Class C+": 5.0, "Class D": 7.0}
  SPLITTER_LOSS = {
      "1:2": 3.5,
      "1:4": 7.25,
      "1:8": 10.5,
      "1:16": 14.0,
      "1:32": 17.5,
  }
  SFP_PLUS_MODULES = {
      "SFP+ 10G 1310 nm SMF 10 km (34060599)": {
          "fiber": "SMF", "reach_km": 10.0, "tx_min_dbm": -8.2,
          "rx_sensitivity_dbm": -14.4, "fiber_loss_db_km": 0.35,
          "wavelength_nm": 1310, "rate_gbps": 10,
      },
      "SFP+ 10G 850 nm MMF 300 m (34060494-002)": {
          "fiber": "MMF", "reach_km": 0.3, "tx_min_dbm": -7.3,
          "rx_sensitivity_dbm": -9.9, "fiber_loss_db_km": 2.5,
          "wavelength_nm": 850, "rate_gbps": 10,
      },
      "SFP+ 10GBASE-T RJ-45 copper (UTP, 30 m)": {
          "fiber": "UTP", "reach_km": 0.03, "tx_min_dbm": 0.0,
          "rx_sensitivity_dbm": 0.0, "fiber_loss_db_km": 0.0,
          "wavelength_nm": None, "rate_gbps": 10, "media": "copper",
      },
      "GE 1000BASE-T RJ-45 SFP copper (UTP, 100 m)": {
          "fiber": "UTP", "reach_km": 0.1, "tx_min_dbm": 0.0,
          "rx_sensitivity_dbm": 0.0, "fiber_loss_db_km": 0.0,
          "wavelength_nm": None, "rate_gbps": 1, "media": "copper",
      },
  }

  def __init__(
      self,
      source_node,
      target_node,
      source_port,
      target_port,
      length_km=1.0,
      sfp_class="Class C+",
      sfp_module="",
      fiber_type="SMF",
      connection_type="fiber",
  ):
    super().__init__()
    self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
    self.setZValue(-1)
    self.source_node = source_node
    self.target_node = target_node
    self.source_port = source_port
    self.target_port = target_port
    self.length_km = max(0.0001, float(length_km))
    self.sfp_class = sfp_class
    self.sfp_module = sfp_module
    self.fiber_type = fiber_type
    self.connection_type = connection_type

    self.info_label = QGraphicsTextItem(self)
    self.info_label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    self.info_label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
    self.info_label.setZValue(5)
    self.info_label.setDefaultTextColor(QColor("#f5f7fa"))
    self.info_label.setFont(QFont("Segoe UI", 9))
    self.info_label.setTextWidth(370)
    self.info_label.setVisible(False)

    self.is_los = False
    self.blink_state = True

    self.source_node.add_link(self)
    self.target_node.add_link(self)
    self.update_position()
    # Adding a second cable between the same devices changes the lane offsets
    # of every parallel cable, so refresh those existing graphics items too.
    for link in tuple(self.source_node.links):
      if link is not self and link._same_device_pair(self):
        link.update_position()

  def _same_device_pair(self, other):
    return ((self.source_node is other.source_node and self.target_node is other.target_node)
            or (self.source_node is other.target_node and self.target_node is other.source_node))

  def _parallel_offset(self):
    siblings = [link for link in self.source_node.links if self._same_device_pair(link)]
    if len(siblings) <= 1:
      return 0.0

    def port_key(link):
      if link.source_node.device_name <= link.target_node.device_name:
        return (link.source_port, link.target_port)
      return (link.target_port, link.source_port)

    siblings.sort(key=port_key)
    lane = siblings.index(self) - (len(siblings) - 1) / 2
    return lane * 12.0

  def update_position(self):
    src_center = self.source_node.get_center_pos()
    tgt_center = self.target_node.get_center_pos()
    dx = tgt_center.x() - src_center.x()
    dy = tgt_center.y() - src_center.y()
    length = max((dx * dx + dy * dy) ** 0.5, 1.0)
    # Use the same perpendicular direction for both cable directions. This
    # keeps lanes stable even when the user creates a reverse-oriented link.
    if self.source_node.device_name <= self.target_node.device_name:
      normal_x, normal_y = -dy / length, dx / length
    else:
      normal_x, normal_y = dy / length, -dx / length
    offset = self._parallel_offset()
    self.setLine(
        src_center.x() + normal_x * offset,
        src_center.y() + normal_y * offset,
        tgt_center.x() + normal_x * offset,
        tgt_center.y() + normal_y * offset,
    )
    self.calculate_attenuation()

  def itemChange(self, change, value):
    if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
      self.update_pen_style()
    return super().itemChange(change, value)

  def calculate_attenuation(self):
    if self.connection_type == "ethernet":
      self.rx_power = 0.0
      self.total_loss = 0.0
      copper_sfp = self.SFP_PLUS_MODULES.get(self.sfp_module, {}).get("media") == "copper"
      reach_ok = not copper_sfp or self.length_km <= self.SFP_PLUS_MODULES[self.sfp_module]["reach_km"]
      rate_ok = True
      negotiated_rate = None
      if copper_sfp:
        olt_node, olt_port = next(((node, port) for node, port in
                                  ((self.source_node, self.source_port), (self.target_node, self.target_port))
                                  if "OLT" in node.device_type and port.startswith("0/2/")), (None, None))
        peer_node = self.target_node if olt_node is self.source_node else self.source_node
        peer_max = 10000 if "OLT" in getattr(peer_node, "device_type", "") or "Core Router" in getattr(peer_node, "device_type", "") else 1000
        settings = None
        if olt_node:
          try:
            from modules.database import get_connection
            board, port_id = olt_port.rsplit("/", 1)[0], int(olt_port.rsplit("/", 1)[1])
            conn = get_connection()
            settings = conn.execute("SELECT speed,auto_neg FROM olt_eth_port_settings WHERE username=? AND node_name=? AND board=? AND port_id=?",
                                    ("admin", olt_node.device_name, board, port_id)).fetchone()
            conn.close()
          except Exception:
            settings = None
        if settings:
          speed, auto_neg = settings
          if auto_neg and speed is None:
            negotiated_rate = min(int(self.SFP_PLUS_MODULES[self.sfp_module]["rate_gbps"] * 1000), peer_max)
          else:
            negotiated_rate = int(speed or 10000)
            module_max = int(self.SFP_PLUS_MODULES[self.sfp_module]["rate_gbps"] * 1000)
            rate_ok = negotiated_rate <= min(peer_max, module_max)
        else:
          negotiated_rate = min(int(self.SFP_PLUS_MODULES[self.sfp_module]["rate_gbps"] * 1000), peer_max)
      self.is_los = not reach_ok or not rate_ok
      self.is_up = True
      self.is_up = not self.is_los
      self.update_pen_style()
      detail = "UTP copper"
      if copper_sfp:
        detail += f" | {self.sfp_module} | negotiated/configured {negotiated_rate or 1000} Mbit/s"
        if not reach_ok:
          detail += " | copper reach exceeded (30 m max)"
        elif not rate_ok:
          detail += f" | speed mismatch with peer ({peer_max} Mbit/s maximum)"
      self._set_connection_information(
          "Ethernet cable", "UTP copper", "Simulated link: UP" if self.is_up else "Simulated link: DOWN",
          detail + " | 0 dB loss"
      )
      return
    optical = self.SFP_PLUS_MODULES.get(self.sfp_module)
    if optical:
      tx_power = optical["tx_min_dbm"]
      fiber_loss = self.length_km * optical["fiber_loss_db_km"]
      receiver_limit = optical["rx_sensitivity_dbm"]
      reach_ok = self.length_km <= optical["reach_km"]
      fiber_match = self.fiber_type == optical["fiber"]
    else:
      tx_power = self.SFP_POWER.get(self.sfp_class, 5.0)
      fiber_loss = self.length_km * 0.35
      receiver_limit = -30.0
      reach_ok = True
      fiber_match = True

    extra_loss = 0.0
    for node in (self.source_node, self.target_node):
      for dev_type, loss in self.SPLITTER_LOSS.items():
        if dev_type in node.device_type:
          extra_loss += loss
      if "OTB" in node.device_type:
        extra_loss += 0.35
      elif "Roset" in node.device_type:
        extra_loss += 0.25

    total_loss = fiber_loss + extra_loss
    self.rx_power = tx_power - total_loss
    self.total_loss = total_loss
    self.is_los = self.rx_power < receiver_limit or not reach_ok or not fiber_match
    self.is_up = not self.is_los

    self.update_pen_style()
    detail = (
        f"Distance: {self.length_km:.3f} km ({self.length_km * 1000:.0f} m)"
        f" | Fiber loss: -{fiber_loss:.2f} dB"
        f" | Component loss: -{extra_loss:.2f} dB"
        f" | Segment Rx: {self.rx_power:.2f} dBm"
    )
    if optical:
      detail += (
          f" | SFP+: {self.sfp_module}"
          f" | {self.fiber_type}, {optical['rate_gbps']} Gbit/s, "
          f"{optical['wavelength_nm']} nm, reach {optical['reach_km']:.1f} km"
      )
    self._set_connection_information(
        "Fiber connection", self.fiber_type, "UP" if self.is_up else "LOS", detail
    )

  def _set_connection_information(self, title, medium, status, detail):
    self.setToolTip(
        f"<b>{title}</b><br>"
        f"<b>{self.source_node.device_name}</b> ({self.source_port})"
        f" &harr; <b>{self.target_node.device_name}</b> ({self.target_port})<br>"
        f"Cable: {medium} &nbsp; | &nbsp; Length: {self.length_km * 1000:.0f} m"
        f" &nbsp; | &nbsp; Status: {status}<br>{detail}"
    )
    self.info_label.setHtml(
        "<div style='background-color:#17212b; border:1px solid #8aa0b5; "
        "border-radius:5px; padding:6px 8px; color:#f5f7fa;'>"
        f"<b>{title}</b><br>"
        f"{self.source_node.device_name} ({self.source_port})"
        f" &harr; {self.target_node.device_name} ({self.target_port})<br>"
        f"{medium} &nbsp; | &nbsp; {self.length_km * 1000:.0f} m"
        f" &nbsp; | &nbsp; {status}<br>{detail}</div>"
    )
    self.info_label.setTextWidth(370)
    self.info_label.setVisible(self.isSelected())
    line = self.line()
    center_x = (line.x1() + line.x2()) / 2
    bottom_y = max(
        self.source_node.pos().y() + self.source_node.height,
        self.target_node.pos().y() + self.target_node.height,
    )
    self.info_label.setPos(center_x - 185, bottom_y + 10)

  def update_pen_style(self):
    if hasattr(self, "info_label"):
      self.info_label.setVisible(self.isSelected())
    if self.isSelected():
      pen = QPen(QColor("#00e5ff"), 4, Qt.PenStyle.SolidLine)
    elif self.is_los:
      color = QColor("#ff0000") if self.blink_state else QColor("#440000")
      pen = QPen(color, 2, Qt.PenStyle.DashLine)
    elif self.rx_power >= -27.0:
      pen = QPen(QColor("#00ff00"), 2, Qt.PenStyle.SolidLine)
    else:
      pen = QPen(QColor("#ffaa00"), 2, Qt.PenStyle.SolidLine)
    self.setPen(pen)

  def shape(self):
    path = QPainterPath()
    path.moveTo(self.line().p1())
    path.lineTo(self.line().p2())
    stroker = QPainterPathStroker()
    stroker.setWidth(12)
    return stroker.createStroke(path)

  def boundingRect(self):
    # Keep the wider hit target inside the scene's item bounds so selection
    # works even when the pointer is a few pixels away from a thin cable line.
    return super().boundingRect().adjusted(-6, -6, 6, 6)

  def toggle_blink(self):
    if self.is_los:
      self.blink_state = not self.blink_state
      self.update_pen_style()

  def detach(self):
    peers = [link for link in self.source_node.links
             if link is not self and self._same_device_pair(link)]
    for node in (self.source_node, self.target_node):
      if self in node.links:
        node.links.remove(self)
    for link in peers:
      link.update_position()

  def to_dict(self):
    return {
        "source_node": self.source_node.device_name,
        "target_node": self.target_node.device_name,
        "source_port": self.source_port,
        "target_port": self.target_port,
        "length_km": self.length_km,
        "sfp_class": self.sfp_class,
        "sfp_module": self.sfp_module,
        "fiber_type": self.fiber_type,
        "connection_type": self.connection_type,
        "is_up": self.is_up,
    }
