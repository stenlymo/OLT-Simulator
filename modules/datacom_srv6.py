"""Deterministic SRv6 locator, SID, and policy view for HCIE Chapter 9 labs."""
import ipaddress
import json


def srv6_state(conn, username):
  devices = {}
  for name, kind, raw in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=?", (username,)
  ).fetchall():
    try: cfg = json.loads(raw or "{}")
    except (TypeError, ValueError): cfg = {}
    devices[name] = {"kind": kind or "", "cfg": cfg}
  from modules.datacom_ipv6_routing import ipv6_routing_state
  underlay = ipv6_routing_state(conn, username)
  locators, local_sids, forwarding, policies = {}, {}, {}, {}
  for owner, device in devices.items():
    cfg = device["cfg"]
    sr = cfg.get("srv6") or {}
    locators[owner], local_sids[owner], forwarding[owner], policies[owner] = [], [], [], []
    for name, locator in (sr.get("locators") or {}).items():
      try:
        prefix = ipaddress.IPv6Network(f"{locator['prefix']}/{int(locator.get('prefix_length', 64))}", strict=False)
      except (KeyError, ValueError, TypeError):
        continue
      locators[owner].append({"name": name, "prefix": str(prefix), "state": "Up" if sr.get("enabled") else "Down"})
      # The simulator assigns deterministic function IDs for common service SIDs.
      sid_types = (("End", 0x100), ("End.DT4", 0x200), ("End.DT6", 0x300))
      for sid_type, function in sid_types:
        sid = str(ipaddress.IPv6Address(int(prefix.network_address) | function))
        local_sids[owner].append({"sid": sid, "type": sid_type, "locator": name,
                                  "vpn_instance": next((vpn_name for vpn_name, vpn in (cfg.get("vpn_instances") or {}).items()
                                                        if vpn.get("srv6_locator") == name and
                                                        ((sid_type == "End.DT4" and vpn.get("srv6_service") in (None, "vpnv4")) or
                                                         (sid_type == "End.DT6" and vpn.get("srv6_service") == "vpnv6"))), None),
                                  "state": "Up" if sr.get("enabled") else "Down"})
    for policy_name, policy in (sr.get("policies") or {}).items():
      candidate_rows = []
      for candidate in policy.get("candidates", []):
        sid_list = candidate.get("segments", [])
        endpoint = policy.get("endpoint", "")
        route = next((row for row in underlay.get("routes", {}).get(owner, [])
                      if endpoint and str(row.get("destination", "")).split("/")[0] == endpoint), None)
        reachable = bool(route) or endpoint in (cfg.get("loopbacks") or {})
        def in_any_configured_locator(sid_value):
          try: sid_address = ipaddress.IPv6Address(sid_value)
          except (ValueError, TypeError): return False
          for peer_device in devices.values():
            peer_sr = peer_device["cfg"].get("srv6") or {}
            if not peer_sr.get("enabled"):
              continue
            for peer_locator in (peer_sr.get("locators") or {}).values():
              try:
                if sid_address in ipaddress.IPv6Network(
                    f"{peer_locator['prefix']}/{int(peer_locator.get('prefix_length', 64))}", strict=False):
                  return True
              except (KeyError, ValueError, TypeError): pass
          return False
        valid_sids = all(in_any_configured_locator(sid) for sid in sid_list)
        state = "Up" if sr.get("enabled") and sid_list and reachable and valid_sids else "Down"
        candidate_rows.append({**candidate, "state": state})
      active = next((row for row in sorted(candidate_rows, key=lambda item: int(item.get("preference", 0)), reverse=True)
                     if row["state"] == "Up"), None)
      policies[owner].append({"name": policy_name, "endpoint": policy.get("endpoint", ""),
                              "color": policy.get("color", 0), "candidates": candidate_rows, "active": active})
    if sr.get("enabled"):
      for locator in locators[owner]:
        forwarding[owner].append({"prefix": locator["prefix"], "sid": str(ipaddress.IPv6Address(
          int(ipaddress.IPv6Network(locator["prefix"]).network_address) | 0x100)), "type": "End", "state": locator["state"]})
  return {"devices": devices, "underlay": underlay, "locators": locators,
          "local_sids": local_sids, "forwarding": forwarding, "policies": policies}


