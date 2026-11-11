"""BGPv4 session and route propagation model for HCIE routing labs.

The model intentionally implements the control-plane behaviors used in the
HCIE-Datacom V1.0 IPv4 routing tasks, rather than packet-level TCP/BGP.
"""
import ipaddress
import json
import re


def _load(conn, username):
  devices = {}
  for name, kind, raw in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (username,)
  ).fetchall():
    try:
      cfg = json.loads(raw)
    except (TypeError, ValueError):
      continue
    bgp = cfg.get("bgp")
    if bgp:
      devices[name] = {"kind": kind or "", "cfg": cfg, "bgp": bgp}
  return devices


def _ip_owner(devices, address):
  for name, device in devices.items():
    for ifname, iface in (device["cfg"].get("interfaces") or {}).items():
      if iface.get("ip") == address:
        return name, ifname, iface
  return None


def _prefix_match(config, name, address):
  entries = sorted(config.get("ip_prefix_lists", {}).get(name, []), key=lambda item: int(item.get("index", 0)))
  try:
    target = ipaddress.IPv4Address(address)
  except ipaddress.AddressValueError:
    return False
  for entry in entries:
    network = ipaddress.IPv4Network(entry["network"], strict=False)
    if target in network and int(entry.get("ge", network.prefixlen)) <= network.prefixlen <= int(entry.get("le", 32)):
      return entry.get("action", "permit") == "permit"
  return False


def _route_policy(device, policy_name, route):
  if not policy_name:
    return True, route
  config, bgp = device["cfg"], device["bgp"]
  policy = config.get("route_policies", {}).get(policy_name, {})
  prefix = route["network"]
  for _, node in sorted(policy.items(), key=lambda pair: int(pair[0])):
    matched = True
    list_name = node.get("ip_prefix")
    if list_name:
      entries = sorted(config.get("ip_prefix_lists", {}).get(list_name, []), key=lambda item: int(item.get("index", 0)))
      matched = False
      for entry in entries:
        candidate = ipaddress.IPv4Network(entry["network"], strict=False)
        if prefix.subnet_of(candidate) and int(entry.get("ge", candidate.prefixlen)) <= prefix.prefixlen <= int(entry.get("le", 32)):
          matched = entry.get("action", "permit") == "permit"
          break
    community_filter = node.get("community_filter")
    if matched and community_filter:
      filters = config.get("community_filters", {}).get(community_filter, [])
      matched = any(item.get("value") in route.get("communities", []) and item.get("action") == "permit" for item in filters)
    as_filter = node.get("as_path_filter")
    if matched and as_filter:
      filters = config.get("as_path_filters", {}).get(as_filter, [])
      path_text = " ".join(map(str, route.get("as_path", [])))
      matched = any(item.get("action") == "permit" and re.search(item.get("expression", ""), path_text) for item in filters)
    if matched:
      result = dict(route)
      if node.get("community"):
        result.setdefault("communities", [])
        if node["community"] not in result["communities"]:
          result["communities"].append(node["community"])
      return node.get("action", "permit") == "permit", result
  return False, route


def _peer_group(bgp, peer, info):
  group_name = info.get("group")
  group = (bgp.get("groups") or {}).get(group_name, {}) if group_name else {}
  merged = dict(group)
  merged.update(info)
  return merged


def _can_reach_peer(conn, username, devices, local_name, remote_ip, local_iface):
  import ipaddress
  owner = _ip_owner(devices, remote_ip)
  if not owner:
    return False
  try:
    local_net = ipaddress.IPv4Network(f"{local_iface['ip']}/{local_iface.get('mask') or '32'}", strict=False)
    if ipaddress.IPv4Address(remote_ip) in local_net:
      return True
  except (KeyError, ValueError):
    pass
  local_cfg = devices[local_name]["cfg"]
  remote_name = owner[0]
  # Lab loopback peering is enabled by explicit static routes or a reachable
  # OSPF route. This avoids treating a peer statement alone as connectivity.
  for cfg_name, target in ((local_name, remote_ip), (remote_name, local_iface.get("ip"))):
    cfg = devices[cfg_name]["cfg"]
    try:
      address = ipaddress.IPv4Address(target)
    except (ValueError, TypeError):
      return False
    reachable = False
    try:
      from modules.datacom_network import datacom_routing_table
      route_rows = datacom_routing_table(conn, username, cfg_name, include_dynamic=False)
      reachable = any(row.get("active") and address in row["network"] for row in route_rows)
    except Exception:
      pass
    if not reachable:
      try:
        from modules.datacom_ospf import ospf_state
        route_rows = ospf_state(conn, username).get("routes", {}).get(cfg_name, [])
        reachable = any(row.get("active") and address in row["network"] for row in route_rows)
      except Exception:
        pass
    if not reachable:
      return False
  return True


