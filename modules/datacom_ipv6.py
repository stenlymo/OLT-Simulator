"""IPv6 address assignment, Neighbor Discovery and route simulation for Datacom labs."""
import ipaddress
import json
import re


def _devices(conn, username):
  result = {}
  for name, kind, raw in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (username,)
  ).fetchall():
    try:
      result[name] = {"kind": kind or "", "cfg": json.loads(raw)}
    except (TypeError, ValueError):
      continue
  return result


def _eui64(node_name, ifname):
  from modules.datacom_dhcp import interface_mac
  mac = re.sub(r"[^0-9a-f]", "", interface_mac(node_name, ifname).lower())
  octets = [int(mac[i:i + 2], 16) for i in range(0, 12, 2)]
  octets[0] ^= 0x02
  iid = octets[:3] + [0xff, 0xfe] + octets[3:]
  return ipaddress.IPv6Address(int.from_bytes(bytes(iid), "big"))


def link_local(node_name, ifname):
  return ipaddress.IPv6Address(int(ipaddress.IPv6Address("fe80::")) | int(_eui64(node_name, ifname)))


def _direct_peer_addresses(conn, username, devices, node, ifname):
  from modules.datacom_network import datacom_topology_links
  links, _ = datacom_topology_links(conn, username)
  peers = []
  for a, b, ap, bp, up in links:
    if not up:
      continue
    if a == node and ap == ifname:
      peers.append((b, bp))
    elif b == node and bp == ifname:
      peers.append((a, ap))
  return peers


def interface_addresses(conn, username, devices=None):
  """Return calculated IPv6 addresses keyed by node and interface."""
  devices = devices or _devices(conn, username)
  addresses = {}
  for node, device in devices.items():
    for ifname, iface in (device["cfg"].get("interfaces") or {}).items():
      if not iface.get("ipv6_enabled") or iface.get("shutdown"):
        continue
      rows = []
      configured = iface.get("ipv6_addresses") or []
      # A global IPv6 address causes automatic link-local address generation.
      needs_link_local = iface.get("ipv6_link_local_auto") or iface.get("ipv6_auto_global") or bool(configured)
      if needs_link_local:
        rows.append({"address": str(link_local(node, ifname)), "prefix": 10, "origin": "link-local"})
      for item in configured:
        try:
          interface = ipaddress.IPv6Interface(f"{item['address']}/{item['prefix']}")
        except (KeyError, ValueError, ipaddress.AddressValueError):
          continue
        rows.append({"address": str(interface.ip), "prefix": interface.network.prefixlen, "origin": "static"})
      if iface.get("ipv6_auto_global"):
        for peer, peer_if in _direct_peer_addresses(conn, username, devices, node, ifname):
          peer_iface = (devices.get(peer, {}).get("cfg", {}).get("interfaces") or {}).get(peer_if, {})
          ra = peer_iface.get("ipv6_ra_prefix")
          if (not peer_iface.get("ipv6_enabled") or peer_iface.get("ipv6_ra_halt", True)
              or not ra or peer_iface.get("shutdown")):
            continue
          try:
            network = ipaddress.IPv6Network(ra["prefix"], strict=False)
            if network.prefixlen != 64:
              continue
            global_addr = ipaddress.IPv6Address(int(network.network_address) | int(_eui64(node, ifname)))
          except (KeyError, ValueError, ipaddress.AddressValueError):
            continue
          rows.append({"address": str(global_addr), "prefix": 64, "origin": "SLAAC",
                       "valid_lifetime": int(ra.get("valid_lifetime", 3600)),
                       "preferred_lifetime": int(ra.get("preferred_lifetime", 1200)),
                       "router": str(link_local(peer, peer_if)), "router_node": peer,
                       "router_interface": peer_if})
          break
      addresses[(node, ifname)] = rows
  return addresses


def ipv6_routing_table(conn, username, node_name):
  devices = _devices(conn, username)
  if node_name not in devices:
    return []
  from modules.datacom_network import datacom_topology_links
  links, _ = datacom_topology_links(conn, username)
  up_ports = set()
  for a, b, ap, bp, up in links:
    if up:
      up_ports.update(((a, ap), (b, bp)))
  addresses = interface_addresses(conn, username, devices)
  table = []
  cfg = devices[node_name]["cfg"]
  for ifname, iface in (cfg.get("interfaces") or {}).items():
    if not iface.get("ipv6_enabled") or iface.get("shutdown"):
      continue
    if not iface.get("is_loopback") and (node_name, ifname) not in up_ports:
      continue
    for address in addresses.get((node_name, ifname), []):
      prefix = 10 if address["origin"] == "link-local" else address["prefix"]
      network = ipaddress.IPv6Network(f"{address['address']}/{prefix}", strict=False)
      table.append({"destination": network, "protocol": "Direct", "preference": 0, "cost": 0,
                    "next_hop": "::1", "interface": ifname, "active": True})
    if iface.get("ipv6_auto_global_default"):
      for address in addresses.get((node_name, ifname), []):
        if address["origin"] == "SLAAC":
          table.append({"destination": ipaddress.IPv6Network("::/0"), "protocol": "ND", "preference": 64,
                        "cost": 0, "next_hop": address["router"], "interface": ifname, "active": True})
          break
  for route in cfg.get("ipv6_static_routes", []):
    try:
      destination = ipaddress.IPv6Network(f"{route['destination']}/{route['prefix']}", strict=False)
      next_hop = ipaddress.IPv6Address(route["next_hop"])
    except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
      continue
    candidates = [item for item in table if item["protocol"] == "Direct" and next_hop in item["destination"]]
    out_if = None
    for candidate in candidates:
      for peer, peer_port in _direct_peer_addresses(conn, username, devices, node_name, candidate["interface"]):
        if any(address["address"] == str(next_hop) for address in addresses.get((peer, peer_port), [])):
          out_if = candidate["interface"]
          break
      if out_if:
        break
    table.append({"destination": destination, "protocol": "Static", "preference": int(route.get("preference", 60)),
                  "cost": 0, "next_hop": str(next_hop), "interface": out_if or "--", "active": out_if is not None})
  try:
    from modules.datacom_ipv6_routing import ipv6_routing_state
    for route in ipv6_routing_state(conn, username).get("routes", {}).get(node_name, []):
      if route.get("protocol") == "Direct":
        continue
      preference = {"OSPFv3": 10, "ISIS": 15, "BGP4+": 255}.get(route["protocol"], 100)
      table.append({"destination": route["destination"], "protocol": route["protocol"],
                    "preference": preference, "cost": route.get("cost", 0),
                    "next_hop": route.get("next_hop", "::"), "interface": route.get("interface", "-"),
                    "active": True})
  except (ImportError, ValueError, TypeError):
    pass
  groups = {}
  for route in table:
    groups.setdefault(str(route["destination"]), []).append(route)
  for routes in groups.values():
    active = [item for item in routes if item["active"]]
    if not active:
      continue
    best = min(item["preference"] for item in active)
    for item in routes:
      item["active"] = item in active and item["preference"] == best
  return sorted(table, key=lambda item: (-item["destination"].prefixlen, item["preference"],
                                         int(item["destination"].network_address), item["interface"]))


