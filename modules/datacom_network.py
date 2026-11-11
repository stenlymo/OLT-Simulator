"""VLAN-aware forwarding checks for simulated Ethernet paths."""
import json
import hashlib
import heapq


def _port_allows(config, port, vlan):
  interface = (config.get("interfaces") or {}).get(port)
  if interface is None and port.startswith("Eth-Trunk"):
    interface = (config.get("eth_trunks") or {}).get(port[len("Eth-Trunk"):], {})
  interface = interface or {}
  link_type = interface.get("link_type", "access")
  pvid = int(interface.get("pvid", 1))
  if link_type == "route":
    return True
  if link_type == "access":
    return vlan == pvid
  if link_type == "trunk":
    allowed = {int(v) for v in interface.get("allowed_vlans", [1])}
    return vlan in allowed
  if link_type == "hybrid":
    allowed = {int(v) for v in interface.get("untagged_vlans", [pvid])}
    allowed.update(int(v) for v in interface.get("tagged_vlans", []))
    return vlan in allowed
  return False


def _switch_configs(conn, username):
  configs = {}
  tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
  if "olt_datacom_devices" not in tables:
    return configs
  for name, device_type, raw in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?",
      (username,),
  ).fetchall():
    if "switch" not in (device_type or "").lower() and not name.lower().startswith("switch_"):
      continue
    try:
      config = json.loads(raw)
    except (TypeError, ValueError):
      config = {}
    config.setdefault("stp", {"enabled": True, "mode": "mstp", "priority": 32768, "root_role": ""})
    config.setdefault("interfaces", {})
    configs[name] = config
  return configs


def _bridge_key(name, config):
  stp = config.get("stp", {})
  role = stp.get("root_role", "")
  priority = 0 if role == "primary" else (4096 if role == "secondary" else int(stp.get("priority", 32768)))
  mac = hashlib.md5(name.encode("utf-8")).hexdigest()[:12]
  return priority, mac, name


def _switch_port_config(config, port):
  if port.startswith("Eth-Trunk"):
    return (config.get("eth_trunks") or {}).get(port[len("Eth-Trunk"):], {})
  return (config.get("interfaces") or {}).get(port, {})


def stp_port_states(conn, username):
  """Calculate deterministic STP/RSTP port roles for configured switch links."""
  tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
  if "olt_device_links" not in tables:
    return {}
  configs = _switch_configs(conn, username)
  links, _lag_info = datacom_topology_links(conn, username)
  links = [row for row in links if row[4]]
  switches = set(configs)
  # Newly placed switches use Huawei's default enabled MSTP behavior before first CLI access.
  switches.update(name for row in links for name in row[:2] if name.lower().startswith("switch_"))
  for name in switches:
    configs.setdefault(name, {"stp": {"enabled": True, "mode": "mstp", "priority": 32768, "root_role": ""}, "interfaces": {}})

  def edge_key(a, ap, b, bp):
    return tuple(sorted(((a, ap), (b, bp))))

  switch_links = []
  adjacency = {name: [] for name in switches}
  states = {}
  for a, b, ap, bp, _up in links:
    a_switch = a in switches
    b_switch = b in switches
    a_l2 = a_switch and _switch_port_config(configs[a], ap).get("link_type", "access") != "route"
    b_l2 = b_switch and _switch_port_config(configs[b], bp).get("link_type", "access") != "route"
    a_stp = a_l2 and configs[a].get("stp", {}).get("enabled", True) and _switch_port_config(configs[a], ap).get("stp_enabled", True)
    b_stp = b_l2 and configs[b].get("stp", {}).get("enabled", True) and _switch_port_config(configs[b], bp).get("stp_enabled", True)
    if a_switch or b_switch:
      if a_stp and b_stp:
        key = edge_key(a, ap, b, bp)
        switch_links.append((a, ap, b, bp, key))
        adjacency.setdefault(a, []).append((b, ap, bp, key))
        adjacency.setdefault(b, []).append((a, bp, ap, key))
      else:
        for switch, port, participates in ((a, ap, a_stp), (b, bp, b_stp)):
          if not participates:
            continue
          iface = _switch_port_config(configs[switch], port)
          states[(switch, port)] = {"role": "DESI", "state": "forwarding",
                                    "cost": int(iface.get("stp_cost", 20000)), "edge": bool(iface.get("stp_edge", False))}

  # Find switch-only connected components.
  remaining = set(switches)
  while remaining:
    seed = min(remaining)
    component, todo = set(), [seed]
    while todo:
      current = todo.pop()
      if current in component:
        continue
      component.add(current)
      todo.extend(peer for peer, *_ in adjacency.get(current, []))
    remaining.difference_update(component)
    component_edges = [edge for edge in switch_links if edge[0] in component and edge[2] in component]
    if not component_edges:
      continue
    if any(not configs[n].get("stp", {}).get("enabled", True) for n in component):
      for a, ap, b, bp, _key in component_edges:
        states[(a, ap)] = {"role": "DESI", "state": "forwarding", "cost": 20000, "edge": False}
        states[(b, bp)] = {"role": "DESI", "state": "forwarding", "cost": 20000, "edge": False}
      continue

    root = min(component, key=lambda n: _bridge_key(n, configs[n]))
    distance = {root: 0}
    parent = {}
    queue = [(0, _bridge_key(root, configs[root]), root)]
    while queue:
      cost_so_far, _rank, node = heapq.heappop(queue)
      if cost_so_far != distance.get(node):
        continue
      for peer, local_port, peer_port, key in adjacency.get(node, []):
        local_iface = _switch_port_config(configs[node], local_port)
        peer_iface = _switch_port_config(configs[peer], peer_port)
        weight = max(1, int(local_iface.get("stp_cost", 20000)))
        candidate = cost_so_far + weight
        parent_rank = (_bridge_key(node, configs[node]), int(local_iface.get("stp_port_priority", 128)), local_port)
        old = parent.get(peer)
        if candidate < distance.get(peer, float("inf")) or (candidate == distance.get(peer, float("inf")) and (old is None or parent_rank < old[1])):
          distance[peer] = candidate
          parent[peer] = (node, parent_rank, key, local_port, peer_port)
          heapq.heappush(queue, (candidate, _bridge_key(peer, configs[peer]), peer))

    tree_keys = {value[2] for value in parent.values()}
    for a, ap, b, bp, key in component_edges:
      a_iface = _switch_port_config(configs[a], ap)
      b_iface = _switch_port_config(configs[b], bp)
      a_cost = int(a_iface.get("stp_cost", 20000))
      b_cost = int(b_iface.get("stp_cost", 20000))
      if key in tree_keys:
        if parent.get(a, (None,))[2:3] == (key,):
          states[(a, ap)] = {"role": "ROOT", "state": "forwarding", "cost": a_cost, "edge": bool(a_iface.get("stp_edge", False))}
          states[(b, bp)] = {"role": "DESI", "state": "forwarding", "cost": b_cost, "edge": bool(b_iface.get("stp_edge", False))}
        else:
          states[(b, bp)] = {"role": "ROOT", "state": "forwarding", "cost": b_cost, "edge": bool(b_iface.get("stp_edge", False))}
          states[(a, ap)] = {"role": "DESI", "state": "forwarding", "cost": a_cost, "edge": bool(a_iface.get("stp_edge", False))}
      else:
        blocked_node = max((a, b), key=lambda n: (distance.get(n, float("inf")), _bridge_key(n, configs[n])))
        a_blocked = blocked_node == a
        states[(a, ap)] = {"role": "ALTE" if a_blocked else "DESI", "state": "discarding" if a_blocked else "forwarding", "cost": a_cost, "edge": bool(a_iface.get("stp_edge", False))}
        states[(b, bp)] = {"role": "ALTE" if not a_blocked else "DESI", "state": "discarding" if not a_blocked else "forwarding", "cost": b_cost, "edge": bool(b_iface.get("stp_edge", False))}
  return states


