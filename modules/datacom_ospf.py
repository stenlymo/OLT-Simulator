"""Small deterministic OSPFv2 control-plane model for HCIA-Datacom labs."""
import ipaddress
import json
import heapq


def _load(conn, username):
  devices = {}
  for name, kind, raw in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (username,)
  ).fetchall():
    try:
      cfg = json.loads(raw)
    except (TypeError, ValueError):
      continue
    processes = cfg.get("ospf_processes") or {}
    if processes:
      devices[name] = {"kind": kind or "", "cfg": cfg, "processes": processes}
  return devices


def _router_id(device):
  processes = device["processes"]
  process = processes.get("1") or next(iter(processes.values()))
  configured = process.get("router_id")
  if configured:
    return configured
  loopbacks = []
  physical = []
  for name, iface in (device["cfg"].get("interfaces") or {}).items():
    try:
      address = ipaddress.IPv4Address(iface.get("ip", ""))
    except ipaddress.AddressValueError:
      continue
    (loopbacks if iface.get("is_loopback") else physical).append(address)
  addresses = loopbacks or physical
  return str(max(addresses)) if addresses else "0.0.0.0"


def _active_interfaces(device):
  result = {}
  cfg = device["cfg"]
  for process_id, process in device["processes"].items():
    for area_id, area in (process.get("areas") or {}).items():
      for ifname, iface in (cfg.get("interfaces") or {}).items():
        if not iface.get("ip") or iface.get("shutdown"):
          continue
        try:
          address = ipaddress.IPv4Address(iface["ip"])
        except ipaddress.AddressValueError:
          continue
        if (iface.get("ospf_enabled") and str(iface.get("ospf_process", "1")) == str(process_id)
            and str(iface.get("ospf_area", "0")) == str(area_id)):
          result.setdefault(ifname, {"area": str(area_id), "process": str(process_id), "iface": iface})
          continue
        for statement in area.get("networks", []):
          try:
            base = ipaddress.IPv4Address(statement["network"])
            wildcard = ipaddress.IPv4Address(statement["wildcard"])
            matches = all((a & (~w & 0xffffffff)) == (b & (~w & 0xffffffff))
                          for a, b, w in [(int(address), int(base), int(wildcard))])
            mask_len = 32 - sum(bin(int(wildcard)).count("1") for _ in [0])
            iface_prefix = ipaddress.IPv4Network(f"{address}/{iface.get('mask') or '24'}", strict=False).prefixlen
          except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
            continue
          if matches and iface_prefix >= mask_len:
            result.setdefault(ifname, {"area": str(area_id), "process": str(process_id), "iface": iface})
  return result


def _auth(device, active):
  iface_auth = active["iface"].get("ospf_auth") or None
  if iface_auth is not None:
    return (iface_auth.get("mode", ""), str(iface_auth.get("key_id", "")), iface_auth.get("password", ""))
  process = device["processes"].get(active["process"], {})
  area = (process.get("areas") or {}).get(active["area"], {})
  auth = area.get("authentication") or {}
  return (auth.get("mode", ""), str(auth.get("key_id", "")), auth.get("password", ""))


def _export_filtered(device, network):
  """Return True when an area export ip-prefix list suppresses this route."""
  config = device["cfg"]
  for process in device["processes"].values():
    for area in (process.get("areas") or {}).values():
      list_name = area.get("export_ip_prefix")
      if not list_name:
        continue
      entries = sorted(config.get("ip_prefix_lists", {}).get(list_name, []), key=lambda item: int(item.get("index", 0)))
      permitted = False
      for entry in entries:
        candidate = ipaddress.IPv4Network(entry["network"], strict=False)
        if network.subnet_of(candidate) and int(entry.get("ge", candidate.prefixlen)) <= network.prefixlen <= int(entry.get("le", 32)):
          permitted = entry.get("action", "permit") == "permit"
          break
      if not permitted:
        return True
  return False


