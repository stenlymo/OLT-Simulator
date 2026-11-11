"""Campus VXLAN deployment inventory and validation for HCIE Chapter 6.

The guide uses iMaster NCE to orchestrate the fabric. This module models the
resulting deployment plan in the simulator and checks it against device state;
it does not pretend to run or emulate the NCE controller itself.
"""
import ipaddress
import json


def campus_state(conn, username):
  devices = {}
  for name, kind, raw in conn.execute(
      "SELECT node_name,device_type,running_json FROM olt_datacom_devices WHERE username=? ORDER BY node_name",
      (username,),
  ).fetchall():
    try:
      config = json.loads(raw or "{}")
    except (TypeError, ValueError):
      config = {}
    campus = config.get("campus") or {}
    devices[name] = {"kind": kind or "", "role": campus.get("role", ""),
                     "fabric": campus.get("fabric", ""), "vns": campus.get("vns", {}),
                     "config": config}

  from modules.datacom_network import datacom_topology_links
  links, _ = datacom_topology_links(conn, username)
  active_links = [(a, b, ap, bp) for a, b, ap, bp, up in links if up]
  fabrics = {}
  for name, device in devices.items():
    if device["fabric"]:
      fabrics.setdefault(device["fabric"], {"devices": [], "vns": {}})["devices"].append(name)
    for vn_name, vn in device["vns"].items():
      fabric = device["fabric"] or "Unassigned"
      fabric_row = fabrics.setdefault(fabric, {"devices": [], "vns": {}})
      row = fabric_row["vns"].setdefault(vn_name, {"members": [], "vni": vn.get("vni"),
          "bd": vn.get("bd"), "vlan": vn.get("vlan"), "gateway": vn.get("gateway", ""), "issues": []})
      row["members"].append(name)
      if (row["vni"], row["bd"], row["vlan"]) != (vn.get("vni"), vn.get("bd"), vn.get("vlan")):
        row["issues"].append(f"{name}: VN mapping does not match other members")
      cfg = device["config"]
      bd = (cfg.get("bridge_domains") or {}).get(str(vn.get("bd")), {})
      nve_vnis = (cfg.get("nve") or {}).get("vnis", {})
      if int(bd.get("vxlan_vni") or 0) != int(vn.get("vni") or 0):
        row["issues"].append(f"{name}: bridge-domain {vn.get('bd')} is not mapped to VNI {vn.get('vni')}")
      if str(vn.get("vni")) not in nve_vnis:
        row["issues"].append(f"{name}: NVE has no VNI {vn.get('vni')}")
      if vn.get("gateway"):
        try:
          address = ipaddress.ip_interface(vn["gateway"])
          gateway_if = next((iface for iface in (cfg.get("interfaces") or {}).values()
                             if iface.get("is_vbdif") and str(iface.get("bridge_domain")) == str(vn.get("bd"))), None)
          if not gateway_if:
            row["issues"].append(f"{name}: no VBDIF exists for bridge-domain {vn.get('bd')}")
          else:
            try:
              configured = ipaddress.ip_interface(f"{gateway_if.get('ip')}/{gateway_if.get('mask') or '24'}")
              if configured != address:
                row["issues"].append(f"{name}: VBDIF address {configured} differs from planned {address}")
            except ValueError:
              row["issues"].append(f"{name}: planned VBDIF has no valid IPv4 address")
        except ValueError:
          row["issues"].append(f"{name}: invalid planned gateway {vn.get('gateway')}")
  for fabric in fabrics.values():
    fabric["devices"].sort()
    for vn in fabric["vns"].values():
      vn["members"] = sorted(set(vn["members"]))
      vn["issues"] = sorted(set(vn["issues"]))
      vn["ready"] = not vn["issues"]
  return {"devices": devices, "fabrics": fabrics, "links": active_links}

