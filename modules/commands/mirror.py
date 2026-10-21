"""Transient Ethernet port mirroring and lightweight packet capture events."""

from __future__ import annotations

import re
from datetime import datetime

from modules.database import get_connection

# Port mirrors and captured frames are volatile by design, like the documented
# debugging feature: they disappear when the simulator process restarts.
_MIRRORS = {}
_PACKETS = {}


def handle_command(username, node_name, board, command):
  text = " ".join(command.strip().split())
  removing = text.lower().startswith("undo mirror port")
  body = text[5:] if removing else text
  match = re.fullmatch(r"mirror port (?:(?:src-port )?(\d+/\d+/\d+|\d+)(?: dst-port )? (\d+/\d+/\d+|\d+)(?: (ingress|egress|all))?)", body, re.I)
  if not match:
    if removing and body.lower() == "mirror port":
      for key in list(_MIRRORS):
        if key[:3] == (username, node_name, board):
          del _MIRRORS[key]
      return "\n  Command:\n          undo mirror port\n  It will take some time, please wait..."
    return None
  source, destination, direction = match.groups()
  direction = (direction or ("all" if removing else "")).lower()
  if not direction:
    return "  Failure: Mirror direction must be ingress, egress, or all."
  source = source if source.count("/") == 2 else f"{board}/{source}"
  destination = destination if destination.count("/") == 2 else f"{board}/{destination}"
  if source.rsplit("/", 1)[0] != board or destination.rsplit("/", 1)[0] != board:
    return "  Failure: Source and destination ports must be on the current ETH board."
  if source == destination:
    return "  Failure: Source and destination ports must be different."
  if board == "0/2" and any(int(port.rsplit("/", 1)[1]) not in range(4) for port in (source, destination)):
    return "  Failure: Uplink port ID on slot 2 must be in range 0-3."
  if removing:
    for key in list(_MIRRORS):
      if key[:5] == (username,node_name,board,source,destination) and (direction == "all" or key[5] == direction):
        del _MIRRORS[key]
    return f"\n  Command:\n          {text}\n  It will take some time, please wait..."
  conn = get_connection()
  try:
    lag = conn.execute("SELECT 1 FROM olt_link_aggregations WHERE username=? AND node_name=? AND master_port=? UNION SELECT 1 FROM olt_link_aggregation_members WHERE username=? AND node_name=? AND member_port=? LIMIT 1", (username,node_name,destination,username,node_name,destination)).fetchone()
    if lag:
      return "  Failure: Mirror destination port cannot be an aggregation port."
    existing = [key[5] for key in _MIRRORS if key[:2] == (username,node_name) and key[3] == source]
    if any(existing_direction == "all" or direction == "all" for existing_direction in existing):
      return "  Failure: Delete the existing mirror before configuring an overlapping direction."
    if direction in set(existing):
      return "  Failure: A mirror with this source and direction already exists."
  finally:
    conn.close()
  _MIRRORS[(username,node_name,board,source,destination,direction)] = True
  return f"\n  Command:\n          {text}"


def emit(username, node_name, source_port, direction, protocol, source, destination, summary):
  """Record a simulated packet only if it is mirrored to a linked Wireshark node."""
  mirrors = [(key[4], key[5]) for key in _MIRRORS if key[:2] == (username,node_name) and key[3] == source_port]
  conn = get_connection()
  try:
    for dest_port, configured_direction in mirrors:
      if configured_direction not in (direction, "all"):
        continue
      links = conn.execute("""SELECT source_node,source_port,target_node,target_port FROM olt_device_links
        WHERE username=? AND ((source_node=? AND source_port=?) OR (target_node=? AND target_port=?)) AND is_up=1""",
        (username,node_name,dest_port,node_name,dest_port)).fetchall()
      for a, ap, b, bp in links:
        peer = b if a == node_name else a
        if "wireshark" in peer.lower():
          _PACKETS.setdefault((username,peer), []).append((datetime.now().astimezone().isoformat(timespec="seconds"),protocol,source,destination,summary))
  finally:
    conn.close()


