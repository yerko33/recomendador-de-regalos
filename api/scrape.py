from http.server import BaseHTTPRequestHandler
import json
import os
import sys

# Ensure project root is on sys.path so scraper.py can be imported on Vercel
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scraper import scrape_chilean_stores

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

            if not isinstance(data, dict):
                return self._send_json(400, {"error": "Formato inválido.", "items": [], "storeStatuses": {}})

            interests = data.get("interests")
            stores = data.get("stores")
            if interests is not None and not isinstance(interests, list):
                return self._send_json(400, {"error": "Campo 'interests' debe ser una lista.", "items": [], "storeStatuses": {}})
            if stores is not None and not isinstance(stores, list):
                return self._send_json(400, {"error": "Campo 'stores' debe ser una lista.", "items": [], "storeStatuses": {}})

            try:
                budget_min = max(0, int(data.get("budgetMin") or 0))
                budget_max = min(5_000_000, max(1000, int(data.get("budgetMax") or 500000)))
            except (TypeError, ValueError):
                return self._send_json(400, {"error": "Presupuesto inválido.", "items": [], "storeStatuses": {}})

            result = scrape_chilean_stores(
                interests=interests or [],
                reference=str(data.get("reference") or "")[:200],
                budget_min=budget_min,
                budget_max=budget_max,
                stores=stores or [],
                recipient=str(data.get("recipient") or "cualquiera")[:40],
            )
            return self._send_json(200, result)
        except Exception:
            return self._send_json(500, {"error": "No se pudo completar la búsqueda.", "items": [], "storeStatuses": {}})
