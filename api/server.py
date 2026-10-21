"""REST API simulator OLT (stdlib only, tanpa dependensi tambahan).

Menjalankan server HTTP yang mengekspos topologi, inventaris, dan eksekusi
perintah CLI OLT sehingga bisa dipakai oleh web application apa pun.

Contoh:
    python run_api.py --port 8080
    curl http://127.0.0.1:8080/api/v1/topology
    curl -X POST http://127.0.0.1:8080/api/v1/nodes/Huawei_1/cli \
         -H "Content-Type: application/json" \
         -d '{"command":"display version"}'
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from api import engine, store
from modules import services
from modules.commands import ont_status

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 1_000_000
API_TOKEN = os.environ.get("OLT_API_TOKEN", "").strip()


class ApiError(Exception):
  def __init__(self, status, message):
    super().__init__(message)
    self.status = status
    self.message = message


def _node_or_404(node_name, username):
  names = {node["name"] for node in store.nodes(username)}
  if node_name not in names:
    raise ApiError(404, f"Node '{node_name}' tidak ada pada topologi.")
  return node_name


class Handler(BaseHTTPRequestHandler):
  server_version = "OLTSimulatorAPI/1.0"
  protocol_version = "HTTP/1.1"

  # ------------------------------------------------------------------ #
  def log_message(self, fmt, *args):  # noqa: D401
    sys.stderr.write("[api] %s - %s\n" % (self.address_string(), fmt % args))

  def _cors(self):
    self.send_header("Access-Control-Allow-Origin", "*")
    self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-Id, Authorization")
    self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")

  def _json(self, status, payload, extra_headers=None):
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.send_header("Content-Length", str(len(body)))
    self.send_header("Cache-Control", "no-store")
    self._cors()
    for key, value in (extra_headers or {}).items():
      self.send_header(key, value)
    self.end_headers()
    self.wfile.write(body)

  def _html(self, status, text):
    body = text.encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Content-Length", str(len(body)))
    self._cors()
    self.end_headers()
    self.wfile.write(body)

  def _read_json(self):
    length = int(self.headers.get("Content-Length") or 0)
    if length > MAX_BODY:
      raise ApiError(413, "Body terlalu besar.")
    raw = self.rfile.read(length) if length else b""
    if not raw:
      return {}
    try:
      return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
      raise ApiError(400, f"JSON tidak valid: {exc}") from exc

  def _authorized(self):
    if not API_TOKEN:
      return True
    header = self.headers.get("Authorization", "")
    return header == f"Bearer {API_TOKEN}"

  def _session_id(self):
    return self.headers.get("X-Session-Id") or uuid.uuid4().hex

  # ------------------------------------------------------------------ #
  def do_OPTIONS(self):
    self.send_response(204)
    self._cors()
    self.send_header("Content-Length", "0")
    self.end_headers()

  def do_GET(self):
    self._route("GET")

  def do_POST(self):
    self._route("POST")

  def do_DELETE(self):
    self._route("DELETE")

  # ------------------------------------------------------------------ #
  def _route(self, method):
    parsed = urlparse(self.path)
    path = parsed.path.rstrip("/") or "/"

    if method == "GET" and path in ("/", "/index.html"):
      page = STATIC_DIR / "index.html"
      if page.exists():
        return self._html(200, page.read_text(encoding="utf-8"))
      return self._html(200, "<h1>OLT Simulator API</h1><p>Lihat /api/v1/docs</p>")

    if method == "GET" and path.startswith("/static/"):
      name = path[len("/static/"):]
      if "/" in name or ".." in name:
        return self._json(400, {"ok": False, "error": "Path tidak valid."})
      target = STATIC_DIR / name
      if not target.exists():
        return self._json(404, {"ok": False, "error": "File tidak ditemukan."})
      content = target.read_bytes()
      self.send_response(200)
      ctype = "application/javascript" if name.endswith(".js") else "text/css" if name.endswith(".css") else "text/plain"
      self.send_header("Content-Type", ctype + "; charset=utf-8")
      self.send_header("Content-Length", str(len(content)))
      self._cors()
      self.end_headers()
      self.wfile.write(content)
      return

    if not self._authorized():
      return self._json(401, {"ok": False, "error": "Token API tidak valid."})

    try:
      if method == "GET":
        return self._get(path)
      if method == "POST":
        return self._post(path)
      if method == "DELETE":
        return self._delete(path)
      raise ApiError(405, "Method tidak didukung.")
    except ApiError as exc:
      return self._json(exc.status, {"ok": False, "error": exc.message})
    except BrokenPipeError:
      return
    except Exception as exc:  # noqa: BLE001
      self.log_message("internal error: %s", exc)
      return self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

  # ------------------------------------------------------------------ #
  def _get(self, path):
    username = store_username_from_query(self.path)

    if path == "/api/v1/health":
      return self._json(200, {"ok": True, "data": {"status": "up", "version": "1.0"}})
    if path == "/api/v1/docs":
      return self._html(200, DOCS_HTML)

    if path == "/api/v1/topology":
      return self._json(200, {"ok": True, "data": store.topology(username)})
    if path == "/api/v1/nodes":
      return self._json(200, {"ok": True, "data": store.nodes(username)})

    match = re.fullmatch(
        r"/api/v1/nodes/([^/]+)(?:/(boards|vlans|vlanifs|routes|onts|service-ports|profiles|ports|autofind))?",
        path,
    )
    if match:
      node = _node_or_404(match.group(1), username)
      section = match.group(2)
      if section in (None, "onts"):
        ont_status.refresh_ont_states(username, node)
      if section is None:
        return self._json(200, {"ok": True, "data": store.inventory(node, username)})
      if section == "boards":
        data = store.boards(username)
      elif section == "vlans":
        data = store.vlans(username)
      elif section == "vlanifs":
        data = store.vlanifs(node, username)
      elif section == "routes":
        data = store.routes(node, username)
      elif section == "onts":
        data = store.ont_rows(node, username)
      elif section == "service-ports":
        data = store.service_ports(node, username)
      elif section == "profiles":
        data = store.profiles(username)
      elif section == "ports":
        data = store.node_ports(node, username)
      else:
        data = store.autofind(node, username)
      return self._json(200, {"ok": True, "data": data})

    match = re.fullmatch(r"/api/v1/nodes/([^/]+)/onts/([^/]+)", path)
    if match:
      node = _node_or_404(match.group(1), username)
      row = store.ont(node, match.group(2), username)
      if row is None:
        raise ApiError(404, "ONT tidak ditemukan.")
      return self._json(200, {"ok": True, "data": row})

    match = re.fullmatch(r"/api/v1/nodes/([^/]+)/native-vlans", path)
    if match:
      node = _node_or_404(match.group(1), username)
      return self._json(
          200, {"ok": True, "data": store.native_vlans(node, username)}
      )

    # --- layanan DHCP / PPPoE ---
    match = re.fullmatch(
        r"/api/v1/nodes/([^/]+)/(dhcp|pppoe-users|leases|sessions|service)", path
    )
    if match:
      node = _node_or_404(match.group(1), username)
      section = match.group(2)
      if section == "dhcp":
        data = services.dhcp_pool(username, node)
      elif section == "pppoe-users":
        data = services.bras_users(username, node)
      elif section == "leases":
        data = services.leases(username)
      elif section == "service":
        data = services.server_settings(username, node)
      else:
        data = services.sessions(username)
      return self._json(200, {"ok": True, "data": data})

    match = re.fullmatch(r"/api/v1/nodes/([^/]+)/links", path)
    if match:
      node = _node_or_404(match.group(1), username)
      links = [
          link
          for link in store.topology(username)["links"]
          if node in (link["source_node"], link["target_node"])
      ]
      return self._json(200, {"ok": True, "data": links})

    raise ApiError(404, "Endpoint tidak ditemukan.")

  # ------------------------------------------------------------------ #
  def _post(self, path):
    body = self._read_json()
    username = str(body.get("username") or "admin")

    if path == "/api/v1/sessions":
      node = _node_or_404(str(body.get("node") or "Huawei_1"), username)
      sid = self._session_id()
      session = engine.get_session(
          f"{sid}:{node}", username, node, store.sysname(node, username)
      )
      return self._json(
          200,
          {"ok": True, "data": {"session": sid, "node": node, "prompt": session.prompt()}},
          {"X-Session-Id": sid},
      )

    # --- layanan DHCP / PPPoE ---
    match = re.fullmatch(r"/api/v1/nodes/([^/]+)/(dhcp|pppoe-users)", path)
    if match:
      sim_user = str(body.get("sim_user") or "admin")
      node = _node_or_404(match.group(1), sim_user)
      try:
        if match.group(2) == "dhcp":
          data = services.configure_dhcp(
              sim_user,
              node,
              str(body.get("start_ip") or ""),
              str(body.get("end_ip") or ""),
              str(body.get("gateway") or ""),
              str(body.get("dns") or ""),
              int(body.get("vlan") or 0),
              str(body.get("pool_name") or "POOL1"),
              str(body.get("local_ip") or ""),
          )
        else:
          data = services.add_bras_user(
              sim_user,
              node,
              str(body.get("username") or ""),
              str(body.get("password") or ""),
              str(body.get("profile") or "default"),
              int(body.get("vlan") or 0),
          )
          local_ip = str(body.get("local_ip") or "")
          if local_ip:
            services.configure_server(sim_user, node, "bras", local_ip)
          data["local_ip"] = local_ip
      except (ValueError, TypeError) as exc:
        raise ApiError(400, str(exc))
      return self._json(200, {"ok": True, "data": data})

    match = re.fullmatch(
        r"/api/v1/nodes/([^/]+)/onts/([^/]+)/(pppoe|ipoe|connect|disconnect)", path
    )
    if match:
      sim_user = str(body.get("sim_user") or "admin")
      node = _node_or_404(match.group(1), sim_user)
      port = str(body.get("port") or "0/1/0")
      action = match.group(3)
      try:
        ont_id = int(match.group(2))
        if action == "pppoe":
          data = services.set_ont_pppoe(
              sim_user,
              node,
              port,
              ont_id,
              str(body.get("username") or ""),
              str(body.get("password") or ""),
              int(body.get("vlan") or 0),
          )
        elif action == "ipoe":
          data = services.set_ont_ipoe(
              sim_user,
              node,
              port,
              ont_id,
              int(body.get("vlan") or 0),
              str(body.get("mac") or ""),
          )
        elif action == "connect":
          data = services.connect(sim_user, node, port, ont_id)
          if not data.get("ok"):
            raise ApiError(400, data.get("message", "PPPoE gagal."))
        else:
          data = services.disconnect(sim_user, node, port, ont_id)
          if not data.get("ok"):
            raise ApiError(400, data.get("message", "Disconnect gagal."))
      except ValueError as exc:
        raise ApiError(400, str(exc))
      return self._json(200, {"ok": True, "data": data})

    match = re.fullmatch(r"/api/v1/nodes/([^/]+)/cli(?:/batch)?", path)
    if not match:
      raise ApiError(404, "Endpoint tidak ditemukan.")
    node = _node_or_404(match.group(1), username)
    sid = self._session_id()
    session = engine.get_session(
        f"{sid}:{node}", username, node, store.sysname(node, username)
    )
    confirm = bool(body.get("confirm"))

    if path.endswith("/batch"):
      commands = body.get("commands") or []
      if not isinstance(commands, list) or not commands:
        raise ApiError(400, "Field 'commands' harus berupa list tidak kosong.")
      results = []
      for command in commands:
        result = session.run(command, confirm=confirm)
        results.append(result)
        if body.get("stop_on_error") and not result["ok"]:
          break
      return self._json(
          200,
          {"ok": True, "data": {"session": sid, "node": node, "results": results}},
          {"X-Session-Id": sid},
      )

    command = body.get("command")
    if not isinstance(command, str) or not command.strip():
      raise ApiError(400, "Field 'command' wajib diisi.")
    result = session.run(command, confirm=confirm)
    result["session"] = sid
    result["node"] = node
    return self._json(
        200 if result["ok"] else 422,
        {"ok": result["ok"], "data": result},
        {"X-Session-Id": sid},
    )

  # ------------------------------------------------------------------ #
  def _delete(self, path):
    match = re.fullmatch(r"/api/v1/sessions/([^/]+)", path)
    if not match:
      raise ApiError(404, "Endpoint tidak ditemukan.")
    sid = match.group(1)
    for key in [key for key in engine.SESSIONS if key.startswith(f"{sid}:")]:
      engine.drop_session(key)
    return self._json(200, {"ok": True, "data": {"deleted": sid}})


def store_username_from_query(raw_path):
  query = urlparse(raw_path).query
  for pair in query.split("&"):
    if pair.startswith("username="):
      return pair.split("=", 1)[1] or "admin"
  return "admin"


DOCS_HTML = """<!doctype html>
<html lang="id"><head><meta charset="utf-8"><title>OLT Simulator API</title>
<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}
code{background:#f2f2f2;padding:.1rem .3rem;border-radius:4px}td,th{border-bottom:1px solid #ddd;padding:.4rem;text-align:left}</style>
</head><body>
<h1>OLT Simulator REST API <small>v1</small></h1>
<p>Base URL: <code>http://127.0.0.1:8080/api/v1</code></p>
<table>
<tr><th>Method</th><th>Endpoint</th><th>Keterangan</th></tr>
<tr><td>GET</td><td><code>/health</code></td><td>Status server</td></tr>
<tr><td>GET</td><td><code>/topology</code></td><td>Node + link topologi</td></tr>
<tr><td>GET</td><td><code>/nodes</code></td><td>Daftar node</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}</code></td><td>Inventaris lengkap node</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}/boards</code></td><td>Board terpasang</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}/vlans</code></td><td>VLAN</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}/vlanifs</code></td><td>VLANIF + IP</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}/routes</code></td><td>Static route</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}/onts</code></td><td>ONT</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}/onts/{id}</code></td><td>Detail ONT</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}/service-ports</code></td><td>Service port</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}/profiles</code></td><td>DBA/line/service/TR069 profile</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}/autofind</code></td><td>ONT terhubung belum diprovisioning</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}/native-vlans</code></td><td>Native VLAN / access port</td></tr>
<tr><td>POST</td><td><code>/nodes/{node}/dhcp</code></td><td>Konfigurasi pool DHCP server</td></tr>
<tr><td>POST</td><td><code>/nodes/{node}/pppoe-users</code></td><td>Tambah akun PPPoE di BRAS</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}/leases</code></td><td>Lease DHCP aktif</td></tr>
<tr><td>GET</td><td><code>/nodes/{node}/sessions</code></td><td>Sesi PPPoE</td></tr>
<tr><td>POST</td><td><code>/nodes/{node}/onts/{id}/pppoe</code></td><td>Set kredensial PPPoE ONT</td></tr>
<tr><td>POST</td><td><code>/nodes/{node}/onts/{id}/connect</code></td><td>Dial PPPoE + ambil IP DHCP</td></tr>
<tr><td>POST</td><td><code>/nodes/{node}/onts/{id}/disconnect</code></td><td>Putus sesi + lepas IP</td></tr>
<tr><td>POST</td><td><code>/nodes/{node}/cli</code></td><td>Jalankan 1 perintah CLI</td></tr>
<tr><td>POST</td><td><code>/nodes/{node}/cli/batch</code></td><td>Beberapa perintah berurutan</td></tr>
<tr><td>POST</td><td><code>/sessions</code></td><td>Buat sesi CLI</td></tr>
<tr><td>DELETE</td><td><code>/sessions/{id}</code></td><td>Hapus sesi</td></tr>
</table>
<h2>Contoh</h2>
<pre>curl -X POST http://127.0.0.1:8080/api/v1/nodes/Huawei_1/cli \\
  -H "Content-Type: application/json" \\
  -d '{"command":"display ont info 0 all"}'</pre>
