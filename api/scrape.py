from http.server import BaseHTTPRequestHandler
import json
import os
import sys

# Ensure project root is on sys.path so scraper.py can be imported on Vercel
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scraper import scrape_chilean_stores


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8", errors="ignore") if length else "{}"
            data = json.loads(raw)

            result = scrape_chilean_stores(
                interests=data.get("interests") or [],
                reference=data.get("reference") or "",
                budget_min=int(data.get("budgetMin") or 0),
                budget_max=int(data.get("budgetMax") or 500000),
                stores=data.get("stores") or [],
                recipient=data.get("recipient") or "cualquiera",
            )
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = json.dumps({"error": str(exc), "items": [], "storeStatuses": {}}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
