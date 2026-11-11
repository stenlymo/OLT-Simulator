"""Static VXLAN, centralized gateway, and EVPN control-plane lab model."""
import ipaddress
import json


def _load(conn, username):
  devices = {}
  for name, kind, raw in conn.execute("SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (username,)).fetchall():
    try: cfg = json.loads(raw)
    except (TypeError, ValueError): continue
    cfg.setdefault("bridge_domains", {})
    cfg.setdefault("nve", {"source": "", "vnis": {}})
    cfg.setdefault("evpn_instances", {})
    devices[name] = {"kind": kind or "", "cfg": cfg}
  return devices


def vxlan_state(conn, username):
  devices = _load(conn, username)
  from modules.datacom_network import datacom_topology_links, routed_ping_reachable
  links, _ = datacom_topology_links(conn, username)
  source_owners = {}
  for name, device in devices.items():
    source = (device["cfg"].get("nve") or {}).get("source")
    if source: source_owners[source] = name
  dynamic_peers = {name: {} for name in devices}
  try:
    from modules.datacom_bgp import bgp_state
    bgp_sessions = bgp_state(conn, username).get("sessions", {})
    for name, sessions in bgp_sessions.items():
      for session in sessions:
        if not (session.get("peer_config", {}).get("evpn_enabled") and session.get("remote_config", {}).get("evpn_enabled")):
          continue
        remote = session.get("peer")
        remote_source = (devices.get(remote, {}).get("cfg", {}).get("nve") or {}).get("source")
        if not remote_source: continue
        for vni, vni_cfg in (devices.get(name, {}).get("cfg", {}).get("nve", {}).get("vnis") or {}).items():
          if vni_cfg.get("protocol") == "bgp" and str(vni) in (devices.get(remote, {}).get("cfg", {}).get("nve", {}).get("vnis") or {}):
            dynamic_peers[name].setdefault(str(vni), []).append(remote_source)
  except Exception:
    pass
  tunnels, peers = {name: [] for name in devices}, {name: [] for name in devices}
  for name, device in devices.items():
    nve = device["cfg"].get("nve") or {}
    source = nve.get("source")
    if not source: continue
    for vni, vni_cfg in (nve.get("vnis") or {}).items():
      peer_addresses = list(vni_cfg.get("peers", []))
      peer_addresses.extend(ip for ip in dynamic_peers.get(name, {}).get(str(vni), []) if ip not in peer_addresses)
      for peer_ip in peer_addresses:
        remote = source_owners.get(peer_ip)
        remote_nve = (devices.get(remote, {}).get("cfg", {}).get("nve") or {}) if remote else {}
        remote_vni = (remote_nve.get("vnis") or {}).get(str(vni), {})
        reciprocal = (source in remote_vni.get("peers", []) if vni_cfg.get("protocol", "static") == "static"
                      else remote_vni.get("protocol") == "bgp")
        reachable = False
        if remote and reciprocal:
          try: reachable = bool(routed_ping_reachable(conn, username, name, remote, peer_ip, source_address=source))
          except Exception: reachable = False
        state = "up" if reachable else "down"
        typ = "static" if vni_cfg.get("protocol", "static") == "static" else ("L2 dynamic" if any(
            int(bd.get("vxlan_vni") or 0) == int(vni) for bd in device["cfg"].get("bridge_domains", {}).values()) else "L3 dynamic")
        record = {"vni": int(vni), "source": source, "destination": peer_ip, "state": state, "type": typ, "peer_node": remote}
        tunnels[name].append(record)
        peers[name].append({"vni": int(vni), "source": source, "destination": peer_ip, "type": "L2 dynamic" if typ == "L2 dynamic" else typ})

  evpn_routes = {name: [] for name in devices}
  # EVPN inclusive multicast (type 3) advertisements are modeled for BDs whose
  # EVPN instance is attached and whose NVE tunnel is operational.
  for name, device in devices.items():
    for bd_id, bd in device["cfg"].get("bridge_domains", {}).items():
      evpn_name = bd.get("evpn_instance")
      bd_vbdif = next((iface for iface in device["cfg"].get("interfaces", {}).values()
                       if iface.get("is_vbdif") and str(iface.get("bridge_domain")) == str(bd_id) and iface.get("vpn_instance")), {})
      vpn_instance = device["cfg"].get("vpn_instances", {}).get(bd_vbdif.get("vpn_instance"), {})
      if not evpn_name and not vpn_instance: continue
      instance = device["cfg"].get("evpn_instances", {}).get(evpn_name, {})
      rd = instance.get("rd") or vpn_instance.get("rd") or "-"
      vni = int(bd.get("vxlan_vni") or 0)
      for tunnel in tunnels.get(name, []):
        if tunnel["vni"] != vni or tunnel["state"] != "up": continue
        evpn_routes[name].append({"route_type": "inclusive-route", "rd": rd, "vni": vni,
                                  "value": f"0:{tunnel['destination']}", "next_hop": tunnel["destination"]})
    for ifname, iface in device["cfg"].get("interfaces", {}).items():
      if not iface.get("is_vbdif") or not iface.get("ip"): continue
      bd = device["cfg"].get("bridge_domains", {}).get(str(iface.get("bridge_domain")), {})
      vpn = device["cfg"].get("vpn_instances", {}).get(iface.get("vpn_instance"), {})
      evpn = device["cfg"].get("evpn_instances", {}).get(bd.get("evpn_instance"), {})
      if bd.get("evpn_instance") or iface.get("vpn_instance"):
        evpn_routes[name].append({"route_type": "mac-route", "rd": evpn.get("rd") or "-", "vni": int(bd.get("vxlan_vni") or 0),
                                  "value": f"{iface.get('mac_address','0000-5e00-0110')}:{iface['ip']}", "next_hop": (device["cfg"].get("nve") or {}).get("source", "-"),
                                  "vpn_rd": vpn.get("rd", "-")})
    for host in device["cfg"].get("vxlan_learned_hosts", {}).values():
      bd = device["cfg"].get("bridge_domains", {}).get(str(host.get("bd")), {})
      vbdif = next((iface for iface in device["cfg"].get("interfaces", {}).values()
                    if iface.get("is_vbdif") and str(iface.get("bridge_domain")) == str(host.get("bd")) and iface.get("vpn_instance")), {})
      vpn = device["cfg"].get("vpn_instances", {}).get(vbdif.get("vpn_instance"), {})
      evpn = device["cfg"].get("evpn_instances", {}).get(bd.get("evpn_instance"), {})
      if not bd.get("evpn_instance") and not vpn: continue
      rd = evpn.get("rd") or vpn.get("rd") or "-"
      evpn_routes[name].append({"route_type": "mac-route", "rd": rd, "vni": int(host.get("vni") or 0),
                                "value": f"{host.get('mac')}:{host.get('ip')}", "next_hop": (device["cfg"].get("nve") or {}).get("source", "-")})
      for remote, remote_device in devices.items():
        if remote == name: continue
        for remote_bd_id, remote_bd in remote_device["cfg"].get("bridge_domains", {}).items():
          if int(remote_bd.get("vxlan_vni") or 0) != int(host.get("vni") or 0): continue
          remote_evpn = remote_device["cfg"].get("evpn_instances", {}).get(remote_bd.get("evpn_instance"), {})
          remote_vbdif = next((iface for iface in remote_device["cfg"].get("interfaces", {}).values()
                               if iface.get("is_vbdif") and str(iface.get("bridge_domain")) == str(remote_bd_id) and iface.get("vpn_instance")), {})
          remote_vpn = remote_device["cfg"].get("vpn_instances", {}).get(remote_vbdif.get("vpn_instance"), {})
          local_export = set(evpn.get("export_rts", [])) | set(vpn.get("export_rts", []))
          remote_import = set(remote_evpn.get("import_rts", [])) | set(remote_vpn.get("import_rts", []))
          if not (local_export & remote_import): continue
          if not any(t["peer_node"] == remote and t["vni"] == int(host.get("vni") or 0) and t["state"] == "up" for t in tunnels.get(name, [])): continue
          evpn_routes[remote].append({"route_type": "mac-route", "rd": rd, "vni": int(host.get("vni") or 0),
                                      "value": f"{host.get('mac')}:{host.get('ip')}", "next_hop": (device["cfg"].get("nve") or {}).get("source", "-")})
  return {"devices": devices, "tunnels": tunnels, "peers": peers, "evpn_routes": evpn_routes, "links": links}


def _pc_site(state, pc_name):
  from modules.datacom_network import _port_allows
  links, devices = state["links"], state["devices"]
  access_switch = None; vlan = None
  for a, b, ap, bp, up in links:
    if not up: continue
    if a == pc_name and b in devices and "switch" in devices[b]["kind"].lower():
      access_switch, swport = b, bp
    elif b == pc_name and a in devices and "switch" in devices[a]["kind"].lower():
      access_switch, swport = a, ap
    else: continue
    switch_cfg = devices[access_switch]["cfg"]
    port = (switch_cfg.get("interfaces") or {}).get(swport, {})
    vlan = int(port.get("pvid", 1))
    break
  if access_switch is None: return []
  sites = []
  for edge, device in devices.items():
    for ifname, iface in device["cfg"].get("interfaces", {}).items():
      if not iface.get("subinterface") or iface.get("mode") != "l2" or iface.get("vlan_type") != "dot1q" or int(iface.get("vlan_id") or 0) != vlan:
        continue
      parent = iface.get("parent")
      for a, b, ap, bp, up in links:
        if not up: continue
        if a == edge and ap == parent and b == access_switch:
          switchport = bp
        elif b == edge and bp == parent and a == access_switch:
          switchport = ap
        else: continue
        sw_iface = (devices[access_switch]["cfg"].get("interfaces") or {}).get(switchport, {})
        if not _port_allows(devices[access_switch]["cfg"], switchport, vlan): continue
        bd_id = iface.get("bridge_domain")
        bd = device["cfg"].get("bridge_domains", {}).get(str(bd_id), {})
        if bd.get("vxlan_vni"):
          sites.append({"edge": edge, "bd": str(bd_id), "vni": int(bd["vxlan_vni"]), "vlan": vlan})
  return sites


def vxlan_pc_ping_reachable(conn, username, source_pc, destination_pc, source_ip, destination_ip, gateway="", source_mask="", destination_mask=""):
  """Check host reachability through configured L2/L3 VXLAN lab domains."""
  state = vxlan_state(conn, username)
  source_sites, destination_sites = _pc_site(state, source_pc), _pc_site(state, destination_pc)
  if not source_sites or not destination_sites: return False
  tunnels = state["tunnels"]
  def connected(a, b, vni):
    return a == b or any(row["peer_node"] == b and row["vni"] == vni and row["state"] == "up" for row in tunnels.get(a, []))
  def learn(site, host_name, address):
    if not site: return
    try:
      from modules.datacom_dhcp import interface_mac
      edge = site["edge"]
      cfg = state["devices"][edge]["cfg"]
      bd = cfg.get("bridge_domains", {}).get(str(site["bd"]), {})
      bd_iface = next((iface for iface in cfg.get("interfaces", {}).values()
                       if iface.get("is_vbdif") and str(iface.get("bridge_domain")) == site["bd"]), {})
      if not (bd.get("evpn_instance") or (bd_iface.get("vpn_instance") and bd_iface.get("arp_collect_host"))): return
      cfg.setdefault("vxlan_learned_hosts", {})[str(address)] = {
          "ip": str(address), "mac": interface_mac(host_name, "GE0/0/0"), "bd": site["bd"], "vni": site["vni"]}
      conn.execute("UPDATE olt_datacom_devices SET running_json=? WHERE username=? AND node_name=?",
                   (json.dumps(cfg), username, edge))
    except Exception:
      return
  def learned():
    conn.commit()
    return True
  try:
    source_addr, destination_addr = ipaddress.IPv4Address(source_ip), ipaddress.IPv4Address(destination_ip)
    source_net = ipaddress.IPv4Network(f"{source_addr}/{source_mask or '255.255.255.0'}", strict=False)
    destination_net = ipaddress.IPv4Network(f"{destination_addr}/{destination_mask or '255.255.255.0'}", strict=False)
  except (ValueError, ipaddress.AddressValueError): return False
  # L2 extension: both PCs share a subnet and their bridge domain uses the same VNI.
  if source_net == destination_net and destination_addr in source_net and any(connected(left["edge"], right["edge"], left["vni"])
      for left in source_sites for right in destination_sites if left["vni"] == right["vni"]):
    learn(source_sites[0], source_pc, source_addr); learn(destination_sites[0], destination_pc, destination_addr)
    return learned()
  if not gateway: return False
  # Centralized gateway: the gateway has VBDIFs in both bridge domains and an
  # operational VXLAN path from each edge to the corresponding BD.
  for router, device in state["devices"].items():
    if not any(iface.get("ip") == gateway and iface.get("is_vbdif") for iface in device["cfg"].get("interfaces", {}).values()): continue
    gateway_bds = []
    for iface in device["cfg"].get("interfaces", {}).values():
      if iface.get("is_vbdif") and iface.get("ip"):
        try: network = ipaddress.IPv4Network(f"{iface['ip']}/{iface.get('mask') or '24'}", strict=False)
        except (ValueError, ipaddress.AddressValueError): continue
        if destination_addr in network or source_addr in network:
          gateway_bds.append((str(iface.get("bridge_domain")), network))
    for left in source_sites:
      for right in destination_sites:
        if left["vni"] == right["vni"]: continue
        if connected(left["edge"], router, left["vni"]) and connected(right["edge"], router, right["vni"]):
          if any(bd == left["bd"] and source_addr in net for bd, net in gateway_bds) and any(
              bd == right["bd"] and destination_addr in net for bd, net in gateway_bds): return True
  # Distributed gateway: each edge owns its local VBDIFs, while matching RTs
  # and an operational L3 VNI carry routes between the edge VPN instances.
  for left in source_sites:
    for right in destination_sites:
      if left["vni"] == right["vni"]: continue
      left_cfg = state["devices"].get(left["edge"], {}).get("cfg", {})
      right_cfg = state["devices"].get(right["edge"], {}).get("cfg", {})
      left_vbdif = next((iface for iface in left_cfg.get("interfaces", {}).values()
                         if iface.get("is_vbdif") and iface.get("vpn_instance") and iface.get("ip") == gateway), None)
      right_vbdif = next((iface for iface in right_cfg.get("interfaces", {}).values()
                          if iface.get("is_vbdif") and iface.get("vpn_instance") and iface.get("bridge_domain") == int(right["bd"])), None)
      if not left_vbdif or not right_vbdif: continue
      try:
        right_net = ipaddress.IPv4Network(f"{right_vbdif['ip']}/{right_vbdif.get('mask') or '24'}", strict=False)
      except (ValueError, ipaddress.AddressValueError): continue
      if destination_addr not in right_net: continue
      left_vpn = left_cfg.get("vpn_instances", {}).get(left_vbdif.get("vpn_instance"), {})
      right_vpn = right_cfg.get("vpn_instances", {}).get(right_vbdif.get("vpn_instance"), {})
      if not left_vpn.get("vxlan_vni") or left_vpn.get("vxlan_vni") != right_vpn.get("vxlan_vni"): continue
      shared_rt = set(left_vpn.get("export_rts", [])) & set(right_vpn.get("import_rts", []))
      reverse_rt = set(right_vpn.get("export_rts", [])) & set(left_vpn.get("import_rts", []))
      if shared_rt and reverse_rt and connected(left["edge"], right["edge"], int(left_vpn["vxlan_vni"])):
        learn(left, source_pc, source_addr); learn(right, destination_pc, destination_addr)
        return learned()
  return False
