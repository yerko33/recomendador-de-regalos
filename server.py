"""
Servidor local para RegalaCL (sin dependencias externas — solo biblioteca estándar de Python 3).
- Lista blanca explícita de estáticos + mapa MIME seguro (evita bloqueo de .js en Windows con nosniff)
- SQLite con cierre garantizado (contextlib.closing), WAL mode, FK activas y sesiones con TTL de 7 días
- Dispatch de rutas declarativo y validación compartida con Vercel mediante scraper.handle_scrape_payload
"""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import urllib.parse

from scraper import handle_scrape_payload

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("regalacl.server")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "regalacl.db")
PORT = int(os.environ.get("PORT", "8000"))
MAX_BODY_BYTES = 16 * 1024
SESSION_TTL_DAYS = 7

MIME_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
}

STATIC_ALLOWLIST = {
    "/": "index.html",
    "/index.html": "index.html",
    "/data/catalog.js": os.path.join("data", "catalog.js"),
    "/data/domain-rules.json": os.path.join("data", "domain-rules.json"),
    "/css/tokens.css": os.path.join("css", "tokens.css"),
    "/css/layout.css": os.path.join("css", "layout.css"),
    "/css/finder.css": os.path.join("css", "finder.css"),
    "/css/cards.css": os.path.join("css", "cards.css"),
    "/css/saved-modal.css": os.path.join("css", "saved-modal.css"),
    "/js/utils.js": os.path.join("js", "utils.js"),
    "/js/auth-store.js": os.path.join("js", "auth-store.js"),
    "/js/recommender.js": os.path.join("js", "recommender.js"),
    "/js/ui-render.js": os.path.join("js", "ui-render.js"),
    "/js/main.js": os.path.join("js", "main.js"),
}


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
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
        # Limpiar sesiones expiradas al iniciar
        conn.execute("DELETE FROM sessions WHERE created_at < datetime('now', ?)", (f"-{SESSION_TTL_DAYS} days",))
        if not conn.execute("SELECT id FROM users WHERE email = ?", ("demo@regalacl.cl",)).fetchone():
            conn.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                ("Usuario Demo", "demo@regalacl.cl", hash_password("demo")),
            )


