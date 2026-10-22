# -*- coding: utf-8 -*-
"""
FnOQuantAI: Local Dashboard Server
Serves index.html on http://localhost:8001 to eliminate browser file:/// CORS restrictions.
Also provides a local read-only API proxy for Neon PostgreSQL queries.
"""

import os
import sys
import json
import logging
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

DIR_PATH = r"D:\SM_Server\MarketTool\FnOQuantAI"
COMMON_MODULE_DIR = r"D:\SM_Server\CommonModule"
if COMMON_MODULE_DIR not in sys.path:
    sys.path.insert(0, COMMON_MODULE_DIR)

from script import postgres_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("FnOQuantAIDashboardServer")

PORT = 8001


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR_PATH, **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Neon-Connection-String")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/query":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8"))
                sql = body.get("query", "")
                sql_clean = sql.strip().upper()
                if not sql_clean.startswith("SELECT") and not sql_clean.startswith("WITH"):
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Only SELECT queries are allowed"}).encode("utf-8"))
                    return

                conn = None
                try:
                    conn = postgres_connection.get_read_connection()
                except Exception as replica_err:
                    log.warning(f"Read replica connection failed ({replica_err}), falling back to primary write connection...")
                    try:
                        conn = postgres_connection.get_write_connection()
                    except Exception as primary_err:
                        raise RuntimeError(f"Database connection failed: {replica_err} / {primary_err}")

                rows = postgres_connection.execute_query(sql, conn=conn)

                def serialize_val(v):
                    if hasattr(v, "isoformat"):
                        return v.isoformat()
                    if hasattr(v, "__float__"):
                        return float(v)
                    return str(v) if v is not None else None

                clean_rows = []
                for r in (rows or []):
                    clean_rows.append({k: serialize_val(v) for k, v in r.items()})

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"rows": clean_rows}).encode("utf-8"))

            except Exception as exc:
                log.error(f"Error handling /api/query: {exc}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


def run_server():
    server_address = ("", PORT)
    try:
        httpd = HTTPServer(server_address, DashboardHandler)
        log.info(f"FnOQuantAI Dashboard server running at http://localhost:{PORT}/index.html")
        webbrowser.open(f"http://localhost:{PORT}/index.html")
        httpd.serve_forever()
    except OSError as err:
        if err.errno == 10048:
            log.warning(f"Port {PORT} already active. Opening browser...")
            webbrowser.open(f"http://localhost:{PORT}/index.html")
        else:
            raise


if __name__ == "__main__":
    run_server()
