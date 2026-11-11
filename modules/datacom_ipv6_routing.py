"""IPv6 control-plane approximation for the HCIE IPv6 routing lab."""
import ipaddress
import json
from collections import deque


def ipv6_routing_state(conn, username):
  devices = {}
  for name, kind, raw in conn.execute("SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (username,)).fetchall():
    try: cfg = json.loads(raw)
    except (TypeError, ValueError): continue
    devices[name] = {"kind": kind or "", "cfg": cfg}
  from modules.datacom_network import datacom_topology_links
  links, _ = datacom_topology_links(conn, username)
  addresses = {}
  for node, device in devices.items():
    for ifname, iface in (device["cfg"].get("interfaces") or {}).items():
      rows = []
      for item in iface.get("ipv6_addresses", []):
        try:
          address = ipaddress.IPv6Interface(f"{item['address']}/{item['prefix']}")
          if not address.ip.is_link_local:
            rows.append((address.ip, address.network, ifname))
        except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError): pass
      addresses.setdefault(node, []).extend(rows)

  adj = {name: [] for name in devices}
  ospf_peers = {name: [] for name in devices}
  isis_peers = {name: [] for name in devices}
  for a, b, ap, bp, up in links:
    if not up or a not in devices or b not in devices: continue
    ca, cb = devices[a]["cfg"], devices[b]["cfg"]
    ia = (ca.get("interfaces") or {}).get(ap, {})
    ib = (cb.get("interfaces") or {}).get(bp, {})
    if ia.get("shutdown") or ib.get("shutdown"): continue
    pa = ca.get("ospfv3_processes", {}).get(str(ia.get("ospfv3_process")), {}) if ia.get("ospfv3_process") is not None else {}
    pb = cb.get("ospfv3_processes", {}).get(str(ib.get("ospfv3_process")), {}) if ib.get("ospfv3_process") is not None else {}
    if (pa and pb and ia.get("ospfv3_process") == ib.get("ospfv3_process") and
        ia.get("ospfv3_area", "0") == ib.get("ospfv3_area", "0") and
        int(ia.get("ospfv3_instance", 1)) == int(ib.get("ospfv3_instance", 1))):
      adj[a].append((b, ap, bp, "OSPFv3")); adj[b].append((a, bp, ap, "OSPFv3"))
      ospf_peers[a].append({"router_id": pb.get("router_id") or "0.0.0.0", "interface": ap, "instance": ia.get("ospfv3_instance", 1)})
      ospf_peers[b].append({"router_id": pa.get("router_id") or "0.0.0.0", "interface": bp, "instance": ib.get("ospfv3_instance", 1)})
    pida, pidb = ia.get("isis_ipv6_process"), ib.get("isis_ipv6_process")
    isa = ca.get("isis_processes", {}).get(str(pida), {}) if pida is not None else {}
    isb = cb.get("isis_processes", {}).get(str(pidb), {}) if pidb is not None else {}
    if isa.get("ipv6_enabled") and isb.get("ipv6_enabled") and pida == pidb:
      adj[a].append((b, ap, bp, "ISIS")); adj[b].append((a, bp, ap, "ISIS"))
      isis_peers[a].append({"peer_id": isa.get("is_name") or b, "interface": ap})
      isis_peers[b].append({"peer_id": isb.get("is_name") or a, "interface": bp})

  route_rows = {name: [] for name in devices}
  # Connected IPv6 prefixes are reachable across configured OSPFv3/IS-IS adjacencies.
  for source, prefixes in addresses.items():
    for address, network, origin_if in prefixes:
      for target in devices:
        if target == source:
          route_rows[target].append({"destination": network, "protocol": "Direct", "next_hop": "::", "interface": origin_if, "cost": 0})
          continue
        queue = deque([(source, None, None, 0, [], [source])]); seen = {source}; found = None
        while queue:
          node, first_if, next_hop, cost, path_protocols, path_nodes = queue.popleft()
          if node == target:
            found = (first_if, next_hop, cost, path_protocols, path_nodes); break
          for peer, out_if, peer_if, proto in adj.get(node, []):
            if peer in seen: continue
            seen.add(peer)
            peer_addr = next((str(item[0]) for item in addresses.get(peer, []) if item[2] == peer_if), "::")
            queue.append((peer, out_if if first_if is None else first_if,
                          peer_addr if next_hop is None else next_hop, cost + 10,
                          path_protocols + [proto], path_nodes + [peer]))
        if found:
          out_if, next_hop, cost, protocols, path_nodes = found
          protocol = protocols[-1] if protocols else "OSPFv3"
          # A protocol route crosses another protocol domain only when the border router imports it.
          permitted = True
          for index, proto in enumerate(protocols[:-1]):
            next_proto = protocols[index + 1]
            if proto == next_proto: continue
            border = path_nodes[index + 1]
            if proto == "OSPFv3":
              permitted = any(item.get("protocol") == "isis" for p in devices[border]["cfg"].get("ospfv3_processes", {}).values() for item in p.get("imports", []))
            else:
              permitted = any(item.get("protocol") == "ospfv3" for p in devices[border]["cfg"].get("isis_processes", {}).values() for item in p.get("ipv6_imports", []))
            if not permitted: break
          if permitted:
            route_rows[target].append({"destination": network, "protocol": protocol, "next_hop": next_hop or "::", "interface": out_if or "-", "cost": cost})

  # IPv6 BGP sessions are modeled for mutually configured IPv6-family peers; routes are exchanged as prefixes.
  bgp_peers = {name: [] for name in devices}; bgp_routes = {name: [] for name in devices}
  def owner(address):
    try: target = ipaddress.IPv6Address(address)
    except (ValueError, TypeError): return None
    for node, rows in addresses.items():
      for ip, _network, ifname in rows:
        if ip == target: return node, ifname
    return None
  sessions = []
  for name, device in devices.items():
    bgp = device["cfg"].get("bgp") or {}
    for peer, info in bgp.get("peers", {}).items():
      peer_owner = owner(peer)
      families = info.get("address_families", {})
      if not peer_owner or not families.get("ipv6"): continue
      remote, remote_if = peer_owner
      if remote == name: continue
      remote_bgp = devices[remote]["cfg"].get("bgp") or {}
      local_source = (device["cfg"].get("interfaces") or {}).get(info.get("connect_interface", ""), {})
      if not local_source.get("ipv6_enabled"): continue
      local_ip = next((str(ip) for ip, _net, ifn in addresses.get(name, []) if ifn == info.get("connect_interface")), None)
      remote_info = (remote_bgp.get("peers") or {}).get(local_ip, {})
      if not local_ip or remote_info.get("as_number") != bgp.get("as_number") or not remote_info.get("address_families", {}).get("ipv6"): continue
      if int(info.get("as_number") or 0) != int(remote_bgp.get("as_number") or 0): continue
      if not any(row[0] == remote and row[1] == name for row in sessions):
        sessions.append((name, remote, peer, local_ip))
        bgp_peers[name].append({"address": peer, "as_number": remote_bgp.get("as_number")})
  for name, device in devices.items():
    bgp = device["cfg"].get("bgp") or {}
    for prefix in bgp.get("ipv6_networks", []):
      try: net = ipaddress.IPv6Network(prefix, strict=False)
      except (ValueError, ipaddress.AddressValueError): continue
      if any(net == row[1] for row in addresses.get(name, [])):
        bgp_routes[name].append({"destination": net, "next_hop": "::", "source": name})
    for imported in bgp.get("ipv6_imports", []):
      protocol = {"isis": "ISIS", "ospfv3": "OSPFv3"}.get(imported.get("protocol"))
      if protocol:
        for route in route_rows.get(name, []):
          if route["protocol"] == protocol and not any(item["destination"] == route["destination"] for item in bgp_routes[name]):
            bgp_routes[name].append({"destination": route["destination"], "next_hop": "::", "source": name})
  for _ in range(max(1, len(devices) * 3)):
    changed = False
    for local, remote, remote_ip, local_ip in sessions:
      for sender, receiver in ((local, remote), (remote, local)):
        sender_bgp = devices[sender]["cfg"].get("bgp") or {}
        receiver_bgp = devices[receiver]["cfg"].get("bgp") or {}
        for route in list(bgp_routes[sender]):
          if route["source"] == receiver: continue
          propagated = {**route, "next_hop": local_ip if sender == local else remote_ip, "source": sender}
          if not any(row["destination"] == propagated["destination"] and row["source"] == sender for row in bgp_routes[receiver]):
            bgp_routes[receiver].append(propagated); changed = True
    if not changed: break
  # A BGP prefix imported at an IS-IS/OSPFv3 border is carried onward through
  # the matching underlay, including a protocol transition at the other border.
  for source, device in devices.items():
    bgp = device["cfg"].get("bgp") or {}
    for route in bgp_routes.get(source, []):
      if route.get("source") == source:
        continue
      isis_imports_bgp = any(item.get("protocol") == "bgp" for p in device["cfg"].get("isis_processes", {}).values() for item in p.get("ipv6_imports", []))
      ospf_imports_bgp = any(item.get("protocol") == "bgp" for p in device["cfg"].get("ospfv3_processes", {}).values() for item in p.get("imports", []))
      if not (isis_imports_bgp or ospf_imports_bgp):
        continue
      for target in devices:
        if target == source: continue
        queue = deque([(source, None, None, [], [source])]); seen = {source}; path = None
        while queue:
          node, first, next_hop, protos, nodes = queue.popleft()
          if node == target:
            path = (first, next_hop, protos, nodes); break
          for peer, out_if, peer_if, proto in adj.get(node, []):
            if peer in seen: continue
            seen.add(peer)
            peer_ip = next((str(item[0]) for item in addresses.get(peer, []) if item[2] == peer_if), "::")
            queue.append((peer, out_if if first is None else first, peer_ip if next_hop is None else next_hop,
                          protos + [proto], nodes + [peer]))
        if not path: continue
        first, next_hop, protos, nodes = path
        allowed = True
        for index in range(1, len(protos)):
          if protos[index] == protos[index - 1]: continue
          border = nodes[index]
          if protos[index] == "OSPFv3":
            allowed = any(item.get("protocol") == "isis" for p in devices[border]["cfg"].get("ospfv3_processes", {}).values() for item in p.get("imports", []))
          else:
            allowed = any(item.get("protocol") == "ospfv3" for p in devices[border]["cfg"].get("isis_processes", {}).values() for item in p.get("ipv6_imports", []))
          if not allowed: break
        if allowed:
          protocol = protos[-1] if protos else ("ISIS" if isis_imports_bgp else "OSPFv3")
          if protocol in ("ISIS", "OSPFv3") and not any(row["destination"] == route["destination"] for row in route_rows[target]):
            route_rows[target].append({"destination": route["destination"], "protocol": protocol,
                                       "next_hop": next_hop or "::", "interface": first or "-", "cost": 10 * len(protos)})
  for node in devices:
    for row in bgp_routes[node]:
      route_rows[node].append({"destination": row["destination"], "protocol": "BGP4+", "next_hop": row["next_hop"], "interface": "-", "cost": 0})
  return {"devices": devices, "routes": route_rows, "ospfv3_peers": ospf_peers,
          "isis_peers": isis_peers, "bgp6_peers": bgp_peers, "bgp6_routes": bgp_routes}
