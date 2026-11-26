"""
Servidor local para RegalaCL (sin dependencias externas — solo biblioteca estándar de Python 3).
Incluye:
- Lista blanca explícita de archivos estáticos (evita exponer regalacl.db, .git/ o código backend)
- Base de datos SQLite (regalacl.db) con sesiones con expiración de 7 días y revocación en /api/logout
- Límite de tamaño de cuerpo HTTP y cabeceras de seguridad
- Endpoint /api/scrape conectado a scraper.py
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import mimetypes
import os
import secrets
import sqlite3
import urllib.parse

from scraper import scrape_chilean_stores

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "regalacl.db")
PORT = int(os.environ.get("PORT", "8000"))
MAX_BODY_BYTES = 16 * 1024  # 16 KB
SESSION_TTL_DAYS = 7

STATIC_ALLOWLIST = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
    "/data/catalog.js": os.path.join("data", "catalog.js"),
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str, salt: str = None):
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
        return secrets.compare_digest(hash_password(password, salt), stored)
    except ValueError:
        return False


def init_db():
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS saved_gifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                gift_id TEXT NOT NULL,
                gift_json TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, gift_id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS saved_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                search_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """
        )
        cur = conn.execute("SELECT id FROM users WHERE email = ?", ("demo@regalacl.cl",))
        if not cur.fetchone():
            conn.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                ("Usuario Demo", "demo@regalacl.cl", hash_password("demo")),
            )
        conn.commit()


def serialize_user(conn, user_row):
    uid = user_row["id"]
    gifts_rows = conn.execute(
        "SELECT gift_json, note FROM saved_gifts WHERE user_id = ? ORDER BY id DESC", (uid,)
    ).fetchall()
    saved_gifts = []
    for r in gifts_rows:
        item = json.loads(r["gift_json"])
        item["note"] = r["note"] or ""
        saved_gifts.append(item)

    searches_rows = conn.execute(
        "SELECT search_json FROM saved_searches WHERE user_id = ? ORDER BY id DESC LIMIT 20", (uid,)
    ).fetchall()
    saved_searches = [json.loads(r["search_json"]) for r in searches_rows]

    return {
        "id": uid,
        "name": user_row["name"],
        "email": user_row["email"],
        "savedGifts": saved_gifts,
        "savedSearches": saved_searches,
    }


class PayloadTooLargeError(Exception):
    pass


class InvalidJsonError(Exception):
    pass


