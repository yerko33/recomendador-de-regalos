from http.server import BaseHTTPRequestHandler
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scraper import handle_scrape_payload

MAX_BODY_BYTES = 16 * 1024  # 16 KB


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._send_json(400, {"error": "Solicitud inválida.", "items": [], "storeStatuses": {}})

        if length < 0 or length > MAX_BODY_BYTES:
            return self._send_json(413, {"error": "Solicitud demasiado grande.", "items": [], "storeStatuses": {}})

        raw = self.rfile.read(length).decode("utf-8", errors="ignore") if length else "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return self._send_json(400, {"error": "JSON inválido.", "items": [], "storeStatuses": {}})

        status, payload = handle_scrape_payload(data)
        return self._send_json(status, payload)