def _area_export_suppresses(device, area_id, network):
  for process in device["processes"].values():
    area = (process.get("areas") or {}).get(str(area_id), {})
    list_name = area.get("export_ip_prefix")
    if not list_name:
      continue
    entries = sorted(device["cfg"].get("ip_prefix_lists", {}).get(list_name, []), key=lambda item: int(item.get("index", 0)))
    for entry in entries:
      candidate = ipaddress.IPv4Network(entry["network"], strict=False)
      if network.subnet_of(candidate) and int(entry.get("ge", candidate.prefixlen)) <= network.prefixlen <= int(entry.get("le", 32)):
        return entry.get("action", "permit") != "permit"
    return True
  return False


def ospf_state(conn, username):
  """Return current adjacencies and per-device OSPF-learned routes."""
  from modules.datacom_network import datacom_topology_links, path_allows_vlan
  devices = _load(conn, username)
  active = {name: _active_interfaces(device) for name, device in devices.items()}
  router_ids = {name: _router_id(device) for name, device in devices.items()}
  links, _ = datacom_topology_links(conn, username)
  neighbors = {name: [] for name in devices}
  edges = {name: [] for name in devices}
  physical = {}
  for a, b, ap, bp, up in links:
    if up:
      physical.setdefault((a, ap), set()).add((b, bp))
      physical.setdefault((b, bp), set()).add((a, ap))
  # OSPF can form over routed physical links or over a shared VLANIF subnet.
  # The latter is essential when a campus core reaches WAC/routers through L2 switches.
  for a in sorted(devices):
    for b in sorted(devices):
      if a >= b or router_ids[a] == router_ids[b]:
        continue
      for ap, aa in active[a].items():
        ia = aa["iface"]
        try:
          ipa = ipaddress.IPv4Address(ia["ip"])
          neta = ipaddress.IPv4Network(f"{ipa}/{ia.get('mask') or '24'}", strict=False)
        except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
          continue
        for bp, ab in active[b].items():
          ib = ab["iface"]
          if aa["area"] != ab["area"] or _auth(devices[a], aa) != _auth(devices[b], ab):
            continue
          try:
            ipb = ipaddress.IPv4Address(ib["ip"])
            netb = ipaddress.IPv4Network(f"{ipb}/{ib.get('mask') or '24'}", strict=False)
          except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
            continue
          if ipa == ipb or neta != netb or ia.get("is_loopback") or ib.get("is_loopback"):
            continue
          vlan_a, vlan_b = ia.get("vlan_id"), ib.get("vlan_id")
          shared_vlan = bool(ia.get("is_vlanif") and ib.get("is_vlanif") and vlan_a == vlan_b)
          vlan_segment = shared_vlan or bool(ia.get("is_vlanif") != ib.get("is_vlanif"))
          parent_a = ia.get("parent", ap)
          parent_b = ib.get("parent", bp)
          direct_link = (b, parent_b) in physical.get((a, parent_a), set())
          if vlan_segment:
            try:
              vlan_id = int(vlan_a if ia.get("is_vlanif") else vlan_b)
              reachable = path_allows_vlan(conn, username, a, b, vlan_id)
              if isinstance(reachable, tuple):
                reachable = reachable[0]
            except (TypeError, ValueError):
              reachable = False
          else:
            # A routed interface must be physically adjacent; a VLANIF can also
            # peer with a routed port when both are on the same directly connected segment.
            reachable = direct_link
          if not reachable:
            continue
          neighbors[a].append({"peer": b, "interface": ap, "peer_interface": bp,
                               "process": aa["process"],
                               "area": aa["area"], "address": str(ipb), "router_id": router_ids[b]})
          neighbors[b].append({"peer": a, "interface": bp, "peer_interface": ap,
                               "process": ab["process"],
                               "area": ab["area"], "address": str(ipa), "router_id": router_ids[a]})
          edges[a].append((b, ap, bp, max(1, int(ia.get("ospf_cost", 1)))))
          edges[b].append((a, bp, ap, max(1, int(ib.get("ospf_cost", 1)))))

  routes = {name: [] for name in devices}
  for source in devices:
    # OSPF SPF runs per area. This lab uses single-area OSPF; keep areas isolated.
    for area_id in sorted({item["area"] for item in active[source].values()}):
      dist = {source: 0}
      first_hops = {source: set()}
      queue = [(0, source)]
      while queue:
        cost, node = heapq.heappop(queue)
        if cost != dist.get(node):
          continue
        for peer, out_if, peer_if, weight in edges.get(node, []):
          if active[node].get(out_if, {}).get("area") != area_id:
            continue
          if active[peer].get(peer_if, {}).get("area") != area_id:
            continue
          candidate = cost + weight
          if candidate < dist.get(peer, 1 << 30):
            dist[peer] = candidate
            first_hops[peer] = {(peer, out_if)} if node == source else set(first_hops.get(node, set()))
            heapq.heappush(queue, (candidate, peer))
          elif candidate == dist.get(peer):
            first_hops.setdefault(peer, set()).update(
                {(peer, out_if)} if node == source else first_hops.get(node, set())
            )

      for origin, device in devices.items():
        origin_ifs = active[origin]
        for ifname, item in origin_ifs.items():
          if item["area"] != area_id:
            continue
          iface = item["iface"]
          try:
            address = ipaddress.IPv4Address(iface["ip"])
            prefix = 32 if iface.get("is_loopback") else ipaddress.IPv4Network(
                f"{address}/{iface.get('mask') or '24'}", strict=False).prefixlen
            network = ipaddress.IPv4Network(f"{address}/{prefix}", strict=False)
          except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
            continue
          if origin == source:
            routes[source].append({"network": network, "protocol": "OSPF", "preference": 10,
                                  "cost": 0 if iface.get("is_loopback") else max(1, int(iface.get("ospf_cost", 1))), "next_hop": "0.0.0.0",
                                  "interface": ifname, "active": False, "process": item["process"], "origin": origin})
          elif origin in dist:
            for nh_router, out_if in first_hops.get(origin, set()):
              peer = next((item for item in neighbors[source] if item["peer"] == nh_router and item["interface"] == out_if), None)
              if peer:
                # An ABR's area export filter suppresses matching inter-area routes.
                source_area = active[source].get(out_if, {}).get("area")
                if item["area"] != source_area and _export_filtered(devices[source], network):
                  continue
                route = {"network": network, "protocol": "OSPF", "preference": 10,
                                      "cost": dist[origin] + (0 if iface.get("is_loopback") else max(1, int(iface.get("ospf_cost", 1)))),
                                      "next_hop": peer["address"], "interface": out_if, "active": True,
                                      "process": active[source].get(out_if, {}).get("process"), "origin": origin}
                source_process = devices[source]["processes"].get(active[source].get(out_if, {}).get("process"), {})
                if (source_process.get("frr", {}).get("loop_free_alternate") and
                    active[source].get(out_if, {}).get("process") == peer.get("process")):
                  primary_peers = {entry[0] for entry in first_hops.get(origin, set())}
                  for alt_peer, alt_if, alt_peer_if, alt_weight in edges.get(source, []):
                    if alt_peer in primary_peers or active[source].get(alt_if, {}).get("area") != source_area:
                      continue
                    alt_dist, alt_queue = {alt_peer: 0}, [(0, alt_peer)]
                    while alt_queue:
                      current_cost, current = heapq.heappop(alt_queue)
                      if current_cost != alt_dist.get(current):
                        continue
                      for next_node, _out, _peerif, weight in edges.get(current, []):
                        if next_node == source:
                          continue
                        candidate = current_cost + weight
                        if candidate < alt_dist.get(next_node, 1 << 30):
                          alt_dist[next_node] = candidate
                          heapq.heappush(alt_queue, (candidate, next_node))
                    if origin in alt_dist:
                      alt_link = next((row for row in neighbors[source] if row["peer"] == alt_peer and row["interface"] == alt_if), None)
                      if alt_link:
                        route["backup_next_hop"] = alt_link["address"]
                        route["backup_interface"] = alt_if
                        route["backup_type"] = "LFA LINK-NODE"
                        break
                routes[source].append(route)

      # An always-advertised default is an external OSPF route for peers.
      for origin, device in devices.items():
        for process in device["processes"].values():
          advertise = process.get("default_route_advertise", {})
          if not advertise.get("enabled"):
            continue
          has_existing_default = any(
              route.get("destination") == "0.0.0.0" and route.get("mask") in ("0.0.0.0", "0")
              for route in device["cfg"].get("static_routes", [])
          ) or any(iface.get("dhcp_gateway") for iface in (device["cfg"].get("interfaces") or {}).values())
          if not advertise.get("always") and not has_existing_default:
            continue
          if origin == source or origin not in dist:
            continue
          for nh_router, out_if in first_hops.get(origin, set()):
            peer = next((item for item in neighbors[source] if item["peer"] == nh_router and item["interface"] == out_if), None)
            if peer:
              routes[source].append({"network": ipaddress.IPv4Network("0.0.0.0/0"), "protocol": "O_ASE",
                                    "preference": 150, "cost": 1, "next_hop": peer["address"],
                                    "interface": out_if, "active": True})
  # ABRs join area-specific SPF trees and advertise inter-area prefixes.
  for source in devices:
    dist, first_hops, previous = {source: 0}, {source: set()}, {}
    queue = [(0, source)]
    while queue:
      cost, node = heapq.heappop(queue)
      if cost != dist.get(node):
        continue
      for peer, out_if, peer_if, weight in edges.get(node, []):
        candidate = cost + weight
        if candidate < dist.get(peer, 1 << 30):
          dist[peer] = candidate
          first_hops[peer] = {(peer, out_if)} if node == source else set(first_hops.get(node, set()))
          previous[peer] = node
          heapq.heappush(queue, (candidate, peer))
        elif candidate == dist.get(peer):
          first_hops.setdefault(peer, set()).update({(peer, out_if)} if node == source else first_hops.get(node, set()))
    source_areas = {item["area"] for item in active[source].values()}
    for origin, device in devices.items():
      if origin == source or origin not in dist:
        continue
      for ifname, item in active[origin].items():
        if item["area"] in source_areas:
          continue
        iface = item["iface"]
        if not iface.get("ip"):
          continue
        try:
          address = ipaddress.IPv4Address(iface["ip"])
          prefix = 32 if iface.get("is_loopback") else ipaddress.IPv4Network(f"{address}/{iface.get('mask') or '24'}", strict=False).prefixlen
          network = ipaddress.IPv4Network(f"{address}/{prefix}", strict=False)
        except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
          continue
        path, cursor = [origin], origin
        while cursor != source and cursor in previous:
          cursor = previous[cursor]
          path.append(cursor)
        suppressed = False
        for abr in path[1:-1]:
          if len({item["area"] for item in active.get(abr, {}).values()}) > 1 and _area_export_suppresses(devices[abr], item["area"], network):
            suppressed = True
            break
        if suppressed:
          continue
        for next_router, out_if in first_hops.get(origin, set()):
          peer = next((row for row in neighbors[source] if row["peer"] == next_router and row["interface"] == out_if), None)
          if peer:
            route = {"network": network, "protocol": "OSPF", "preference": 10,
                     "cost": dist[origin] + max(0, int(iface.get("ospf_cost", 1))),
                     "next_hop": peer["address"], "interface": out_if, "active": True,
                     "area_type": "Inter-area", "process": active[source].get(out_if, {}).get("process"), "origin": origin}
            if not any(existing["network"] == network and existing.get("next_hop") == route["next_hop"] for existing in routes[source]):
              routes[source].append(route)

  # Respect the configured ECMP limit per OSPF process.
  for name, entries in routes.items():
    groups = {}
    for row in entries:
      if row.get("protocol") == "OSPF" and row.get("active"):
        groups.setdefault(str(row["network"]), []).append(row)
    for candidates in groups.values():
      if len(candidates) <= 1:
        continue
      process_id = active[name].get(candidates[0]["interface"], {}).get("process")
      maximum = int(devices[name]["processes"].get(process_id, {}).get("maximum_load_balancing", 8))
      candidates.sort(key=lambda row: (row.get("next_hop", ""), row.get("interface", "")))
      for row in candidates[maximum:]:
        row["active"] = False
  return {"devices": devices, "active": active, "router_ids": router_ids,
          "neighbors": neighbors, "routes": routes}