def stp_blocked_link_keys(conn, username):
  tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
  if "olt_device_links" not in tables:
    return set()
  states = stp_port_states(conn, username)
  links, lag_info = datacom_topology_links(conn, username)
  result = set()
  for a, b, ap, bp, up in links:
    if not up:
      continue
    key = tuple(sorted(((a, ap), (b, bp))))
    if states.get((a, ap), {}).get("state") == "discarding" or states.get((b, bp), {}).get("state") == "discarding":
      result.add(key)
      if ap.startswith("Eth-Trunk") or bp.startswith("Eth-Trunk"):
        for group in lag_info["groups"].values():
          if ((group["node_a"] == a and group["port_a"] == ap and group["node_b"] == b and group["port_b"] == bp)
              or (group["node_a"] == b and group["port_a"] == bp and group["node_b"] == a and group["port_b"] == ap)):
            result.update(member["key"] for member in group["selected"])
  return result


def datacom_topology_links(conn, username):
  """Return logical links after applying Datacom Eth-Trunk membership/LACP."""
  tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
  if "olt_device_links" not in tables:
    return [], {}
  configs = {}
  if "olt_datacom_devices" in tables:
    for name, raw in conn.execute(
        "SELECT node_name,running_json FROM olt_datacom_devices WHERE username=?", (username,)
    ).fetchall():
      try:
        configs[name] = json.loads(raw)
      except (TypeError, ValueError):
        continue
  # Normalize the OLT's physical-port LAG database into the same interface
  # model used by Datacom switches, so mixed OLT--switch LACP is evaluated as
  # one negotiated bundle rather than as unrelated cables.
  if {"olt_link_aggregations", "olt_link_aggregation_members"}.issubset(tables):
    try:
      olt_groups = conn.execute(
          "SELECT node_name,master_port,mode,workmode,max_link_number,least_link_number "
          "FROM olt_link_aggregations WHERE username=?", (username,)).fetchall()
      for node, master, balance, workmode, maximum, least in olt_groups:
        cfg = configs.setdefault(node, {"interfaces": {}, "eth_trunks": {}, "lacp_priority": 32768})
        cfg.setdefault("interfaces", {})
        cfg.setdefault("eth_trunks", {})
        group_id = str(master)
        cfg["eth_trunks"][group_id] = {
            "mode": "lacp-static" if workmode == "lacp-static" else "manual",
            "max_active": int(maximum or 1), "least_active": int(least or 1),
            "link_type": "trunk", "pvid": 1, "allowed_vlans": [1],
            "olt_master_port": master, "balance_mode": balance,
        }
        for _board, _port_id, member in conn.execute(
            "SELECT board,port_id,member_port FROM olt_link_aggregation_members "
            "WHERE username=? AND node_name=? AND master_port=?", (username, node, master)).fetchall():
          cfg["interfaces"].setdefault(member, {})["eth_trunk"] = group_id
    except Exception:
      pass
  raw_links = conn.execute(
      "SELECT source_node,target_node,source_port,target_port,is_up FROM olt_device_links WHERE username=?",
      (username,),
  ).fetchall()

  def iface(node, port):
    return (configs.get(node, {}).get("interfaces") or {}).get(port, {})

  def group(node, port):
    trunk_id = iface(node, port).get("eth_trunk")
    if trunk_id is None:
      return None
    return str(trunk_id), (configs.get(node, {}).get("eth_trunks") or {}).get(str(trunk_id), {})

  plain = []
  lag_candidates = {}
  member_states = {}
  unmatched = []
  for a, b, ap, bp, is_up in raw_links:
    ga, gb = group(a, ap), group(b, bp)
    if ga is None and gb is None:
      plain.append((a, b, ap, bp, bool(is_up) and not iface(a, ap).get("shutdown", False) and not iface(b, bp).get("shutdown", False)))
      continue
    if ga is None or gb is None:
      if ga:
        member_states[(a, ap)] = "Unselected (no peer Eth-Trunk)" if is_up else "Down"
      if gb:
        member_states[(b, bp)] = "Unselected (no peer Eth-Trunk)" if is_up else "Down"
      unmatched.append((a, b, ap, bp, bool(is_up)))
      continue
    gid_a, ca = ga
    gid_b, cb = gb
    mode_a, mode_b = ca.get("mode", "manual"), cb.get("mode", "manual")
    pair_key = tuple(sorted(((a, gid_a), (b, gid_b))))
    item = {"a": a, "b": b, "ap": ap, "bp": bp, "gid_a": gid_a, "gid_b": gid_b,
            "mode_a": mode_a, "mode_b": mode_b, "cfg_a": ca, "cfg_b": cb,
            "up": bool(is_up) and not iface(a, ap).get("shutdown", False) and not iface(b, bp).get("shutdown", False),
            "key": tuple(sorted(((a, ap), (b, bp))))}
    lag_candidates.setdefault(pair_key, []).append(item)

  logical = list(plain)
  lag_summary = {}
  for pair_key, members in lag_candidates.items():
    sample = members[0]
    node_a, node_b = sample["a"], sample["b"]
    cfg_a, cfg_b = sample["cfg_a"], sample["cfg_b"]
    mode_a, mode_b = sample["mode_a"], sample["mode_b"]
    compatible = mode_a == mode_b and mode_a in ("manual", "lacp-static")
    if not compatible:
      for member in members:
        member_states[(member["a"], member["ap"])] = "Non-C&D" if member["up"] else "Down"
        member_states[(member["b"], member["bp"])] = "Non-C&D" if member["up"] else "Down"
      continue
    available = [member for member in members if member["up"]]
    if mode_a == "lacp-static":
      sys_a = int(configs.get(node_a, {}).get("lacp_priority", 32768))
      sys_b = int(configs.get(node_b, {}).get("lacp_priority", 32768))
      actor = node_a if (sys_a, node_a) <= (sys_b, node_b) else node_b
      max_active = min(int(cfg_a.get("max_active", 128)), int(cfg_b.get("max_active", 128)))
      least_active = max(int(cfg_a.get("least_active", 1)), int(cfg_b.get("least_active", 1)))
      def actor_priority(member):
        port = member["ap"] if member["a"] == actor else member["bp"]
        return int(iface(actor, port).get("lacp_priority", 32768)), port
      available.sort(key=actor_priority)
      selected = available[:max_active]
      group_up = len(selected) >= least_active
      if not group_up:
        selected = []
    else:
      selected = available
      least_active = max(int(cfg_a.get("least_active", 1)), int(cfg_b.get("least_active", 1)))
      max_active = min(int(cfg_a.get("max_active", 128)), int(cfg_b.get("max_active", 128)))
      selected = selected[:max_active]
      group_up = len(selected) >= least_active
      if not group_up:
        selected = []
    selected_keys = {item["key"] for item in selected}
    for member in members:
      status = "Down" if not member["up"] else ("Selected" if member["key"] in selected_keys else "Unselect")
      member_states[(member["a"], member["ap"])] = status
      member_states[(member["b"], member["bp"])] = status
    key_a, key_b = pair_key
    # Normalize endpoint direction to the group's canonical key order.
    if (node_a, sample["gid_a"]) == key_a:
      logical_a, logical_b = node_a, node_b
      port_a, port_b = f"Eth-Trunk{sample['gid_a']}", f"Eth-Trunk{sample['gid_b']}"
    else:
      logical_a, logical_b = node_b, node_a
      port_a, port_b = f"Eth-Trunk{sample['gid_b']}", f"Eth-Trunk{sample['gid_a']}"
    lag_summary[pair_key] = {"members": members, "selected": selected, "up": group_up,
                             "mode": mode_a, "max_active": max_active, "least_active": least_active,
                             "node_a": logical_a, "node_b": logical_b, "port_a": port_a, "port_b": port_b}
    if group_up and selected:
      logical.append((logical_a, logical_b, port_a, port_b, True))
  return logical, {"member_states": member_states, "groups": lag_summary, "unmatched": unmatched}


