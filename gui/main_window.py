import json
import sys
from PyQt6.QtCore import QMimeData, QTimer, Qt
from PyQt6.QtGui import QAction, QDrag, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from gui.models.link import OpticalLinkItem
from gui.models.node import NetworkNodeItem
from modules import services
from modules.commands.eth_ports import configure_access_port
from modules.commands.ont_status import refresh_ont_states
from modules.database import (
    get_connection,
    get_next_available_sn,
    list_topology_records,
    load_topology_record,
    release_sn,
    save_topology_record,
)


class OLTGraphicsView(QGraphicsView):

  def __init__(self, scene, parent=None):
    super().__init__(scene, parent)
    self.setRenderHints(
        QPainter.RenderHint.Antialiasing
        | QPainter.RenderHint.TextAntialiasing
    )
    self.setSceneRect(0, 0, 2000, 2000)
    self.setStyleSheet("background-color: #1e1e1e; border: none;")
    self.setAcceptDrops(True)

  def dragEnterEvent(self, event):
    if event.mimeData().hasText():
      event.acceptProposedAction()

  def dragMoveEvent(self, event):
    if event.mimeData().hasText():
      event.acceptProposedAction()

  def dropEvent(self, event):
    if event.mimeData().hasText():
      device_type = event.mimeData().text()
      pos = self.mapToScene(event.position().toPoint())
      parent_window = self.window()
      node_name = f"{device_type.split()[0]}_{len(parent_window.scene.items()) + 1}"
      node_item = NetworkNodeItem(device_type, node_name, pos.x(), pos.y())
      parent_window.scene.addItem(node_item)
      parent_window.log_output.append(
          f"[INFO] Added {device_type} ({node_name})"
      )
      event.acceptProposedAction()


class DraggableListWidget(QListWidget):

  def mousePressEvent(self, event):
    super().mousePressEvent(event)
    item = self.itemAt(event.position().toPoint())
    if item:
      drag = QDrag(self)
      mime_data = QMimeData()
      mime_data.setText(item.text())
      drag.setMimeData(mime_data)
      drag.exec(Qt.DropAction.CopyAction)


