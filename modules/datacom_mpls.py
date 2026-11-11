"""Lightweight MPLS LDP and L3VPN control-plane model for the HCIE lab."""
import ipaddress
import json


def _devices(conn, username):
  result = {}
  for name, kind, raw in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (username,)
  ).fetchall():
    try:
      cfg = json.loads(raw)
    except (TypeError, ValueError):
      continue
    result[name] = {"kind": kind or "", "cfg": cfg}
  return result


def _address_owner(devices, address):
  for name, device in devices.items():
    for ifname, iface in (device["cfg"].get("interfaces") or {}).items():
      if iface.get("ip") == address:
        return name, ifname, iface
  return None


def _mask(iface):
  try:
    return ipaddress.IPv4Network(f"{iface['ip']}/{iface.get('mask') or '32'}", strict=False)
  except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
    return None


def _policy_permits(config, name, network):
  if not name:
    return True
  nodes = (config.get("route_policies") or {}).get(name, {})
  prefix_lists = config.get("ip_prefix_lists") or {}
  for _index, node in sorted(nodes.items(), key=lambda pair: int(pair[0])):
    list_name = node.get("ip_prefix")
    if not list_name:
      return node.get("action", "permit") == "permit"
    matched = False
    for entry in sorted(prefix_lists.get(list_name, []), key=lambda row: int(row.get("index", 0))):
      try:
        candidate = ipaddress.IPv4Network(entry["network"], strict=False)
      except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
        continue
      if network.subnet_of(candidate) and int(entry.get("ge", candidate.prefixlen)) <= network.prefixlen <= int(entry.get("le", 32)):
        matched = entry.get("action", "permit") == "permit"
        break
    if matched:
      return node.get("action", "permit") == "permit"
  return False