def datacom_active_physical_link_keys(conn, username):
  """Physical cables currently able to carry traffic after LAG/LACP selection."""
  logical, info = datacom_topology_links(conn, username)
  active = set()
  for a, b, ap, bp, up in logical:
    if not up:
      continue
    if ap.startswith("Eth-Trunk") or bp.startswith("Eth-Trunk"):
      for group in info["groups"].values():
        if ((group["node_a"] == a and group["port_a"] == ap and group["node_b"] == b and group["port_b"] == bp)
            or (group["node_a"] == b and group["port_a"] == bp and group["node_b"] == a and group["port_b"] == ap)):
          active.update(member["key"] for member in group["selected"])
    else:
      active.add(tuple(sorted(((a, ap), (b, bp)))))
  return active


def _olt_configs(conn, username):
  """Build routed-interface configs for OLT VLANIFs and static routes."""
  tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
  if "olt_node_vlanif_addresses" not in tables:
    return {}
  configs = {}
  for node, vlan_id, address, mask, secondary in conn.execute(
      "SELECT node_name,vlan_id,address,mask,secondary FROM olt_node_vlanif_addresses WHERE username=?",
      (username,),
  ).fetchall():
    config = configs.setdefault(node, {"_is_olt": True, "interfaces": {}, "static_routes": [], "vlans": [1]})
    name = f"Vlanif{vlan_id}" if not secondary else f"Vlanif{vlan_id}-secondary-{address}"
    config["interfaces"][name] = {"ip": address, "mask": mask, "is_vlanif": True,
                                   "vlan_id": int(vlan_id), "link_type": "route", "shutdown": False}
  if "olt_vlan" in tables:
    vlan_ids = {int(row[0]) for row in conn.execute("SELECT vlan_id FROM olt_vlan WHERE username=?", (username,)).fetchall()}
    for config in configs.values():
      config["vlans"] = sorted(vlan_ids or {1})
  if "olt_node_routes" in tables:
    for node, dest, mask, gateway, preference, outbound in conn.execute(
        "SELECT node_name,dest,mask,gateway,preference,outbound_interface FROM olt_node_routes WHERE username=?",
        (username,),
    ).fetchall():
      config = configs.setdefault(node, {"_is_olt": True, "interfaces": {}, "static_routes": [], "vlans": [1]})
      config["static_routes"].append({"destination": dest, "mask": mask, "next_hop": gateway or "0.0.0.0",
                                       "preference": int(preference or 60), "outbound_interface": outbound or ""})
  return configs


