"""Small in-process NAT translator and session table for Datacom labs."""

import ipaddress
import time

_SESSIONS = {}


def _source_matches(rule, source_ip):
  try:
    address = ipaddress.IPv4Address(source_ip)
    source = rule.get("source", {"type": "network", "network": "0.0.0.0/0"})
    if isinstance(source, str):
      source = {"type": "network", "network": source}
    if source.get("type") == "range":
      return ipaddress.IPv4Address(source["start"]) <= address <= ipaddress.IPv4Address(source["end"])
    return address in ipaddress.IPv4Network(source.get("network", "0.0.0.0/0"), strict=False)
  except (KeyError, ValueError, TypeError):
    return False


def source_translation(config, egress_interface, source_ip):
  """Return (translated address, rule name, interface) for a matching source NAT rule."""
  interface = (config.get("interfaces") or {}).get(egress_interface, {})
  if not interface.get("nat_enabled"):
    return None
  for name, rule in (config.get("nat_policy_rules") or {}).items():
    if not rule.get("action") or not _source_matches(rule, source_ip):
      continue
    if rule["action"] == "easy-ip":
      translated = interface.get("ip")
    else:
      group = (config.get("nat_address_groups") or {}).get(rule.get("address_group"), {})
      sections = sorted(group.get("sections", []), key=lambda item: int(item.get("id", 0)))
      ranges = []
      for section in sections:
        try:
          first, last = int(ipaddress.IPv4Address(section["start"])), int(ipaddress.IPv4Address(section["end"]))
        except (KeyError, ValueError):
          continue
        if last >= first:
          ranges.append((first, last))
      size = sum(last - first + 1 for first, last in ranges)
      translated = None
      if size:
        offset = int(ipaddress.IPv4Address(source_ip)) % size
        for first, last in ranges:
          count = last - first + 1
          if offset < count:
            translated = str(ipaddress.IPv4Address(first + offset))
            break
          offset -= count
    if translated:
      return str(translated), name, egress_interface
  return None


def public_nat_interface(config, address):
  """Return the NAT egress interface that owns or advertises a public address."""
  try:
    target = ipaddress.IPv4Address(address)
  except ipaddress.AddressValueError:
    return None
  for ifname, iface in (config.get("interfaces") or {}).items():
    if not iface.get("nat_enabled"):
      continue
    if iface.get("ip") == str(target):
      return ifname
    for rule in (config.get("nat_policy_rules") or {}).values():
      if rule.get("action") != "address-group":
        continue
      group = (config.get("nat_address_groups") or {}).get(rule.get("address_group"), {})
      for section in group.get("sections", []):
        try:
          if ipaddress.IPv4Address(section["start"]) <= target <= ipaddress.IPv4Address(section["end"]):
            return ifname
        except (KeyError, ValueError):
          continue
  return None


def record_session(username, router, protocol, source, destination, new_source="-", new_destination="-",
                   source_port=None, destination_port=None, new_source_port=None, new_destination_port=None,
                   ttl=20, icmp_id=None, nat_rule=""):
  key = (username, router, str(protocol), str(source), str(destination), source_port, destination_port)
  _SESSIONS[key] = {"username": username, "router": router, "protocol": str(protocol).upper(),
                    "source": str(source), "destination": str(destination), "new_source": str(new_source),
                    "new_destination": str(new_destination), "source_port": source_port,
                    "destination_port": destination_port, "new_source_port": new_source_port,
                    "new_destination_port": new_destination_port, "icmp_id": icmp_id,
                    "nat_rule": nat_rule, "expires": time.time() + ttl, "ttl": ttl}


def sessions(username, router):
  now = time.time()
  for key, row in list(_SESSIONS.items()):
    if row["expires"] <= now:
      _SESSIONS.pop(key, None)
  return [dict(row, ttl=max(0, int(row["expires"] - now))) for row in _SESSIONS.values()
          if row["username"] == username and row["router"] == router]