<p>Kirim header <code>X-Session-Id</code> untuk mempertahankan mode CLI
(mis. <code>enable</code> lalu <code>config</code>).</p>
</body></html>"""


class ApiServer(ThreadingHTTPServer):
  """Server yang tidak berisik saat browser klien menutup koneksi."""

  daemon_threads = True

  def handle_error(self, request, client_address):
    exc = sys.exc_info()[1]
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
      return
    super().handle_error(request, client_address)


def build_server(host="127.0.0.1", port=8080, allow_fallback=True):
  """Bind server; bila port terpakai, pilih port bebas terdekat.

  Mengembalikan (server, port_aktual). WinError 10013/10048 sering muncul
  karena port sudah dipakai aplikasi lain (mis. proxy/WSL/IDE).
  """
  try:
    server = ApiServer((host, port), Handler)
    return server, port
  except OSError as exc:
    if not allow_fallback:
      raise
    reason = exc.strerror or str(exc)
    print(f"[!] Port {port} tidak bisa dipakai: {reason}.", flush=True)
    for candidate in range(port + 1, port + 20):
      try:
        server = ApiServer((host, candidate), Handler)
        print(f"[i] Server otomatis memakai port {candidate}.", flush=True)
        print(f"[i] Hentikan aplikasi yang memakai port {port} bila ingin memakainya:", flush=True)
        print(f"    netstat -ano | findstr :{port}", flush=True)
        return server, candidate
      except OSError:
        continue
    server = ApiServer((host, 0), Handler)
    actual = server.server_address[1]
    print(f"[i] Server memakai port acak yang bebas: {actual}.", flush=True)
    return server, actual


def serve(host="127.0.0.1", port=8080, allow_fallback=True):
  server, actual_port = build_server(host, port, allow_fallback=allow_fallback)
  server.daemon_threads = True
  print(f"OLT Simulator API berjalan di http://{host}:{actual_port}/", flush=True)
  print(f"Dokumentasi: http://{host}:{actual_port}/api/v1/docs", flush=True)
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    print("\nServer dihentikan.")
  finally:
    server.server_close()