def _olt_port_vlan_allowed(conn, username, node, port, vlan):
  """Check an OLT VLAN binding on a physical or logical uplink port."""
  tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
  candidates = [port]
  if port.startswith("Eth-Trunk") and "olt_link_aggregation_members" in tables:
    master = port[len("Eth-Trunk"):]
    candidates.extend(row[0] for row in conn.execute(
        "SELECT member_port FROM olt_link_aggregation_members WHERE username=? AND node_name=? AND master_port=?",
        (username, node, master)).fetchall())
  if "olt_node_vlan_ports" in tables:
    bound = conn.execute(
        "SELECT 1 FROM olt_node_vlan_ports WHERE username=? AND node_name=? AND vlan_id=? AND port IN (" +
        ",".join("?" for _ in candidates) + ") LIMIT 1",
        (username, node, int(vlan), *candidates)).fetchone()
    if bound:
      return True
    has_bindings = conn.execute("SELECT 1 FROM olt_node_vlan_ports WHERE username=? AND node_name=? LIMIT 1",
                                (username, node)).fetchone()
    if not has_bindings and int(vlan) == 1:
      return True
  if "olt_eth_native_vlans" in tables:
    for candidate in candidates:
      if not candidate.startswith("0/2/"):
        continue
      try:
        port_id = int(candidate.rsplit("/", 1)[1])
      except ValueError:
        continue
      if conn.execute(
          "SELECT 1 FROM olt_eth_native_vlans WHERE username=? AND node_name=? AND board='0/2' AND port_id=? AND vlan_id=? LIMIT 1",
          (username, node, port_id, int(vlan))).fetchone():
        return True
  return False