def emit_pppoe_exchange(username, olt_node, subscriber, bras_node, user, ip=""):
  """Generate a small representative PPPoE discovery/auth/IP packet exchange."""
  if not olt_node:
    return
  conn = get_connection()
  try:
    ports = conn.execute("SELECT source_port FROM olt_device_links WHERE username=? AND source_node=? AND source_port LIKE '0/2/%' AND is_up=1 UNION SELECT target_port FROM olt_device_links WHERE username=? AND target_node=? AND target_port LIKE '0/2/%' AND is_up=1", (username,olt_node,username,olt_node)).fetchall()
  finally:
    conn.close()
  phases = [
      ("egress", "PPPoE", subscriber, "ff:ff:ff:ff:ff:ff", "PADI — PPPoE Active Discovery Initiation"),
      ("ingress", "PPPoE", bras_node, subscriber, "PADO — PPPoE Active Discovery Offer"),
      ("egress", "PPPoE", subscriber, bras_node, "PADR — PPPoE Active Discovery Request"),
      ("ingress", "PPPoE", bras_node, subscriber, f"PADS — PPPoE Active Discovery Session-confirmation (user {user})"),
      ("ingress", "PPP", bras_node, subscriber, "LCP/IPCP negotiation completed"),
  ]
  if ip:
    phases.extend([
        ("ingress", "IPv4", bras_node, subscriber, f"Session traffic available; assigned IP {ip}"),
        ("egress", "DHCP", subscriber, "255.255.255.255", "DHCP Discover (simulated address allocation)"),
        ("ingress", "DHCP", "DHCP Server", subscriber, f"DHCP Offer — {ip}"),
        ("egress", "DHCP", subscriber, "DHCP Server", f"DHCP Request — {ip}"),
        ("ingress", "DHCP", "DHCP Server", subscriber, f"DHCP ACK — lease {ip}"),
    ])
  for (port,) in ports:
    for direction, protocol, src, dst, summary in phases:
      emit(username, olt_node, port, direction, protocol, src, dst, summary)


def emit_dhcp_exchange(username, olt_node, client, server, ip, mac=""):
  if not olt_node:
    return
  conn = get_connection()
  try:
    ports = conn.execute("SELECT source_port FROM olt_device_links WHERE username=? AND source_node=? AND source_port LIKE '0/2/%' AND is_up=1 UNION SELECT target_port FROM olt_device_links WHERE username=? AND target_node=? AND target_port LIKE '0/2/%' AND is_up=1", (username,olt_node,username,olt_node)).fetchall()
  finally:
    conn.close()
  phases = [
      ("egress", "DHCP", client, "255.255.255.255", f"DHCP Discover (chaddr {mac or 'unknown'})"),
      ("ingress", "DHCP", server, client, f"DHCP Offer — {ip}"),
      ("egress", "DHCP", client, server, f"DHCP Request — {ip}"),
      ("ingress", "DHCP", server, client, f"DHCP ACK — lease {ip}"),
  ]
  for (port,) in ports:
    for direction, protocol, src, dst, summary in phases:
      emit(username, olt_node, port, direction, protocol, src, dst, summary)


def packets(username, capture_node):
  return list(reversed(_PACKETS.get((username,capture_node), [])[-500:]))


def clear_packets(username, capture_node):
  _PACKETS.pop((username,capture_node), None)


def display(username, node_name, board):
  rows = sorted((key[3],key[4],key[5]) for key in _MIRRORS if key[:3] == (username,node_name,board))
  lines = ["", "  Command:", "          display mirror port", "  Source Port   Destination Port   Direction", "  ------------------------------------------"]
  lines.extend(f"  {src:<13} {dst:<18} {direction}" for src,dst,direction in rows)
  if not rows:
    lines.append("  (no port mirrors configured)")
  lines.append("  ------------------------------------------")
  return "\n".join(lines)