def serialize_user(conn, user_row):
    uid = user_row["id"]
    saved_gifts = []
    for r in conn.execute("SELECT gift_json, note FROM saved_gifts WHERE user_id = ? ORDER BY id DESC", (uid,)):
        item = json.loads(r["gift_json"])
        item["note"] = r["note"] or ""
        saved_gifts.append(item)

    saved_searches = [
        json.loads(r["search_json"])
        for r in conn.execute("SELECT search_json FROM saved_searches WHERE user_id = ? ORDER BY id DESC LIMIT 20", (uid,))
    ]
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
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
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
        ext = os.path.splitext(full_path)[1].lower()
        ctype = MIME_TYPES.get(ext, "application/octet-stream")
        with open(full_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self._send_security_headers()
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            raise InvalidJsonError()
        if length < 0 or length > MAX_BODY_BYTES:
            raise PayloadTooLargeError()
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise InvalidJsonError()
        if not isinstance(parsed, dict):
            raise InvalidJsonError()
        return parsed

    def _get_bearer_token(self):
        auth = self.headers.get("Authorization", "")
        return auth[7:].strip() if auth.startswith("Bearer ") and auth[7:].strip() else None

    def _get_auth_user(self, conn):
        token = self._get_bearer_token()
        if not token:
            return None
        return conn.execute(
            """
            SELECT u.* FROM users u
            JOIN sessions s ON s.user_id = u.id
            WHERE s.token = ? AND s.created_at >= datetime('now', ?)
            """,
            (token, f"-{SESSION_TTL_DAYS} days"),
        ).fetchone()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/health":
            return self._send_json(200, {"status": "ok", "service": "RegalaCL"})
        if path == "/api/me":
            with get_db() as conn:
                user = self._get_auth_user(conn)
                if not user:
                    return self._send_json(401, {"error": "No autorizado"})
                return self._send_json(200, {"user": serialize_user(conn, user)})
        if path in STATIC_ALLOWLIST:
            return self._serve_static(STATIC_ALLOWLIST[path])
        return self._send_json(404, {"error": "Ruta no encontrada"})

    # --- Handlers POST ---
    def _post_logout(self, _data):
        token = self._get_bearer_token()
        if token:
            with get_db() as conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return 200, {"ok": True}

    def _post_register(self, data):
        name = str(data.get("name") or "").strip()[:80]
        email = str(data.get("email") or "").strip().lower()[:120]
        password = str(data.get("password") or "")
        if not email or "@" not in email or len(password) < 8:
            return 400, {"error": "Correo inválido o contraseña menor a 8 caracteres."}
        with get_db() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                    (name or email.split("@")[0], email, hash_password(password)),
                )
                uid = cur.lastrowid
                token = secrets.token_urlsafe(32)
                conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, uid))
                user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
                return 201, {"token": token, "user": serialize_user(conn, user)}
            except sqlite3.IntegrityError:
                return 409, {"error": "Ya existe una cuenta con ese correo."}

    def _post_login(self, data):
        email = str(data.get("email") or "").strip().lower()
        password = str(data.get("password") or "")
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if not user or not verify_password(password, user["password_hash"]):
                return 401, {"error": "Correo o contraseña incorrectos."}
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user["id"]))
            return 200, {"token": token, "user": serialize_user(conn, user)}

    def _post_saved_gifts(self, data):
        gift = data.get("gift")
        if not isinstance(gift, dict) or not str(gift.get("id") or "").strip():
            return 400, {"error": "Regalo inválido."}
        gid = str(gift.get("id"))[:100]
        with get_db() as conn:
            user = self._get_auth_user(conn)
            if not user:
                return 401, {"error": "Inicia sesión para guardar regalos."}
            conn.execute(
                "INSERT OR REPLACE INTO saved_gifts (user_id, gift_id, gift_json, note) VALUES (?, ?, ?, ?)",
                (user["id"], gid, json.dumps(gift, ensure_ascii=False), str(gift.get("note", ""))[:300]),
            )
            return 200, {"ok": True}

    def _post_saved_searches(self, data):
        search = data.get("search")
        if not isinstance(search, dict):
            return 400, {"error": "Búsqueda inválida."}
        with get_db() as conn:
            user = self._get_auth_user(conn)
            if not user:
                return 401, {"error": "Inicia sesión para guardar búsquedas."}
            conn.execute(
                "INSERT INTO saved_searches (user_id, search_json) VALUES (?, ?)",
                (user["id"], json.dumps(search, ensure_ascii=False)),
            )
            return 200, {"ok": True}

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            data = self._read_json()
        except PayloadTooLargeError:
            return self._send_json(413, {"error": "Solicitud demasiado grande."})
        except InvalidJsonError:
            return self._send_json(400, {"error": "JSON inválido."})

        routes = {
            "/api/logout": self._post_logout,
            "/api/register": self._post_register,
            "/api/login": self._post_login,
            "/api/saved-gifts": self._post_saved_gifts,
            "/api/saved-searches": self._post_saved_searches,
            "/api/scrape": handle_scrape_payload,
        }
        handler_fn = routes.get(path)
        if not handler_fn:
            return self._send_json(404, {"error": "Ruta no encontrada"})
        try:
            status, payload = handler_fn(data)
            return self._send_json(status, payload)
        except sqlite3.Error as exc:
            logger.exception("SQLite error on %s: %s", path, exc)
            return self._send_json(500, {"error": "Error interno de base de datos."})

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/saved-gifts/") and path.endswith("/note"):
            gift_id = urllib.parse.unquote(path[len("/api/saved-gifts/"):-len("/note")])
            try:
                data = self._read_json()
            except PayloadTooLargeError:
                return self._send_json(413, {"error": "Solicitud demasiado grande."})
            except InvalidJsonError:
                return self._send_json(400, {"error": "JSON inválido."})
            with get_db() as conn:
                user = self._get_auth_user(conn)
                if not user:
                    return self._send_json(401, {"error": "No autorizado"})
                conn.execute(
                    "UPDATE saved_gifts SET note = ? WHERE user_id = ? AND gift_id = ?",
                    (str(data.get("note", ""))[:300], user["id"], gift_id),
                )
                return self._send_json(200, {"ok": True})
        return self._send_json(404, {"error": "Ruta no encontrada"})

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/saved-gifts/"):
            gift_id = urllib.parse.unquote(path[len("/api/saved-gifts/"):])
            with get_db() as conn:
                user = self._get_auth_user(conn)
                if not user:
                    return self._send_json(401, {"error": "No autorizado"})
                conn.execute("DELETE FROM saved_gifts WHERE user_id = ? AND gift_id = ?", (user["id"], gift_id))
                return self._send_json(200, {"ok": True})
        return self._send_json(404, {"error": "Ruta no encontrada"})


if __name__ == "__main__":
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), RegalaHandler)
    logger.info("RegalaCL corriendo en http://localhost:%d", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