def datacom_routing_table(conn, username, node_name, include_dynamic=True):
  """Build active direct and static IPv4 routes for one Datacom device."""
  devices = {}
  try:
    rows = conn.execute(
        "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?",
        (username,),
    ).fetchall()
  except Exception:
    return []
  for name, kind, raw in rows:
    try:
      config = json.loads(raw)
    except (TypeError, ValueError):
      continue
    devices[name] = (kind or "", config)
  for name, config in _olt_configs(conn, username).items():
    devices.setdefault(name, ("Huawei OLT", config))
  if node_name not in devices:
    return []
  links, _info = datacom_topology_links(conn, username)
  up_ports = set()
  neighbors = {}
  for a, b, ap, bp, up in links:
    if not up:
      continue
    up_ports.update(((a, ap), (b, bp)))
    neighbors[(a, ap)] = (b, bp)
    neighbors[(b, bp)] = (a, ap)

  import ipaddress
  _kind, config = devices[node_name]
  table = []
  for ifname, iface in (config.get("interfaces") or {}).items():
    vlanif_active = False
    if iface.get("is_vlanif"):
      vlan_id = int(iface.get("vlan_id") or 0)
      if config.get("_is_olt"):
        vlanif_active = vlan_id in set(map(int, config.get("vlans", [1]))) and any(
            owner == node_name and (owner, port) in up_ports and _olt_port_vlan_allowed(conn, username, node_name, port, vlan_id)
            for owner, port in up_ports
        )
      else:
        vlanif_active = vlan_id in set(map(int, config.get("vlans", [1]))) and any(
            owner == node_name and (owner, port) in up_ports and _port_allows(config, port, vlan_id)
            for owner, port in up_ports
        )
    parent_name = iface.get("parent", "")
    interface_active = iface.get("is_loopback") or (node_name, ifname) in up_ports or (parent_name and (node_name, parent_name) in up_ports) or vlanif_active
    if not iface.get("ip") or iface.get("shutdown") or not interface_active:
      continue
    try:
      address = ipaddress.IPv4Address(iface["ip"])
      network = ipaddress.IPv4Network(f"{address}/{iface.get('mask') or '255.255.255.0'}", strict=False)
    except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
      continue
    table.append({"network": network, "protocol": "Direct", "preference": 0,
                  "next_hop": "0.0.0.0", "interface": ifname, "active": True})
  connected = list(table)
  for ifname, iface in (config.get("interfaces") or {}).items():
    gateway = iface.get("dhcp_gateway")
    if not gateway or not any(route["interface"] == ifname for route in connected):
      continue
    try:
      gateway = str(ipaddress.IPv4Address(gateway))
    except ipaddress.AddressValueError:
      continue
    table.append({"network": ipaddress.IPv4Network("0.0.0.0/0"), "protocol": "OPR", "preference": 60,
                  "next_hop": gateway, "interface": ifname, "active": True})
  for route in config.get("static_routes", []):
    try:
      network = ipaddress.IPv4Network(f"{route['destination']}/{route['mask']}", strict=False)
      next_hop = ipaddress.IPv4Address(route["next_hop"])
    except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError, TypeError):
      continue
    outgoing = next((item for item in connected if next_hop in item["network"]), None)
    if outgoing is None and route.get("outbound_interface"):
      requested_interface = route["outbound_interface"].replace(" ", "").lower()
      outgoing = next((item for item in connected if item["interface"].replace(" ", "").lower() == requested_interface), None)
    if outgoing is None:
      table.append({"network": network, "protocol": "Static", "preference": int(route.get("preference", 60)),
                    "next_hop": str(next_hop), "interface": "--", "active": False})
    else:
      table.append({"network": network, "protocol": "Static", "preference": int(route.get("preference", 60)),
                    "next_hop": str(next_hop), "interface": outgoing["interface"], "active": True})
  if include_dynamic:
    for module_name, function_name in (("modules.datacom_ospf", "ospf_state"),
                                       ("modules.datacom_isis", "isis_state"),
                                       ("modules.datacom_bgp", "bgp_state")):
      try:
        module = __import__(module_name, fromlist=[function_name])
        state = getattr(module, function_name)(conn, username)
        for route in state.get("routes", {}).get(node_name, []):
          if function_name == "bgp_state":
            route = {"network": route["network"], "protocol": "BGP", "preference": 255,
                     "cost": len(route.get("as_path", [])), "next_hop": route.get("next_hop", "0.0.0.0"),
                     "interface": "--", "active": bool(route.get("best")),
                     "as_path": route.get("as_path", []), "communities": route.get("communities", [])}
          table.append(route)
      except (ImportError, KeyError, ValueError, TypeError):
        continue
    try:
      from modules.datacom_mpls import mpls_state
      vpn_state = mpls_state(conn, username)
      for (owner, vpn_name), routes in vpn_state.get("vpn_routes", {}).items():
        if owner != node_name:
          continue
        for route in routes:
          table.append({"network": route["network"], "protocol": "BGP-VPN", "preference": 255,
                        "cost": len(route.get("as_path", [])), "next_hop": route.get("next_hop", "0.0.0.0"),
                        "interface": "--", "active": True, "vpn_instance": vpn_name,
                        "route_distinguisher": route.get("rd", "")})
      for route in vpn_state.get("ce_routes", {}).get(node_name, []):
        table.append({"network": route["network"], "protocol": "BGP", "preference": 255,
                      "cost": len(route.get("as_path", [])), "next_hop": route.get("next_hop", "0.0.0.0"),
                      "interface": "--", "active": True, "vpn_instance": route.get("vpn_instance", ""),
                      "route_distinguisher": route.get("rd", "")})
    except (ImportError, KeyError, ValueError, TypeError):
      pass
  # Keep inactive protocol candidates visible, but select the best active
  # route for each prefix (Direct, then preference, then OSPF cost).
  groups = {}
  for route in table:
    groups.setdefault((str(route["network"]), route.get("vpn_instance", "")), []).append(route)
  for candidates in groups.values():
    eligible = [row for row in candidates if row.get("active")]
    if not eligible:
      continue
    best_preference = min(int(row.get("preference", 255)) for row in eligible)
    best = [row for row in eligible if int(row.get("preference", 255)) == best_preference]
    if best[0].get("protocol") in ("OSPF", "O_ASE", "ISIS", "BGP", "BGP-VPN"):
      best_cost = min(int(row.get("cost", 0)) for row in best)
      best = [row for row in best if int(row.get("cost", 0)) == best_cost]
    chosen = {id(row) for row in best}
    for row in candidates:
      row["active"] = id(row) in chosen
  return sorted(table, key=lambda item: (-item["network"].prefixlen, item["preference"], int(item["network"].network_address), item.get("cost", 0)))


def _topology_path_exists(conn, username, source, destination):
  links, _info = datacom_topology_links(conn, username)
  blocked = stp_blocked_link_keys(conn, username)
  graph = {}
  for a, b, ap, bp, up in links:
    if not up or tuple(sorted(((a, ap), (b, bp)))) in blocked:
      continue
    graph.setdefault(a, set()).add(b)
    graph.setdefault(b, set()).add(a)
  queue, seen = [(source, [source])], set()
  found_path = None
  while queue:
    current, path = queue.pop(0)
    if current == destination:
      found_path = path
      break
    if current in seen:
      continue
    seen.add(current)
    queue.extend((peer, path + [peer]) for peer in graph.get(current, ()) if peer not in seen)
  if found_path is None:
    return False
  switch_configs = _switch_configs(conn, username)
  switch_nodes = set(switch_configs)
  switch_nodes.update(name for edge in links for name in edge[:2] if name.lower().startswith("switch_"))
  if not switch_nodes.intersection(found_path):
    return True
  vlans = {1}
  for config in switch_configs.values():
    vlans.update(map(int, config.get("vlans", [1])))
    for iface in (config.get("interfaces") or {}).values():
      vlans.add(int(iface.get("pvid", 1)))
      vlans.update(map(int, iface.get("allowed_vlans", [])))
  return any(path_allows_vlan(conn, username, source, destination, vlan)[0] for vlan in vlans)


