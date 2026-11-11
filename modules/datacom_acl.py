"""ACL and MQC traffic-policy evaluation for routed simulator packets."""
import ipaddress


def _address_matches(value, wildcard, packet_address):
  if not value:
    return True
  try:
    address = int(ipaddress.IPv4Address(value))
    wildcard_bits = int(ipaddress.IPv4Address(wildcard or "0.0.0.0"))
    packet = int(ipaddress.IPv4Address(packet_address))
  except ipaddress.AddressValueError:
    return False
  return ((address ^ packet) & (0xFFFFFFFF ^ wildcard_bits)) == 0


def acl_action(config, acl_number, source, destination, protocol="icmp"):
  """Return the first matching ACL action, or None when no rule matches."""
  acl = (config.get("acls") or {}).get(str(acl_number))
  if not acl:
    return None
  for rule in sorted(acl.get("rules", []), key=lambda item: int(item.get("sequence", 0))):
    rule_protocol = (rule.get("protocol") or "ip").lower()
    if rule_protocol not in ("ip", protocol.lower()):
      continue
    if not _address_matches(rule.get("source"), rule.get("source_wildcard"), source):
      continue
    if not _address_matches(rule.get("destination"), rule.get("destination_wildcard"), destination):
      continue
    return rule.get("action", "deny").lower()
  return None


def policy_allows(config, interface, direction, source, destination, protocol="icmp"):
  """Evaluate an interface's bound traffic policy for one packet."""
  iface = (config.get("interfaces") or {}).get(interface, {})
  policy_name = (iface.get("traffic_policies") or {}).get(direction)
  if not policy_name:
    return True
  policy = (config.get("traffic_policies") or {}).get(policy_name, {})
  entries = sorted(policy.get("classifiers", []), key=lambda item: int(item.get("precedence", 5)))
  for entry in entries:
    classifier = (config.get("traffic_classifiers") or {}).get(entry.get("classifier"), {})
    number = classifier.get("acl")
    if not number:
      continue
    action = acl_action(config, number, source, destination, protocol)
    if action is None:
      continue
    behavior = (config.get("traffic_behaviors") or {}).get(entry.get("behavior"), {}).get("action", "permit")
    if action == "deny":
      return False
    return behavior != "deny"
  return True


def packet_path_allows(devices, path, source, destination, protocol="icmp"):
  """Apply outbound and inbound policies to a sequence of (device, port, direction)."""
  for node, interface, direction in path:
    if node not in devices:
      continue
    if not policy_allows(devices[node][1], interface, direction, source, destination, protocol):
      return False
  return True