class MainWindow(QMainWindow):

  def __init__(self):
    super().__init__()
    self.setWindowTitle(
        "Huawei SmartAX OLT Network Simulator (Packet Tracer Mode)"
    )
    self.resize(1280, 768)
    self.connecting_cable = False
    self.selected_src_node = None
    self.cli_sessions = {}

    services.ensure_schema()
    self.init_ui()
    self.blink_timer = QTimer(self)
    self.blink_timer.timeout.connect(self.update_los_animation)
    self.blink_timer.start(500)

    self.service_timer = QTimer(self)
    self.service_timer.timeout.connect(self.refresh_service_indicators)
    self.service_timer.start(3000)

  def refresh_service_indicators(self):
    """Sinkronkan IP lokal server & status PPPoE ONT dari database."""
    from modules.database import get_connection

    conn = get_connection()
    try:
      for item in self.scene.items():
        if not isinstance(item, NetworkNodeItem):
          continue
        if "OLT" in item.device_type:
          refresh_ont_states("admin", item.device_name)
          continue
        if (
            "DHCP" in item.device_type
            or "PPPoE" in item.device_type
            or "BRAS" in item.device_type
        ):
          row = conn.execute(
              "SELECT local_ip FROM sim_service_nodes"
              " WHERE username = 'admin' AND node_name = ?",
              (item.device_name,),
          ).fetchone()
          new_ip = row[0] if row else ""
          if new_ip != item.local_ip:
            item.local_ip = new_ip
            item.update()
        elif "ONT" in item.device_type:
          row = conn.execute(
              "SELECT wan_ip, wan_status FROM olt_onts"
              " WHERE username = 'admin' AND ont_name = ?"
              " ORDER BY ont_id DESC LIMIT 1",
              (item.device_name,),
          ).fetchone()
          new_ip = row[0] if row and row[0] else ""
          new_status = row[1] if row and row[1] else ""
          if (new_ip, new_status) != (item.wan_ip, item.wan_status):
            item.wan_ip, item.wan_status = new_ip, new_status
            item.update()
    finally:
      conn.close()

  def update_los_animation(self):
    for item in self.scene.items():
      if isinstance(item, OpticalLinkItem):
        item.toggle_blink()
    self.scene.update()

  def init_ui(self):
    self.scene = QGraphicsScene(self)
    self.canvas = OLTGraphicsView(self.scene, self)
    self.setCentralWidget(self.canvas)
    self.scene.selectionChanged.connect(self.handle_selection_changed)

    self.init_device_palette()
    self.init_bottom_panel()
    self.init_menu_toolbar()

  def init_device_palette(self):
    dock = QDockWidget("Device Palette", self)
    palette_widget = QWidget()
    layout = QVBoxLayout()
    self.device_list = DraggableListWidget()
    self.device_list.addItems([
        "Core Router",
        "Huawei OLT MA5801",
        "PC",
        "OTB (Optical Termination Box)",
        "Splitter (1:4)",
        "Splitter (1:8)",
        "Splitter (1:16)",
        "Splitter (1:32)",
        "Roset Optik Pelanggan",
        "ONT Huawei",
        "ONT ZTE",
        "DHCP Server",
        "PPPoE Server (BRAS)",
        "Wireshark Packet Analyzer",
    ])
    layout.addWidget(self.device_list)
    palette_widget.setLayout(layout)
    dock.setWidget(palette_widget)
    self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

  def init_bottom_panel(self):
    dock = QDockWidget("Console", self)
    self.console_dock = dock
    self.console_tabs = QTabWidget()
    self.console_tabs.setTabsClosable(True)
    self.console_tabs.tabCloseRequested.connect(self.close_console_tab)
    self.log_output = QTextEdit()
    self.log_output.setReadOnly(True)
    self.log_output.setStyleSheet(
        "background-color: #121212; color: #00ff00; font-family: Consolas;"
    )
    self.console_tabs.addTab(self.log_output, "System Event Logs")
    self.console_tabs.tabBar().setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
    dock.setWidget(self.console_tabs)
    self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
    dock.setMinimumHeight(210)

  def open_olt_console(self, node_name):
    from gui.dialogs.cli_terminal import CLITerminalDialog

    terminal = self.cli_sessions.get(node_name)
    if terminal is None:
      terminal = CLITerminalDialog(node_name=node_name, username="admin", parent=self)
      self.cli_sessions[node_name] = terminal
      self.console_tabs.addTab(terminal, terminal.sysname)
    self.console_dock.show()
    self.console_tabs.setCurrentWidget(terminal)
    terminal.terminal.setFocus()

  def update_device_sysname(self, node_name, sysname):
    for item in self.scene.items():
      if isinstance(item, NetworkNodeItem) and item.device_name == node_name:
        item.set_display_name(sysname)
        break
    terminal = self.cli_sessions.get(node_name)
    if terminal is not None:
      tab_index = self.console_tabs.indexOf(terminal)
      if tab_index >= 0:
        self.console_tabs.setTabText(tab_index, sysname)

  def close_console_tab(self, index):
    widget = self.console_tabs.widget(index)
    if widget is None or widget is self.log_output:
      return
    node_name = getattr(widget, "node_name", None)
    self.console_tabs.removeTab(index)
    if node_name:
      self.cli_sessions.pop(node_name, None)
    widget.deleteLater()

  def close_console_widget(self, widget):
    index = self.console_tabs.indexOf(widget)
    if index > 0:
      self.close_console_tab(index)

  def reset_simulator_topology(self, active_terminal):
    self.scene.clear()
    self.connecting_cable = False
    self.selected_src_node = None
    self.btn_connect.setChecked(False)
    for index in range(self.console_tabs.count() - 1, 0, -1):
      widget = self.console_tabs.widget(index)
      if widget is active_terminal:
        continue
      node_name = getattr(widget, "node_name", None)
      self.console_tabs.removeTab(index)
      if node_name:
        self.cli_sessions.pop(node_name, None)
      widget.deleteLater()
    active_index = self.console_tabs.indexOf(active_terminal)
    if active_index >= 0:
      self.console_tabs.setTabText(active_index, active_terminal.node_name)
    self.log_output.clear()
    self.log_output.append("[INFO] Simulator reset to initial data")

  def init_menu_toolbar(self):
    file_menu = self.menuBar().addMenu("Topology")
    save_topology_action = QAction("Save Topology", self)
    save_topology_action.triggered.connect(self.save_topology)
    file_menu.addAction(save_topology_action)
    load_topology_action = QAction("Load Topology", self)
    load_topology_action.triggered.connect(self.load_topology)
    file_menu.addAction(load_topology_action)

    toolbar = self.addToolBar("Actions")
    toolbar.addAction(save_topology_action)
    toolbar.addAction(load_topology_action)
    self.btn_connect = QAction("Connect Cable", self)
    self.btn_connect.setCheckable(True)
    self.btn_connect.triggered.connect(self.toggle_connect_mode)
    toolbar.addAction(self.btn_connect)

    btn_delete = QAction("Delete Selected", self)
    btn_delete.triggered.connect(self.delete_selected_item)
    toolbar.addAction(btn_delete)

  def _serialize_topology(self):
    nodes = [item for item in self.scene.items() if isinstance(item, NetworkNodeItem)]
    links = [item for item in self.scene.items() if isinstance(item, OpticalLinkItem)]
    return {
        "version": 1,
        "nodes": [node.to_dict() for node in nodes],
        "links": [link.to_dict() for link in links],
    }

  def save_topology(self):
    name, accepted = QInputDialog.getText(self, "Save Topology", "Topology name:")
    name = name.strip()
    if not accepted or not name:
      return
    try:
      self._sync_topology_links()
      save_topology_record(name, self._serialize_topology(), "admin")
      self.log_output.append(f"[INFO] Topology '{name}' and current OLT configuration saved.")
      QMessageBox.information(self, "Topology Saved", f"Topology '{name}' and its latest configuration were saved.")
    except Exception as exc:
      QMessageBox.critical(self, "Save Failed", f"Could not save topology: {exc}")

  def load_topology(self):
    names = list_topology_records("admin")
    if not names:
      QMessageBox.information(self, "Load Topology", "No saved topologies were found.")
      return
    name, accepted = QInputDialog.getItem(self, "Load Topology", "Choose topology:", names, 0, False)
    if not accepted:
      return
    try:
      data = load_topology_record(name, "admin")
      if data is None:
        QMessageBox.warning(self, "Load Topology", "The selected topology no longer exists.")
        return
      self.scene.clear()
      node_by_name = {}
      for row in data.get("nodes", []):
        node = NetworkNodeItem(
            row["device_type"], row["device_name"], row["x"], row["y"],
            row.get("pc_config"), row.get("display_name"),
        )
        node.local_ip = row.get("local_ip", "")
        node.wan_ip = row.get("wan_ip", "")
        node.wan_status = row.get("wan_status", "")
        # Older topology files do not carry a display label. Restore sysname
        # from the saved per-device configuration in that case.
        if "display_name" not in row and "OLT" in node.device_type:
          conn = get_connection()
          saved_name = conn.execute(
              "SELECT sysname FROM olt_node_settings WHERE username=? AND node_name=?",
              ("admin", node.device_name),
          ).fetchone()
          conn.close()
          if saved_name and saved_name[0]:
            node.set_display_name(saved_name[0])
        self.scene.addItem(node)
        node_by_name[node.device_name] = node
      for row in data.get("links", []):
        src = node_by_name.get(row.get("source_node"))
        tgt = node_by_name.get(row.get("target_node"))
        if src is None or tgt is None:
          continue
        self.scene.addItem(OpticalLinkItem(
            src, tgt, row["source_port"], row["target_port"],
            row.get("length_km", 1.0), row.get("sfp_class", "Class C+"),
            row.get("sfp_module", ""), row.get("fiber_type", "SMF"),
            row.get("connection_type", "fiber"),
        ))
      self._sync_topology_links()
      self.log_output.append(f"[INFO] Topology '{name}' loaded with its saved OLT configuration.")
    except Exception as exc:
      QMessageBox.critical(self, "Load Failed", f"Could not load topology: {exc}")

  def toggle_connect_mode(self, checked):
    self.connecting_cable = checked
    self.selected_src_node = None

  def get_available_ports(self, node, other_node):
    used = {
        l.source_port if l.source_node == node else l.target_port
        for l in node.links
    }
    dev = node.device_type
    if dev.lower() == "pc":
      all_p = ["Ethernet0"]
    elif "Wireshark" in dev:
      all_p = ["Ethernet0"]
    elif "ONT" in dev and other_node.device_type.lower() == "pc":
      all_p = [f"ETH-{i}" for i in range(1, 5)]
    elif "OLT" in dev:
      peer = other_node.device_type.lower()
      uplink_peers = (
          "olt" in peer
          or "router" in peer
          or "dhcp" in peer
          or "pppoe" in peer
          or "bras" in peer
          or "wireshark" in peer
          or peer == "pc"
      )
      if uplink_peers:
        all_p = [f"0/2/{i}" for i in range(4)]
      else:
        all_p = [f"0/1/{i}" for i in range(16)]
    elif "Core Router" in dev:
      all_p = ["10GE-0/0/0", "10GE-0/0/1"]
    elif "DHCP" in dev or "PPPoE" in dev or "BRAS" in dev:
      all_p = [f"GE-0/0/{i}" for i in range(4)]
    elif "OTB" in dev:
      all_p = [f"IN-{i}" for i in range(1, 13)] + [
          f"OUT-{i}" for i in range(1, 13)
      ]
    elif "Splitter" in dev:
      r = (
          4
          if "1:4" in dev
          else (8 if "1:8" in dev else (16 if "1:16" in dev else 32))
      )
      all_p = ["IN-1"] + [f"OUT-{i}" for i in range(1, r + 1)]
    elif "Roset" in dev:
      all_p = ["IN-LINE", "OUT-PATCHCORD"]
    elif "ONT" in dev:
      all_p = ["GPON"]
    else:
      all_p = ["PORT-1"]
    return [p for p in all_p if p not in used]

  def handle_selection_changed(self):
    if not self.connecting_cable:
      return
    items = self.scene.selectedItems()
    if not items or not isinstance(items[0], NetworkNodeItem):
      return

    item = items[0]
    if self.selected_src_node is None:
      self.selected_src_node = item
    elif self.selected_src_node != item:
      src, tgt = self.selected_src_node, item
      src_p = self.get_available_ports(src, tgt)
      tgt_p = self.get_available_ports(tgt, src)
      if not src_p or not tgt_p:
        self.reset_cable()
        return

      s_port, ok1 = QInputDialog.getItem(
          self, "Port", f"Select Port ({src.device_name}):", src_p, 0, False
      )
      t_port, ok2 = QInputDialog.getItem(
          self, "Port", f"Select Port ({tgt.device_name}):", tgt_p, 0, False
      )
      if not (ok1 and ok2):
        self.reset_cable()
        return

      sfp = "Class C+"
      sfp_module = ""
      fiber_type = "SMF"
      connection_type = "fiber"
      is_pc_ont_link = (
          (src.device_type.lower() == "pc" and "ONT" in tgt.device_type)
          or (tgt.device_type.lower() == "pc" and "ONT" in src.device_type)
      )
      is_pc_olt_link = (
          (src.device_type.lower() == "pc" and "OLT" in tgt.device_type)
          or (tgt.device_type.lower() == "pc" and "OLT" in src.device_type)
      )
      is_uplink = s_port.startswith("0/2/") or t_port.startswith("0/2/") or s_port.startswith("10GE-") or t_port.startswith("10GE-")
      if is_pc_ont_link or is_pc_olt_link:
        connection_type = "ethernet"
        fiber_type = "UTP"
        max_m = 100
      elif is_uplink:
        modules = list(OpticalLinkItem.SFP_PLUS_MODULES)
        sfp_module, ok3 = QInputDialog.getItem(
            self, "10GE SFP+ Module",
            "Select matching SFP+ modules and duplex TX/RX fiber:",
            modules, 0, False,
        )
        if not ok3:
          self.reset_cable()
          return
        optical = OpticalLinkItem.SFP_PLUS_MODULES[sfp_module]
        fiber_type = optical["fiber"]
        max_m = int(optical["reach_km"] * 1000)
      elif "OLT" in src.device_type or "OLT" in tgt.device_type:
        sfp, ok4 = QInputDialog.getItem(
            self,
            "SFP Class",
            "Select OLT SFP Class:",
            ["Class B+", "Class C+", "Class D"],
            1,
            False,
        )
        if not ok4:
          self.reset_cable()
          return
        max_m = 100000
      else:
        max_m = 100000

      if is_pc_ont_link or is_pc_olt_link:
        len_m = 5
      else:
        len_m, ok_distance = QInputDialog.getInt(
            self, "Distance", f"Fiber distance in meters (maximum {max_m}):",
            min(500, max_m), 1, max_m, 10,
        )
        if not ok_distance:
          self.reset_cable()
          return

      link = OpticalLinkItem(src, tgt, s_port, t_port, len_m / 1000.0, sfp, sfp_module, fiber_type, connection_type)
      self.scene.addItem(link)
      self._sync_topology_links()
      self.log_output.append(
          f"[INFO] Optical link {src.device_name}:{s_port} <-> {tgt.device_name}:{t_port} "
          f"({connection_type}, {fiber_type}, {sfp_module or sfp}) {'UP' if link.is_up else 'LOS'}"
      )

      # OLT <-> PC / DHCP / BRAS: port ETH menjadi access (VLAN 20 native).
      # OLT <-> router: tetap trunk (VLAN bertag).
      peer_text = f"{src.device_type} {tgt.device_type}".lower()
      is_service_link = any(
          key in peer_text for key in ("dhcp", "pppoe", "bras")
      )
      if is_pc_olt_link or is_service_link:
        olt_node = src if "OLT" in src.device_type else tgt
        olt_port = s_port if olt_node is src else t_port
        if configure_access_port("admin", olt_node.device_name, olt_port, 20):
          self.log_output.append(
              f"[INFO] {olt_node.device_name}:{olt_port} = access port"
              " (native VLAN 20, untagged)"
          )
        else:
          self.log_output.append(
              f"[WARN] VLAN 20 belum dibuat; {olt_port} belum dijadikan access."
          )
      elif is_uplink and (
          "router" in src.device_type.lower()
          or "router" in tgt.device_type.lower()
      ):
        olt_node = src if "OLT" in src.device_type else tgt
        olt_port = s_port if olt_node is src else t_port
        self.log_output.append(
            f"[INFO] {olt_node.device_name}:{olt_port} = trunk port (VLAN bertag)"
        )

      # Daftarkan ONT hasil auto-find (SN dari pool) pada port GPON OLT
      # yang benar-benar dipakai kabel, bukan port tetap 0/1/0.
      ont_node = (
          src
          if "ONT" in src.device_type
          else (tgt if "ONT" in tgt.device_type else None)
      )
      if ont_node and not link.is_los:
        conn = get_connection()
        cursor = conn.cursor()
        existing = cursor.execute(
            "SELECT ont_id FROM olt_onts WHERE username = 'admin' AND ont_name = ?",
            (ont_node.device_name,),
        ).fetchone()
        if existing is None:
          if "OLT" in src.device_type:
            olt_port = s_port
          elif "OLT" in tgt.device_type:
            olt_port = t_port
          else:
            olt_port = "0/1/0"
          next_id = cursor.execute(
              "SELECT COALESCE(MAX(ont_id), -1) + 1 FROM olt_onts"
              " WHERE username = 'admin' AND port = ?",
              (olt_port,),
          ).fetchone()[0]
          cursor.execute(
              "INSERT INTO olt_onts (username, port, ont_id, sn, control_flag,"
              " run_state, config_state, match_state, line_profile_id,"
              " service_profile_id, ont_name, autofind_state) VALUES"
              " ('admin', ?, ?, ?, NULL, 'offline', 'initial', 'initial',"
              " ?, ?, ?, 1)",
              (
                  olt_port,
                  next_id,
                  get_next_available_sn(),
                  1,
                  1,
                  ont_node.device_name,
              ),
          )
        conn.commit()
        conn.close()

      self.reset_cable()

  def reset_cable(self):
    self.connecting_cable = False
    self.selected_src_node = None
    self.btn_connect.setChecked(False)

  def _sync_topology_links(self):
    conn = get_connection()
    try:
      cursor = conn.cursor()
      cursor.execute("""CREATE TABLE IF NOT EXISTS olt_device_links (
          username TEXT NOT NULL, source_node TEXT NOT NULL, target_node TEXT NOT NULL,
          source_port TEXT NOT NULL, target_port TEXT NOT NULL,
          fiber_type TEXT DEFAULT 'SMF', sfp_module TEXT DEFAULT '', length_km REAL DEFAULT 0,
          is_up INTEGER DEFAULT 1,
          PRIMARY KEY (username, source_node, source_port, target_node, target_port))""")
      cursor.execute("DELETE FROM olt_device_links WHERE username = 'admin'")
      for item in self.scene.items():
        if not isinstance(item, OpticalLinkItem):
          continue
        cursor.execute(
            "INSERT OR REPLACE INTO olt_device_links "
            "(username,source_node,target_node,source_port,target_port,fiber_type,sfp_module,length_km,is_up) "
            "VALUES ('admin',?,?,?,?,?,?,?,?)",
            (item.source_node.device_name, item.target_node.device_name,
             item.source_port, item.target_port, item.fiber_type, item.sfp_module,
             item.length_km, int(item.is_up)),
        )
      conn.commit()
    finally:
      conn.close()

  def delete_selected_item(self):
    for item in self.scene.selectedItems():
      if isinstance(item, NetworkNodeItem):
        for link in list(item.links):
          link.detach()
          self.scene.removeItem(link)
        self.scene.removeItem(item)
      elif isinstance(item, OpticalLinkItem):
        item.detach()
        self.scene.removeItem(item)
    self._sync_topology_links()