def routed_ping_reachable(conn, username, source_node, destination_node, destination_ip, source_address=None):
  """Simulate routed IPv4 forwarding and the ICMP return path between routers."""
  import ipaddress

  devices = {}
  for name, kind, raw in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (username,)
  ).fetchall():
    try:
      config = json.loads(raw)
    except (TypeError, ValueError):
      continue
    devices[name] = (kind or "", config)
  for name, config in _olt_configs(conn, username).items():
    devices.setdefault(name, ("Huawei OLT", config))
  if source_node not in devices or destination_node not in devices:
    return False
  try:
    target = ipaddress.IPv4Address(destination_ip)
  except ipaddress.AddressValueError:
    return False

  def interfaces_in_network(network):
    matches = []
    for name, (_kind, cfg) in devices.items():
      for ifname, iface in (cfg.get("interfaces") or {}).items():
        if not iface.get("ip") or iface.get("shutdown"):
          continue
        try:
          address = ipaddress.IPv4Address(iface["ip"])
        except ipaddress.AddressValueError:
          continue
        if address in network:
          matches.append((name, ifname, address))
    return matches

  links, _lag_info = datacom_topology_links(conn, username)
  graph = {}
  for a, b, ap, bp, up in links:
    if up:
      graph.setdefault(a, []).append((b, ap, bp))
      graph.setdefault(b, []).append((a, bp, ap))

  def endpoint_ports(start, end):
    """Find source and destination ports along the connected L2 path."""
    queue, seen = [(start, None, None)], set()
    while queue:
      node, first_port, current_port = queue.pop(0)
      if node == end:
        return first_port, current_port
      if node in seen:
        continue
      seen.add(node)
      for peer, local_port, peer_port in graph.get(node, []):
        if peer in seen:
          continue
        queue.append((peer, local_port if first_port is None else first_port, peer_port))
    return None, None

  def route_walk(node, target_ip, seen):
    if node in seen:
      return False, []
    _kind, cfg = devices[node]
    active_table = datacom_routing_table(conn, username, node)
    for ifname, iface in (cfg.get("interfaces") or {}).items():
      if iface.get("ip") == str(target_ip):
        return True, []
    table = [route for route in active_table
             if route["active"] and target_ip in route["network"]]
    if not table:
      return False, []
    route = table[0]
    if route["protocol"] == "Direct":
      candidates = [(name, ifname, ipaddress.IPv4Address(iface["ip"])) for name, cfg_item in devices.items() for ifname, iface in (cfg_item[1].get("interfaces") or {}).items()
                    if iface.get("ip") == str(target_ip) and not iface.get("shutdown")]
      from modules.datacom_nat import public_nat_interface
      for name, (_kind, cfg_item) in devices.items():
        nat_if = public_nat_interface(cfg_item, target_ip)
        if nat_if and (name, nat_if, target_ip) not in candidates:
          candidates.append((name, nat_if, target_ip))
      for peer, peer_if, _peer_ip in candidates:
        if peer == node:
          return True, []
        local_port, remote_port = endpoint_ports(node, peer)
        source_interface = (cfg.get("interfaces") or {}).get(route["interface"], {})
        peer_interface = (devices[peer][1].get("interfaces") or {}).get(peer_if, {})
        expected_local = source_interface.get("parent", route["interface"])
        expected_remote = peer_interface.get("parent", peer_if)
        if local_port == expected_local and remote_port == expected_remote:
          return True, [(node, route["interface"], "outbound"), (peer, remote_port, "inbound")]
      # A terminal (such as a PC) may own the target IP without being a
      # Datacom CLI device. Preserve connected-subnet reachability through
      # the selected routed interface for those endpoints.
      if not candidates:
        for peer, _local_port, remote_port in graph.get(node, []):
          if _local_port == route["interface"]:
            return True, [(node, route["interface"], "outbound"), (peer, remote_port, "inbound")]
      return False, []
    try:
      next_hop = ipaddress.IPv4Address(route["next_hop"])
      outgoing_network = next(r["network"] for r in active_table
                              if r["protocol"] == "Direct" and r["interface"] == route["interface"])
    except (ipaddress.AddressValueError, StopIteration):
      return False, []
    candidates = [item for item in interfaces_in_network(outgoing_network)
                  if item[2] == next_hop] if int(next_hop) else [
                      item for item in interfaces_in_network(outgoing_network) if item[0] != node]
    for peer, peer_if, _peer_ip in candidates:
      if peer == node:
        continue
      _local_port, remote_port = endpoint_ports(node, peer)
      if not remote_port:
        continue
      reachable, tail = route_walk(peer, target_ip, seen | {node})
      if reachable:
        return True, [(node, route["interface"], "outbound"), (peer, remote_port, "inbound")] + tail
    return False, []

  outbound, forward_path = route_walk(source_node, target, set())
  if not outbound:
    return False
  source_address = source_address or next((devices[node][1].get("interfaces", {}).get(ifname, {}).get("ip")
                                           for node, ifname, direction in forward_path
                                           if node == source_node and direction == "outbound"), None)
  if not source_address:
    return False
  nat_translation = None
  from modules.datacom_nat import record_session, source_translation
  for node, ifname, direction in forward_path:
    if direction != "outbound":
      continue
    translated = source_translation(devices[node][1], ifname, source_address)
    if translated:
      nat_translation = (node, ifname, translated)
      break
  reverse_target = ipaddress.IPv4Address(nat_translation[2][0] if nat_translation else source_address)
  inbound, reverse_path = route_walk(destination_node, reverse_target, set())
  if not inbound:
    return False
  from modules.datacom_acl import packet_path_allows
  forward_allowed = packet_path_allows(devices, forward_path, str(source_address), str(target), "icmp")
  reverse_allowed = packet_path_allows(devices, reverse_path, str(target), str(reverse_target), "icmp")
  if nat_translation:
    nat_node, _nat_if, (translated_ip, rule_name, _ifname) = nat_translation
    translated_back, back_path = route_walk(nat_node, ipaddress.IPv4Address(source_address), set())
    reverse_allowed = reverse_allowed and translated_back and packet_path_allows(
        devices, back_path, str(target), str(source_address), "icmp")
    if forward_allowed and reverse_allowed:
      record_session(username, nat_node, "ICMP", source_address, target, new_source=translated_ip,
                     ttl=20, icmp_id=2048, nat_rule=rule_name)
    return forward_allowed and reverse_allowed
  return forward_allowed and reverse_allowed