class RegalaHandler(BaseHTTPRequestHandler):
    def _send_security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' https: data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'",
        )

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, rel_path: str):
        full_path = os.path.join(BASE_DIR, rel_path)
        if not os.path.isfile(full_path):
            return self._send_json(404, {"error": "Archivo no encontrado"})
        ctype, _ = mimetypes.guess_type(full_path)
        with open(full_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self._send_security_headers()
        self.send_header("Content-Type", f"{ctype or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            raise InvalidJsonError("Content-Length inválido")
        if length < 0 or length > MAX_BODY_BYTES:
            raise PayloadTooLargeError("Cuerpo demasiado grande")
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise InvalidJsonError("JSON inválido")
        if not isinstance(parsed, dict):
            raise InvalidJsonError("Se esperaba un objeto JSON")
        return parsed

    def _get_bearer_token(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        return auth[7:].strip() or None

    def _get_auth_user(self, conn):
        token = self._get_bearer_token()
        if not token:
            return None
        return conn.execute(
            """
            SELECT u.* FROM users u
            JOIN sessions s ON s.user_id = u.id
            WHERE s.token = ?
              AND s.created_at >= datetime('now', ?)
            """,
            (token, f"-{SESSION_TTL_DAYS} days"),
        ).fetchone()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/health":
            return self._send_json(200, {"status": "ok", "service": "RegalaCL"})

        if parsed.path == "/api/me":
            with get_db() as conn:
                user = self._get_auth_user(conn)
                if not user:
                    return self._send_json(401, {"error": "No autorizado"})
                return self._send_json(200, {"user": serialize_user(conn, user)})

        if parsed.path in STATIC_ALLOWLIST:
            return self._serve_static(STATIC_ALLOWLIST[parsed.path])

        return self._send_json(404, {"error": "Ruta no encontrada"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            data = self._read_json()
        except PayloadTooLargeError:
            return self._send_json(413, {"error": "Solicitud demasiado grande."})
        except InvalidJsonError:
            return self._send_json(400, {"error": "JSON inválido."})

        if parsed.path == "/api/logout":
            token = self._get_bearer_token()
            if token:
                with get_db() as conn:
                    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                    conn.commit()
            return self._send_json(200, {"ok": True})

        if parsed.path == "/api/register":
            name = str(data.get("name") or "").strip()[:80]
            email = str(data.get("email") or "").strip().lower()[:120]
            password = str(data.get("password") or "")
            if not email or "@" not in email or len(password) < 8:
                return self._send_json(400, {"error": "Correo inválido o contraseña menor a 8 caracteres."})
            with get_db() as conn:
                try:
                    cur = conn.execute(
                        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                        (name or email.split("@")[0], email, hash_password(password)),
                    )
                    uid = cur.lastrowid
                    token = secrets.token_urlsafe(32)
                    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, uid))
                    conn.commit()
                    user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
                    return self._send_json(201, {"token": token, "user": serialize_user(conn, user)})
                except sqlite3.IntegrityError:
                    return self._send_json(409, {"error": "Ya existe una cuenta con ese correo."})

        if parsed.path == "/api/login":
            email = str(data.get("email") or "").strip().lower()
            password = str(data.get("password") or "")
            with get_db() as conn:
                user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
                if not user or not verify_password(password, user["password_hash"]):
                    return self._send_json(401, {"error": "Correo o contraseña incorrectos."})
                token = secrets.token_urlsafe(32)
                conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user["id"]))
                conn.commit()
                return self._send_json(200, {"token": token, "user": serialize_user(conn, user)})

        if parsed.path == "/api/saved-gifts":
            with get_db() as conn:
                user = self._get_auth_user(conn)
                if not user:
                    return self._send_json(401, {"error": "Inicia sesión para guardar regalos."})
                gift = data.get("gift")
                if not isinstance(gift, dict):
                    return self._send_json(400, {"error": "Regalo inválido."})
                gid = str(gift.get("id") or "")[:100]
                if not gid:
                    return self._send_json(400, {"error": "Regalo inválido."})
                conn.execute(
                    """
                    INSERT OR REPLACE INTO saved_gifts (user_id, gift_id, gift_json, note)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user["id"], gid, json.dumps(gift, ensure_ascii=False), str(gift.get("note", ""))[:300]),
                )
                conn.commit()
                return self._send_json(200, {"ok": True})

        if parsed.path == "/api/saved-searches":
            with get_db() as conn:
                user = self._get_auth_user(conn)
                if not user:
                    return self._send_json(401, {"error": "Inicia sesión para guardar búsquedas."})
                search = data.get("search")
                if not isinstance(search, dict):
                    return self._send_json(400, {"error": "Búsqueda inválida."})
                conn.execute(
                    "INSERT INTO saved_searches (user_id, search_json) VALUES (?, ?)",
                    (user["id"], json.dumps(search, ensure_ascii=False)),
                )
                conn.commit()
                return self._send_json(200, {"ok": True})

        if parsed.path == "/api/scrape":
            interests = data.get("interests")
            stores = data.get("stores")
            if interests is not None and not isinstance(interests, list):
                return self._send_json(400, {"error": "Campo 'interests' inválido.", "items": [], "storeStatuses": {}})
            if stores is not None and not isinstance(stores, list):
                return self._send_json(400, {"error": "Campo 'stores' inválido.", "items": [], "storeStatuses": {}})
            try:
                budget_min = max(0, int(data.get("budgetMin") or 0))
                budget_max = min(5_000_000, max(1000, int(data.get("budgetMax") or 500000)))
            except (TypeError, ValueError):
                return self._send_json(400, {"error": "Presupuesto inválido.", "items": [], "storeStatuses": {}})
            try:
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

        return self._send_json(404, {"error": "Ruta no encontrada"})

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/saved-gifts/") and parsed.path.endswith("/note"):
            gift_id = urllib.parse.unquote(parsed.path[len("/api/saved-gifts/"):-len("/note")])
            try:
                data = self._read_json()
            except (PayloadTooLargeError, InvalidJsonError):
                return self._send_json(400, {"error": "Solicitud inválida."})
            with get_db() as conn:
                user = self._get_auth_user(conn)
                if not user:
                    return self._send_json(401, {"error": "No autorizado"})
                conn.execute(
                    "UPDATE saved_gifts SET note = ? WHERE user_id = ? AND gift_id = ?",
                    (str(data.get("note", ""))[:300], user["id"], gift_id),
                )
                conn.commit()
                return self._send_json(200, {"ok": True})
        return self._send_json(404, {"error": "Ruta no encontrada"})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/saved-gifts/"):
            gift_id = urllib.parse.unquote(parsed.path[len("/api/saved-gifts/"):])
            with get_db() as conn:
                user = self._get_auth_user(conn)
                if not user:
                    return self._send_json(401, {"error": "No autorizado"})
                conn.execute(
                    "DELETE FROM saved_gifts WHERE user_id = ? AND gift_id = ?",
                    (user["id"], gift_id),
                )
                conn.commit()
                return self._send_json(200, {"ok": True})
        return self._send_json(404, {"error": "Ruta no encontrada"})


if __name__ == "__main__":
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), RegalaHandler)
    print(f"RegalaCL corriendo en http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