def _owner(devices, addresses, address):
  for (node, ifname), rows in addresses.items():
    if any(item["address"] == str(address) for item in rows):
      return node, ifname
  return None


def _resolve_path(conn, username, devices, addresses, current, target, seen=None, interface_hint=None):
  from modules.datacom_network import _topology_path_exists
  seen = set(seen or ())
  if current in seen:
    return None
  seen.add(current)
  owner = _owner(devices, addresses, target)
  if owner and owner[0] == current:
    return []
  routes = [item for item in ipv6_routing_table(conn, username, current)
            if item["active"] and target in item["destination"]
            and (interface_hint is None or item["interface"] == interface_hint)]
  routes.sort(key=lambda item: (-item["destination"].prefixlen, item["preference"], item["cost"]))
  if not routes:
    return None
  route = routes[0]
  if route["protocol"] == "Direct":
    if owner and _topology_path_exists(conn, username, current, owner[0]):
      return [(route["interface"], owner[0], owner[1])]
    return None
  next_hop = route["next_hop"]
  next_owner = _owner(devices, addresses, next_hop)
  if not next_owner:
    return None
  if not _topology_path_exists(conn, username, current, next_owner[0]):
    return None
  tail = _resolve_path(conn, username, devices, addresses, next_owner[0], target, seen)
  if tail is None:
    return None
  return [(route["interface"], next_owner[0], next_owner[1])] + tail


def ping_ipv6(conn, username, source_node, destination, source_interface=None):
  devices = _devices(conn, username)
  addresses = interface_addresses(conn, username, devices)
  try:
    target = ipaddress.IPv6Address(destination)
  except ipaddress.AddressValueError:
    return False, None, []
  source_interfaces = [(name, rows) for (node, name), rows in addresses.items()
                       if node == source_node and (source_interface is None or name == source_interface)]
  if not source_interfaces:
    return False, None, []
  if target.is_link_local:
    if not source_interface:
      return False, None, []
    source_if = source_interface
    source_link_local = next((item["address"] for item in dict(addresses).get((source_node, source_if), [])
                              if item["origin"] == "link-local"), None)
    if not source_link_local:
      return False, None, []
    path = _resolve_path(conn, username, devices, addresses, source_node, target, interface_hint=source_if)
    return path is not None, source_link_local, path or []
  source_candidates = [(ifname, item) for ifname, rows in source_interfaces for item in rows
                       if not ipaddress.IPv6Address(item["address"]).is_link_local]
  if not source_candidates:
    return False, None, []
  source_if, source_address = source_candidates[0]
  path = _resolve_path(conn, username, devices, addresses, source_node, target)
  if path is None:
    return False, source_address["address"], []
  owner = _owner(devices, addresses, target)
  if not owner:
    return False, source_address["address"], []
  return_path = _resolve_path(conn, username, devices, addresses, owner[0], ipaddress.IPv6Address(source_address["address"]))
  return return_path is not None, source_address["address"], path


def remember_neighbors(conn, username, source_node, path, destination):
  devices = _devices(conn, username)
  addresses = interface_addresses(conn, username, devices)
  current = source_node
  for ifname, peer, peer_if in path:
    if current not in devices or peer not in devices:
      continue
    cfg = devices[current]["cfg"]
    peer_ip = next((item["address"] for item in addresses.get((peer, peer_if), [])
                    if item["origin"] != "link-local"), destination)
    if ipaddress.IPv6Address(peer_ip).is_link_local:
      peer_ip = next((item["address"] for item in addresses.get((peer, peer_if), [])
                      if item["origin"] == "link-local"), peer_ip)
    from modules.datacom_dhcp import interface_mac
    cfg.setdefault("ipv6_neighbors", {})[f"{ifname}|{peer_ip}"] = {
        "address": peer_ip, "mac": interface_mac(peer, peer_if), "interface": ifname, "state": "REACH"
    }
    conn.execute("UPDATE olt_datacom_devices SET running_json=? WHERE username=? AND node_name=?",
                 (json.dumps(cfg), username, current))
    current = peer
  conn.commit()