def datacom_trace_path(conn, username, source_node, destination_ip, source_address=None, max_hops=16):
  """Resolve the simulated next-hop path used by the HCIA `tracert` command."""
  import ipaddress
  devices = {}
  for name, kind, raw in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (username,)
  ).fetchall():
    try:
      devices[name] = (kind or "", json.loads(raw))
    except (TypeError, ValueError):
      continue
  if source_node not in devices:
    return None
  try:
    target = ipaddress.IPv4Address(destination_ip)
  except ipaddress.AddressValueError:
    return None
  owner = next((name for name, (_kind, cfg) in devices.items()
                if any(iface.get("ip") == str(target) for iface in (cfg.get("interfaces") or {}).values())), None)
  if owner is None:
    return None
  links, _ = datacom_topology_links(conn, username)
  hops, current, seen = [], source_node, set()
  for _ in range(max_hops):
    if current == owner:
      return hops
    if current in seen:
      return None
    seen.add(current)
    routes = [route for route in datacom_routing_table(conn, username, current)
              if route.get("active") and target in route["network"]]
    if not routes:
      return None
    routes.sort(key=lambda route: (-route["network"].prefixlen, route.get("preference", 255), route.get("cost", 0)))
    route = routes[0]
    if route["protocol"] == "Direct":
      if current == owner:
        return hops
      candidates = [(name, ifname, iface) for name, (_kind, cfg) in devices.items() if name != current
                    for ifname, iface in (cfg.get("interfaces") or {}).items() if iface.get("ip") == str(target)]
      next_node = next((name for name, _ifname, _iface in candidates
                        if _topology_path_exists(conn, username, current, name)), None)
      if next_node:
        hops.append(str(target))
        current = next_node
        continue
      return None
    next_hop = route.get("next_hop")
    next_node = next((name for name, (_kind, cfg) in devices.items() if name != current
                      and any(iface.get("ip") == next_hop for iface in (cfg.get("interfaces") or {}).values())), None)
    if not next_node or not _topology_path_exists(conn, username, current, next_node):
      return None
    hops.append(next_hop)
    current = next_node
  return None