def bgp_state(conn, username):
  devices = _load(conn, username)
  sessions = {name: [] for name in devices}
  links = []
  for name, device in devices.items():
    bgp = device["bgp"]
    for peer, raw_info in (bgp.get("peers") or {}).items():
      info = _peer_group(bgp, peer, raw_info)
      owner = _ip_owner(devices, peer)
      if not owner or owner[0] == name:
        continue
      remote_name, _, _ = owner
      remote = devices[remote_name]
      remote_bgp = remote["bgp"]
      local_source = (device["cfg"].get("interfaces") or {}).get(info.get("connect_interface", ""), {})
      if not local_source.get("ip"):
        continue
      remote_info_raw = (remote_bgp.get("peers") or {}).get(local_source["ip"])
      if not remote_info_raw:
        continue
      remote_info = _peer_group(remote_bgp, local_source["ip"], remote_info_raw)
      remote_source = (remote["cfg"].get("interfaces") or {}).get(remote_info.get("connect_interface", ""), {})
      if not local_source.get("ip") or not remote_source.get("ip"):
        continue
      if int(info.get("as_number") or 0) != int(remote_bgp.get("as_number") or 0):
        continue
      if int(remote_info.get("as_number") or 0) != int(bgp.get("as_number") or 0):
        continue
      if info.get("password") != remote_info.get("password"):
        continue
      if not _can_reach_peer(conn, username, devices, name, peer, local_source):
        continue
      if not _can_reach_peer(conn, username, devices, remote_name, local_source["ip"], remote_source):
        continue
      # A one-sided or mismatched GTSM setting rejects the modeled session.
      if info.get("valid_ttl_hops") != remote_info.get("valid_ttl_hops"):
        continue
      session = {"peer": remote_name, "peer_ip": peer, "local_ip": local_source["ip"],
                 "remote_as": int(remote_bgp["as_number"]), "local_as": int(bgp["as_number"]),
                 "peer_config": info, "remote_config": remote_info}
      sessions[name].append(session)
      if name < remote_name:
        links.append((name, remote_name, session))

  routes = {name: [] for name in devices}
  # Originate only explicitly configured prefixes that are present in the local RIB.
  from modules.datacom_network import datacom_routing_table
  local_ribs = {name: datacom_routing_table(conn, username, name, include_dynamic=False) for name in devices}
  for name, device in devices.items():
    for entry in device["bgp"].get("networks", []):
      prefix_value = str(entry["network"])
      network = ipaddress.IPv4Network(prefix_value if "/" in prefix_value else f"{prefix_value}/{entry['mask']}", strict=False)
      if any(row.get("active") and row["network"] == network for row in local_ribs[name]):
        routes[name].append({"network": network, "next_hop": "0.0.0.0", "as_path": [],
                             "communities": [], "origin": "i", "med": 0, "local_pref": 100,
                             "preference": 0, "learned_from": name, "best": True})

  # Exchange routes until the small lab topology reaches a stable state.
  for _ in range(max(1, len(devices) * 4)):
    changed = False
    for local, remote, session in links:
      for sender, receiver, outgoing in ((local, remote, session["peer_config"]),
                                         (remote, local, session["remote_config"])):
        sender_bgp, receiver_bgp = devices[sender]["bgp"], devices[receiver]["bgp"]
        receiver_session = next((row for row in sessions[receiver] if row["peer"] == sender), None)
        if not receiver_session:
          continue
        receiver_peer_cfg = receiver_session["peer_config"]
        for route in list(routes[sender]):
          if route["learned_from"] == receiver:
            continue
          sender_as, receiver_as = int(sender_bgp["as_number"]), int(receiver_bgp["as_number"])
          same_as = sender_as == receiver_as
          if same_as and route["learned_from"] != sender:
            # iBGP split-horizon; permit reflection when the sending peer is
            # configured as a route reflector and the destination is a client.
            send_group = sender_bgp.get("peers", {}).get(receiver_session["peer_ip"], {}).get("group")
            receiver_group = sender_bgp.get("groups", {}).get(send_group, {}) if send_group else {}
            learned_client = any(group.get("reflect_client") for group in sender_bgp.get("groups", {}).values())
            destination_client = bool(receiver_group.get("reflect_client"))
            if not learned_client and not destination_client:
              continue
          if not same_as and sender_as in route.get("as_path", []):
            continue
          prefix_filter = receiver_peer_cfg.get("import_prefix")
          if prefix_filter and not _prefix_match(devices[receiver]["cfg"], prefix_filter, str(route["network"].network_address)):
            continue
          export_policy = outgoing.get("route_policy", {}).get("export")
          permitted, transformed = _route_policy(devices[sender], export_policy, route)
          if not permitted:
            continue
          import_policy = receiver_peer_cfg.get("route_policy", {}).get("import")
          permitted, transformed = _route_policy(devices[receiver], import_policy, transformed)
          if not permitted:
            continue
          updated = dict(transformed)
          updated["as_path"] = list(route.get("as_path", [])) if same_as else [sender_as, *route.get("as_path", [])]
          updated["learned_from"] = sender
          sender_session = next((row for row in sessions[sender] if row["peer"] == receiver), None)
          if outgoing.get("next_hop_local") or route.get("learned_from") == sender:
            updated["next_hop"] = sender_session["local_ip"] if sender_session else "0.0.0.0"
          else:
            updated["next_hop"] = route.get("next_hop", "0.0.0.0")
          if not same_as:
            updated["communities"] = list(route.get("communities", [])) if outgoing.get("advertise_community") else []
          updated["preference"] = 255
          if not any(existing["network"] == updated["network"] and existing.get("learned_from") == sender and
                     existing.get("as_path") == updated.get("as_path") for existing in routes[receiver]):
            routes[receiver].append(updated)
            changed = True
    if not changed:
      break
  for name, entries in routes.items():
    best_by_prefix = {}
    for route in entries:
      key = str(route["network"])
      rank = (len(route.get("as_path", [])), route.get("med", 0), route.get("learned_from", ""))
      if key not in best_by_prefix or rank < best_by_prefix[key][0]:
        best_by_prefix[key] = (rank, route)
    for route in entries:
      route["best"] = best_by_prefix.get(str(route["network"]), (None,))[1] is route
  return {"devices": devices, "sessions": sessions, "routes": routes}
