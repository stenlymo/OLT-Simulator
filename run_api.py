"""Entry point REST API simulator OLT.

Contoh:
    python run_api.py                 # http://127.0.0.1:8080 (pindah port bila sibuk)
    python run_api.py --port 9000
    python run_api.py --db other.db --token rahasia
    python run_api.py --strict-port   # gagal bila port tidak tersedia
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
  parser = argparse.ArgumentParser(description="OLT Simulator REST API")
  parser.add_argument("--host", default="127.0.0.1")
  parser.add_argument("--port", type=int, default=8080)
  parser.add_argument("--db", default=None, help="Path database SQLite (default: olt_simulator.db)")
  parser.add_argument("--token", default=None, help="Token Bearer opsional")
  parser.add_argument(
      "--strict-port",
      action="store_true",
      help="Jangan pindah port otomatis bila port yang diminta sibuk",
  )
  args = parser.parse_args()

  if args.db:
    os.environ["OLT_DB_NAME"] = args.db
  if args.token:
    os.environ["OLT_API_TOKEN"] = args.token

  sys.path.insert(0, str(ROOT))

  from modules.database import init_db

  init_db()

  from modules import database

  print(f"Database: {Path(database.DB_NAME).resolve()}", flush=True)

  from api.server import serve

  try:
    serve(args.host, args.port, allow_fallback=not args.strict_port)
  except OSError as exc:
    print(f"[x] Tidak bisa membuka port {args.port}: {exc.strerror or exc}")
    print(f"    Cek pemakai port: netstat -ano | findstr :{args.port}")
    print("    Pilih port lain, mis.: python run_api.py --port 8081")
    return 2
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