def path_allows_vlan(conn, username, source, destination, vlan):
  """Return (reachable, checked) across VLAN-aware switch and OLT uplinks.

  `checked` is false when no switch VLAN policy or OLT uplink binding exists;
  existing simulator service paths then retain their prior behavior.
  """
  rows, _lag_info = datacom_topology_links(conn, username)
  if not rows:
    return True, False
  configs = {}
  for row in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?",
      (username,),
  ).fetchall():
    try:
      config = json.loads(row[2])
      configs[row[0]] = config
      if "switch" in (row[1] or "").lower():
        config["_is_switch"] = True
    except (TypeError, ValueError):
      continue
  switch_nodes = {name for name, config in configs.items() if config.get("_is_switch") or "switch" in name.lower()}
  switch_nodes.update(
      name for row in rows for name in row[:2] if name.lower().startswith("switch_")
  )
  tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
  has_olt_vlan_table = "olt_node_vlan_ports" in tables
  olt_vlan_nodes = set()
  if has_olt_vlan_table:
    olt_vlan_nodes = {row[0] for row in conn.execute(
        "SELECT DISTINCT node_name FROM olt_node_vlan_ports WHERE username=?", (username,)).fetchall()}
  if not switch_nodes and not olt_vlan_nodes:
    return True, False

  graph = {}
  blocked = stp_blocked_link_keys(conn, username)
  def olt_port_allows(node, port):
    if not has_olt_vlan_table:
      return True
    configured = conn.execute("SELECT 1 FROM olt_node_vlan_ports WHERE username=? AND node_name=? LIMIT 1",
                              (username, node)).fetchone()
    if not configured:
      return True
    candidates = [port]
    if port.startswith("Eth-Trunk") and "olt_link_aggregation_members" in tables:
      master = port[len("Eth-Trunk"):]
      candidates.extend(row[0] for row in conn.execute(
          "SELECT member_port FROM olt_link_aggregation_members WHERE username=? AND node_name=? AND master_port=?",
          (username, node, master)).fetchall())
    placeholders = ",".join("?" for _ in candidates)
    rows = conn.execute(
        f"SELECT port FROM olt_node_vlan_ports WHERE username=? AND node_name=? AND vlan_id=? AND port IN ({placeholders})",
        (username, node, int(vlan), *candidates)).fetchall()
    if rows:
      return True
    if "olt_eth_native_vlans" in tables:
      for candidate in candidates:
        if not candidate.startswith("0/2/"):
          continue
        try:
          port_id = int(candidate.rsplit("/", 1)[1])
        except ValueError:
          continue
        native = conn.execute(
            "SELECT 1 FROM olt_eth_native_vlans WHERE username=? AND node_name=? AND board='0/2' AND port_id=? AND vlan_id=? LIMIT 1",
            (username, node, port_id, int(vlan))).fetchone()
        if native:
          return True
    return False

  for a, b, ap, bp, up in rows:
    if not up or tuple(sorted(((a, ap), (b, bp)))) in blocked:
      continue
    graph.setdefault(a, []).append((b, ap, bp))
    graph.setdefault(b, []).append((a, bp, ap))
  queue = [(source, None, frozenset({source}), False, True)]
  physical_switch_path = False
  while queue:
    node, ingress, seen_nodes, crossed_switch, valid = queue.pop(0)
    for neighbor, egress, neighbor_port in graph.get(node, []):
      if neighbor in seen_nodes:
        continue
      switch_here = node in switch_nodes or neighbor in switch_nodes
      checked_here = switch_here or node in olt_vlan_nodes or neighbor in olt_vlan_nodes
      next_crossed = crossed_switch or checked_here
      next_valid = valid
      if node in switch_nodes:
        if ingress is not None:
          next_valid = next_valid and _port_allows(configs.get(node, {}), ingress, vlan)
        next_valid = next_valid and _port_allows(configs.get(node, {}), egress, vlan)
      elif egress.startswith("0/2/") or egress.startswith("Eth-Trunk0/2/"):
        if ingress and (ingress.startswith("0/2/") or ingress.startswith("Eth-Trunk0/2/")):
          next_valid = next_valid and olt_port_allows(node, ingress)
        next_valid = next_valid and olt_port_allows(node, egress)
      if neighbor in switch_nodes:
        next_valid = next_valid and _port_allows(configs.get(neighbor, {}), neighbor_port, vlan)
      elif neighbor_port.startswith("0/2/") or neighbor_port.startswith("Eth-Trunk0/2/"):
        next_valid = next_valid and olt_port_allows(neighbor, neighbor_port)
      if neighbor == destination:
        if next_crossed:
          physical_switch_path = True
        elif next_valid:
          return True, False
        if next_valid and next_crossed:
          return True, True
        continue
      queue.append((neighbor, neighbor_port, seen_nodes | {neighbor}, next_crossed, next_valid))
  if physical_switch_path:
    return False, True
  return True, False


def ping_datacom(username, source_node, destination_ip, source_address=None):
  """Resolve and simulate an IPv4 ping between configured Datacom interfaces."""
  import ipaddress

  conn = __import__("modules.database", fromlist=["get_connection"]).get_connection()
  try:
    rows = conn.execute(
        "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?",
        (username,),
    ).fetchall()
    devices = {}
    destination = None
    for name, device_type, raw in rows:
      try:
        config = json.loads(raw)
      except (TypeError, ValueError):
        continue
      devices[name] = config
      if "switch" in (device_type or "").lower():
        config["_is_switch"] = True
      for ifname, iface in (config.get("interfaces") or {}).items():
        if iface.get("ip") == destination_ip:
          destination = (name, ifname, iface)
    if source_node not in devices:
      return None
    source_config = devices[source_node]
    if destination is None:
      olt_rows = conn.execute(
          "SELECT node_name,vlan_id,address,mask FROM olt_node_vlanif_addresses WHERE username=? AND address=?",
          (username, destination_ip),
      ).fetchall()
      if not olt_rows:
        return None
      from modules.datacom_network import _topology_path_exists
      for olt_node, vlan_id, olt_address, olt_mask in olt_rows:
        for _ifname, iface in (source_config.get("interfaces") or {}).items():
          if not iface.get("ip") or iface.get("shutdown") or (source_address and iface.get("ip") != source_address):
            continue
          try:
            source_network = ipaddress.IPv4Network(f"{iface['ip']}/{iface.get('mask') or '255.255.255.0'}", strict=False)
            target_network = ipaddress.IPv4Network(f"{olt_address}/{olt_mask}", strict=False)
          except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
            continue
          if not _topology_path_exists(conn, username, source_node, olt_node):
            continue
          if source_network == target_network and path_allows_vlan(conn, username, source_node, olt_node, int(vlan_id))[0]:
            return str(ipaddress.IPv4Address(destination_ip)), True
          if source_network != target_network and routed_ping_reachable(
              conn, username, source_node, olt_node, destination_ip,
              source_address=iface["ip"]):
            return str(ipaddress.IPv4Address(destination_ip)), True
      return str(ipaddress.IPv4Address(destination_ip)), False
    sources = [(n, iface) for n, iface in (source_config.get("interfaces") or {}).items()
               if iface.get("ip") and (source_address is None or iface.get("ip") == source_address)]
    if not sources:
      return None
    target_ip = ipaddress.IPv4Address(destination_ip)
    # A shared IP subnet only makes the target address reachable when the
    # route-selected interface has an active physical path to the target's
    # interface. Node-level graph connectivity alone can be stale or use a
    # different interface and must not produce a false ping reply.
    for _ifname, source_iface in sources:
      if source_iface.get("shutdown"):
        continue
      if routed_ping_reachable(conn, username, source_node, destination[0], str(target_ip),
                               source_address=source_iface["ip"]):
        return str(target_ip), True
    return str(target_ip), False
  except (ValueError, TypeError):
    return None
  finally:
    conn.close()
