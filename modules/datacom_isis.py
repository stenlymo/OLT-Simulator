"""Deterministic IS-IS adjacency and IPv4 route model for HCIE labs."""
import heapq
import ipaddress
import json


def _system_id(process):
  for net in process.get("networks", []):
    fields = net.split(".")
    if len(fields) == 6:
      return ".".join(fields[2:5])
  return "0000.0000.0000"


def _area(process):
  net = (process.get("networks") or [""])[0]
  return ".".join(net.split(".")[:2])


def _active(config):
  output = {}
  processes = config.get("isis_processes") or {}
  for name, iface in (config.get("interfaces") or {}).items():
    process_id = iface.get("isis_process")
    if process_id is None or iface.get("shutdown"):
      continue
    process = processes.get(str(process_id), {})
    if not process.get("networks"):
      continue
    try:
      ifname_network = (ipaddress.IPv4Network(
          f"{iface['ip']}/{iface.get('mask') or '255.255.255.0'}", strict=False)
          if iface.get("ip") else None)
    except (ValueError, KeyError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
      continue
    output[name] = {"process": str(process_id), "process_cfg": process,
                    "interface": iface, "network": ifname_network}
  return output


def isis_state(conn, username):
  from modules.datacom_network import datacom_topology_links

  devices = {}
  for name, kind, raw in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (username,)
  ).fetchall():
    try:
      cfg = json.loads(raw)
    except (TypeError, ValueError):
      continue
    if cfg.get("isis_processes"):
      devices[name] = {"kind": kind or "", "cfg": cfg, "processes": cfg["isis_processes"]}
  active = {name: _active(device["cfg"]) for name, device in devices.items()}
  links, _ = datacom_topology_links(conn, username)
  physical = set()
  for a, b, ap, bp, up in links:
    if up:
      physical.add(((a, ap), (b, bp)))
      physical.add(((b, bp), (a, ap)))
  neighbors = {name: [] for name in devices}
  edges = {name: [] for name in devices}

  for a in devices:
    for b in devices:
      if a >= b:
        continue
      for ap, aa in active[a].items():
        for bp, ab in active[b].items():
          if ((a, aa["interface"].get("parent", ap)), (b, ab["interface"].get("parent", bp))) not in physical:
            continue
          pa, pb = aa["process_cfg"], ab["process_cfg"]
          level_a, level_b = pa.get("level", "level-1-2"), pb.get("level", "level-1-2")
          levels_a = {"level-1-2": {1, 2}, "level-1": {1}, "level-2": {2}}.get(level_a, {1, 2})
          levels_b = {"level-1-2": {1, 2}, "level-1": {1}, "level-2": {2}}.get(level_b, {1, 2})
          shared = levels_a & levels_b
          if not shared:
            continue
          # Level-1 adjacency requires a common area; Level-2 adjacency spans areas.
          area_a, area_b = _area(pa), _area(pb)
          level = "L1" if 1 in shared and area_a == area_b else "L2" if 2 in shared else None
          if level is None:
            continue
          sid_a, sid_b = _system_id(pa), _system_id(pb)
          neighbors[a].append({"peer": b, "interface": ap, "peer_interface": bp,
                               "system_id": sid_b, "level": level, "area": area_a})
          neighbors[b].append({"peer": a, "interface": bp, "peer_interface": ap,
                               "system_id": sid_a, "level": level, "area": area_b})
          cost_a = max(1, int(aa["interface"].get("isis_cost", 10)))
          cost_b = max(1, int(ab["interface"].get("isis_cost", 10)))
          edges[a].append((b, ap, bp, cost_a, level))
          edges[b].append((a, bp, ap, cost_b, level))

  routes = {name: [] for name in devices}
  for source, device in devices.items():
    distances, hops = {source: 0}, {source: set()}
    queue = [(0, source)]
    while queue:
      cost, node = heapq.heappop(queue)
      if cost != distances.get(node):
        continue
      for peer, out_if, peer_if, weight, _level in edges.get(node, []):
        candidate = cost + weight
        if candidate < distances.get(peer, 1 << 30):
          distances[peer] = candidate
          hops[peer] = {(peer, out_if)} if node == source else set(hops.get(node, set()))
          heapq.heappush(queue, (candidate, peer))
        elif candidate == distances.get(peer):
          hops.setdefault(peer, set()).update({(peer, out_if)} if node == source else hops.get(node, set()))

    for origin, origin_device in devices.items():
      for ifname, info in active[origin].items():
        iface = info["interface"]
        if not iface.get("ip"):
          continue
        try:
          address = ipaddress.IPv4Address(iface["ip"])
          network = ipaddress.IPv4Network(f"{address}/{32 if iface.get('is_loopback') else iface.get('mask') or '24'}", strict=False)
        except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
          continue
        if origin == source:
          routes[source].append({"network": network, "protocol": "ISIS", "preference": 15,
                                 "cost": 0 if iface.get("is_loopback") else max(1, int(iface.get("isis_cost", 10))),
                                 "next_hop": "0.0.0.0", "interface": ifname, "active": True})
        elif origin in distances:
          for next_router, out_if in hops.get(origin, set()):
            peer = next((row for row in neighbors[source] if row["peer"] == next_router and row["interface"] == out_if), None)
            if peer:
              routes[source].append({"network": network, "protocol": "ISIS", "preference": 15,
                                     "cost": distances[origin] + max(0, int(iface.get("isis_cost", 10))),
                                     "next_hop": next((item.get("ip") for key, item in (devices[next_router]["cfg"].get("interfaces") or {}).items() if key == peer["peer_interface"]), "0.0.0.0"),
                                     "interface": out_if, "active": True})
      if origin != source and origin in distances and any(
          process.get("default_route_advertise") for process in origin_device["processes"].values()):
        for next_router, out_if in hops.get(origin, set()):
          peer = next((row for row in neighbors[source] if row["peer"] == next_router and row["interface"] == out_if), None)
          if peer:
            routes[source].append({"network": ipaddress.IPv4Network("0.0.0.0/0"), "protocol": "ISIS",
                                   "preference": 15, "cost": distances[origin], "next_hop": peer["system_id"],
                                   "interface": out_if, "active": True})
  return {"devices": devices, "active": active, "neighbors": neighbors, "routes": routes}
