"""In-process remote login sessions for Datacom CLI simulations."""

import secrets

_SESSIONS = {}


def register(username, server_node, client_node, client_ip, protocol, vty, login_name):
  session_id = secrets.token_hex(12)
  _SESSIONS[session_id] = {
      "username": username, "server_node": server_node, "client_node": client_node,
      "client_ip": client_ip, "protocol": protocol.upper(), "vty": int(vty), "login_name": login_name,
  }
  return session_id


def unregister(session_id):
  _SESSIONS.pop(session_id, None)


def sessions(username, server_node):
  return [dict(item) for item in _SESSIONS.values()
          if item["username"] == username and item["server_node"] == server_node]