def mpls_state(conn, username):
  """Return operational LDP adjacencies and VPNv4 route views.

  This intentionally models the lab control plane. Label switching is inferred
  from enabled LDP links; it does not construct packet-level MPLS frames.
  """
  devices = _devices(conn, username)
  try:
    from modules.datacom_srmpls import sr_state
    sr_policies = sr_state(conn, username).get("policies", {})
  except Exception:
    sr_policies = {}
  try:
    from modules.datacom_srv6 import srv6_state
    srv6_policies = srv6_state(conn, username).get("policies", {})
  except Exception:
    srv6_policies = {}
  links = []
  try:
    from modules.datacom_network import datacom_topology_links
    links, _ = datacom_topology_links(conn, username)
  except Exception:
    pass
  ldp = {name: [] for name in devices}
  for a, b, ap, bp, up in links:
    if not up:
      continue
    ca = devices.get(a, {}).get("cfg", {})
    cb = devices.get(b, {}).get("cfg", {})
    ia = (ca.get("interfaces") or {}).get(ap, {})
    ib = (cb.get("interfaces") or {}).get(bp, {})
    if not (ca.get("mpls_enabled") and ca.get("mpls_ldp_enabled") and cb.get("mpls_enabled") and cb.get("mpls_ldp_enabled")
            and ia.get("mpls_enabled") and ia.get("mpls_ldp_enabled") and ib.get("mpls_enabled") and ib.get("mpls_ldp_enabled")):
      continue
    aid = ca.get("mpls_lsr_id") or ca.get("bgp", {}).get("router_id", "")
    bid = cb.get("mpls_lsr_id") or cb.get("bgp", {}).get("router_id", "")
    if not aid or not bid:
      continue
    ldp[a].append({"peer_id": bid, "source": ia.get("ip", "-"), "interface": ap, "state": "Operational"})
    ldp[b].append({"peer_id": aid, "source": ib.get("ip", "-"), "interface": bp, "state": "Operational"})

  instances = {}
  for name, device in devices.items():
    for vpn_name, raw in (device["cfg"].get("vpn_instances") or {}).items():
      instance = dict(raw)
      instance.update({"device": name, "name": vpn_name, "routes": []})
      instances[(name, vpn_name)] = instance

  # Locally connected prefixes bound to a VPN instance are VPN routes.
  for name, device in devices.items():
    cfg = device["cfg"]
    for ifname, iface in (cfg.get("interfaces") or {}).items():
      vpn_name = iface.get("vpn_instance")
      key = (name, vpn_name)
      network = _mask(iface)
      if vpn_name and key in instances and network:
        instances[key]["routes"].append({"network": network, "next_hop": iface["ip"], "source": name,
                                         "protocol": "Direct", "as_path": [], "rd": instances[key].get("rd", "")})

  # CE BGP networks are injected into a VPN instance when a configured VPN AF
  # peer points at that CE's directly connected address.
  for (name, vpn_name), instance in instances.items():
    cfg = devices[name]["cfg"]
    for peer_ip, peer_data in (instance.get("bgp_peers") or {}).items():
      if not peer_data.get("as_number"):
        continue
      owner = _address_owner(devices, peer_ip)
      if not owner or owner[0] == name:
        continue
      peer_cfg = devices[owner[0]]["cfg"]
      peer_bgp = peer_cfg.get("bgp") or {}
      if int(peer_bgp.get("as_number") or 0) != int(peer_data.get("as_number") or 0):
        continue
      for prefix in peer_bgp.get("networks", []):
        try:
          network = ipaddress.IPv4Network(prefix["network"] if "/" in str(prefix["network"]) else f"{prefix['network']}/{prefix.get('mask','32')}", strict=False)
        except (KeyError, ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError):
          continue
        instance["routes"].append({"network": network, "next_hop": peer_ip, "source": owner[0],
                                   "protocol": "BGP-CE", "as_path": [int(peer_data["as_number"])], "rd": instance.get("rd", "")})

  # Importing OSPF routes into a VPN AF is the route exchange used by Branch 1.
  if any(instance.get("import_ospf") for instance in instances.values()):
    try:
      from modules.datacom_ospf import ospf_state
      ospf_routes = ospf_state(conn, username).get("routes", {})
    except Exception:
      ospf_routes = {}
    for (name, _vpn_name), instance in instances.items():
      for import_cfg in instance.get("import_ospf", []):
        for route in ospf_routes.get(name, []):
          if str(route.get("process", "1")) != str(import_cfg.get("process", "1")):
            continue
          if not _policy_permits(devices[name]["cfg"], import_cfg.get("route_policy", ""), route["network"]):
            continue
          instance["routes"].append({"network": route["network"], "next_hop": route.get("next_hop", "-"),
                                     "source": route.get("origin", name), "protocol": "OSPF", "as_path": [],
                                     "rd": instance.get("rd", "")})

  # RT matching represents VPNv4 MP-BGP distribution through the configured
  # provider iBGP/RR domain, provided every participating PE enabled VPNv4.
  def vpn_transport_enabled(instance):
    cfg = devices[instance["device"]]["cfg"]
    bgp = cfg.get("bgp") or {}
    sr_ready = bool((cfg.get("segment_routing") or {}).get("enabled") and any(
        process.get("segment_routing_mpls") for process in (cfg.get("isis_processes") or {}).values()) and any(
        iface.get("is_loopback") and iface.get("isis_prefix_sid_index")
        for iface in (cfg.get("interfaces") or {}).values()))
    srv6 = cfg.get("srv6") or {}
    srv6_ready = bool(srv6.get("enabled") and instance.get("srv6_locator") in srv6.get("locators", {}) and
                      any(process.get("srv6_locator") == instance.get("srv6_locator") and process.get("ipv6_enabled")
                          for process in (cfg.get("isis_processes") or {}).values()))
    srv6_policy_name = instance.get("srv6_policy")
    if srv6_policy_name and not any(row.get("name") == srv6_policy_name and row.get("active")
                                    for row in srv6_policies.get(instance["device"], [])):
      srv6_ready = False
    if not (cfg.get("mpls_enabled") and (cfg.get("mpls_ldp_enabled") or sr_ready) or srv6_ready):
      return False
    tunnel_policy = instance.get("tunnel_policy")
    if tunnel_policy:
      policy_config = (cfg.get("tunnel_policies") or {}).get(tunnel_policy, {})
      if "sr-te" in policy_config.get("protocols", []):
        color = instance.get("default_color")
        if not any(policy.get("active") and (color is None or int(policy.get("color", -1)) == int(color))
                   for policy in sr_policies.get(instance["device"], [])):
          return False
    if bgp.get("vpnv4_enabled"):
      return True
    return bool(bgp.get("evpn_enabled") and instance.get("evpn_mpls_routing_enable") and instance.get("evpn_rts")
                and instance.get("advertise_l2vpn_evpn"))

  exported = []
  for instance in instances.values():
    if not vpn_transport_enabled(instance):
      continue
    for route in instance["routes"]:
      exported.append((instance, route))
  routes = {key: [] for key in instances}
  for destination in instances.values():
    destination_key = (destination["device"], destination["name"])
    imports = set(destination.get("import_rts", []))
    for source, route in exported:
      source_key = (source["device"], source["name"])
      if source_key == destination_key:
        routes[destination_key].append(route)
        continue
      if imports.intersection(source.get("export_rts", [])):
        copy = dict(route)
        copy["rd"] = source.get("rd", "")
        copy["vpn_source"] = source_key
        tunnel_policy = destination.get("tunnel_policy")
        if tunnel_policy:
          policy = (devices[destination["device"]]["cfg"].get("tunnel_policies") or {}).get(tunnel_policy, {})
          color = destination.get("default_color")
          active = next((row.get("active") for row in sr_policies.get(destination["device"], [])
                         if row.get("active") and (color is None or int(row.get("color", -1)) == int(color))), None)
          copy["tunnel_policy"] = tunnel_policy
          if active:
            copy["tunnel_candidate_preference"] = active.get("preference")
            copy["tunnel_segment_list"] = active.get("segment_list")
        routes[destination_key].append(copy)
  for key, rows in routes.items():
    unique = {}
    for row in rows:
      unique[(str(row["network"]), row.get("source"), row.get("rd"))] = row
    routes[key] = sorted(unique.values(), key=lambda row: (int(row["network"].network_address), row["network"].prefixlen))
  ce_routes = {name: [] for name in devices}
  for (name, vpn_name), instance in instances.items():
    for peer_ip, peer_data in (instance.get("bgp_peers") or {}).items():
      if not peer_data.get("as_number"):
        continue
      owner = _address_owner(devices, peer_ip)
      if not owner or owner[0] == name:
        continue
      for route in routes.get((name, vpn_name), []):
        if route.get("source") == owner[0]:
          continue
        copy = dict(route)
        copy.update({"next_hop": peer_ip, "protocol": "BGP", "vpn_instance": vpn_name,
                     "ce_peer": owner[0]})
        ce_routes[owner[0]].append(copy)
  for name, rows in ce_routes.items():
    unique = {(str(row["network"]), row.get("source")): row for row in rows}
    ce_routes[name] = sorted(unique.values(), key=lambda row: (int(row["network"].network_address), row["network"].prefixlen))
  evpn_routes = {name: [] for name in devices}
  for key, rows in routes.items():
    instance = instances[key]
    if not (instance.get("evpn_mpls_routing_enable") and instance.get("evpn_rts") and instance.get("advertise_l2vpn_evpn")
            and (devices[instance["device"]]["cfg"].get("bgp") or {}).get("evpn_enabled")):
      continue
    evpn_routes[key[0]].extend(dict(row, route_type=5) for row in rows)
  for name, rows in evpn_routes.items():
    unique = {(str(row["network"]), row.get("rd", "")): row for row in rows}
    evpn_routes[name] = sorted(unique.values(), key=lambda row: (int(row["network"].network_address), row["network"].prefixlen))
  return {"ldp_neighbors": ldp, "vpn_routes": routes, "evpn_routes": evpn_routes,
          "ce_routes": ce_routes, "instances": instances}
