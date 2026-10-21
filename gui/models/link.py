from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainterPath, QPainterPathStroker, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsLineItem


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

    self.is_los = False
    self.blink_state = True

    self.source_node.add_link(self)
    self.target_node.add_link(self)
    self.update_position()

  def update_position(self):
    src_center = self.source_node.get_center_pos()
    tgt_center = self.target_node.get_center_pos()
    self.setLine(
        src_center.x(), src_center.y(), tgt_center.x(), tgt_center.y()
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
      self.is_los = False
      self.is_up = True
      self.update_pen_style()
      self.setToolTip(
          f"Ethernet cable: {self.source_node.device_name} ({self.source_port})"
          f" <---> {self.target_node.device_name} ({self.target_port})\n"
          f"Length: {self.length_km * 1000:.0f} m; simulated link: UP"
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
    self.setToolTip(
        f"Link Segment: {self.source_node.device_name} ({self.source_port})"
        f" <---> {self.target_node.device_name} ({self.target_port})\nDistance:"
        f" {self.length_km:.3f} km ({self.length_km*1000:.0f} m)\nFiber Loss:"
        f" -{fiber_loss:.2f} dB | Component Loss: -{extra_loss:.2f}"
        f" dB\nCalculated Segment Rx: {self.rx_power:.2f} dBm"
        + (f"\nSFP+: {self.sfp_module}\nFiber: {self.fiber_type}; "
           f"{optical['rate_gbps']} Gbit/s, LC, {optical['wavelength_nm']} nm; "
           f"reach: {optical['reach_km']:.1f} km; Tx min: {optical['tx_min_dbm']:.1f} dBm; "
           f"Rx sensitivity: {optical['rx_sensitivity_dbm']:.1f} dBm" if optical else "")
    )

  def update_pen_style(self):
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
    for node in (self.source_node, self.target_node):
      if self in node.links:
        node.links.remove(self)

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
