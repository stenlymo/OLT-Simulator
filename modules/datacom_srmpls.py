"""Control-plane view for SR-MPLS BE, TE tunnels, and SR policies."""
import json


def sr_state(conn, username):
  from modules.datacom_isis import isis_state
  isis = isis_state(conn, username)
  devices = {}
  for name, kind, raw in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (username,)
  ).fetchall():
    try: cfg = json.loads(raw or "{}")
    except (TypeError, ValueError): cfg = {}
    devices[name] = {"kind": kind or "", "cfg": cfg}
  lsp = {name: [] for name in devices}
  forwarding = {name: [] for name in devices}
  label_up = {}
  for owner, device in devices.items():
    cfg = device["cfg"]
    for process_id, process in (cfg.get("isis_processes") or {}).items():
      if not process.get("segment_routing_mpls"): continue
      low, high = process.get("srgb") or [16000, 17000]
      for iface in (cfg.get("interfaces") or {}).values():
        index = iface.get("isis_prefix_sid_index")
        if (iface.get("is_loopback") and str(iface.get("isis_process")) == str(process_id)
            and index and low <= low + int(index) - 1 <= high):
          label_up[int(low) + int(index) - 1] = True
    for adj in (cfg.get("segment_routing") or {}).get("adjacency_sids", []):
      local = next(((ifname, iface) for ifname, iface in (cfg.get("interfaces") or {}).items()
                    if iface.get("ip") == adj.get("local") and not iface.get("shutdown")), None)
      remote_owner = next((peer for peer, peer_device in devices.items() if any(
          iface.get("ip") == adj.get("remote") and not iface.get("shutdown")
          for iface in (peer_device["cfg"].get("interfaces") or {}).values())), None)
      operational = bool(local and remote_owner and any(
          row.get("peer") == remote_owner and row.get("interface") == local[0]
          for row in isis.get("neighbors", {}).get(owner, [])))
      label_up[int(adj.get("sid", 0))] = label_up.get(int(adj.get("sid", 0)), False) or operational
  for source, device in devices.items():
    cfg = device["cfg"]
    sr = cfg.get("segment_routing") or {}
    if not sr.get("enabled") or not cfg.get("mpls_enabled") or not any(
        process.get("segment_routing_mpls") for process in (cfg.get("isis_processes") or {}).values()):
      continue
    for ifname, iface in (cfg.get("interfaces") or {}).items():
      if not (iface.get("is_loopback") and iface.get("ip") and iface.get("isis_prefix_sid_index")):
        continue
      process = (cfg.get("isis_processes") or {}).get(str(iface.get("isis_process")), {})
      if not process.get("segment_routing_mpls"): continue
      srgb = process.get("srgb") or [16000, 17000]
      label = int(srgb[0]) + int(iface["isis_prefix_sid_index"]) - 1
      forwarding[source].append({"prefix": f"{iface['ip']}/32", "label": label,
                                 "out_label": "POP", "next_hop": "127.0.0.1",
                                 "interface": ifname, "state": "Local"})
    for target, target_device in devices.items():
      if source == target: continue
      target_cfg = target_device["cfg"]
      target_sr = target_cfg.get("segment_routing") or {}
      if not target_sr.get("enabled") or not target_cfg.get("mpls_enabled"): continue
      loop = next(((name, iface) for name, iface in (target_cfg.get("interfaces") or {}).items()
                   if iface.get("is_loopback") and iface.get("ip") and iface.get("isis_prefix_sid_index")), None)
      if not loop: continue
      loop_name, loop_if = loop
      process_id = loop_if.get("isis_process")
      process = (target_cfg.get("isis_processes") or {}).get(str(process_id), {})
      if not process.get("segment_routing_mpls"): continue
      route = next((row for row in isis.get("routes", {}).get(source, [])
                    if str(row.get("network")) == f"{loop_if['ip']}/32"), None)
      if not route: continue
      srgb = process.get("srgb") or [16000, 17000]
      label = int(srgb[0]) + int(loop_if["isis_prefix_sid_index"]) - 1
      lsp[source].append({"destination": loop_if["ip"], "node": target, "label": label,
                          "next_hop": route.get("next_hop"), "interface": route.get("interface"), "state": "UP"})
      forwarding[source].append({"prefix": f"{loop_if['ip']}/32", "label": label,
                                 "out_label": label, "next_hop": route.get("next_hop"),
                                 "interface": route.get("interface"), "state": "Active"})

  te_tunnels = {name: [] for name in devices}
  policies = {name: [] for name in devices}
  for name, device in devices.items():
    cfg = device["cfg"]
    for ifname, iface in (cfg.get("interfaces") or {}).items():
      if not iface.get("is_tunnel") or not iface.get("sr_te"):
        continue
      dest = iface.get("destination", "")
      path_name = iface.get("explicit_path", "")
      path = (cfg.get("sr_mpls") or {}).get("explicit_paths", {}).get(path_name, [])
      reachable = any(row["destination"] == dest for row in lsp.get(name, []))
      segments_up = bool(path) and all(label_up.get(int(item.get("label", 0)), False) for item in path)
      te_tunnels[name].append({"name": ifname, "destination": dest, "path": path_name,
                               "segments": path, "state": "UP" if reachable and segments_up else "DOWN"})
    for policy_name, policy in (cfg.get("sr_mpls") or {}).get("policies", {}).items():
      candidates = []
      for candidate in policy.get("candidates", []):
        seglist = (cfg.get("sr_mpls") or {}).get("segment_lists", {}).get(candidate.get("segment_list", ""), [])
        labels = [int(item.get("label", 0)) for item in seglist]
        candidates.append({**candidate, "labels": labels,
                           "state": "UP" if labels and all(label_up.get(label, False) for label in labels) else "DOWN"})
      active = next((item for item in sorted(candidates, key=lambda row: int(row.get("preference", 0)), reverse=True)
                     if item["state"] == "UP"), None)
      policies[name].append({"name": policy_name, "endpoint": policy.get("endpoint", ""),
                             "color": policy.get("color", 0), "candidates": candidates,
                             "active": active})
  return {"devices": devices, "isis": isis, "lsps": lsp, "forwarding": forwarding,
          "te_tunnels": te_tunnels, "policies": policies}


