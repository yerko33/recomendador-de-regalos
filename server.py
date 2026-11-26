"""
Servidor local para RegalaCL (sin dependencias externas — solo biblioteca estándar de Python 3).
Incluye:
- Servicio de archivos estáticos (index.html, styles.css, app.js, data/catalog.js)
- Base de datos SQLite (regalacl.db) para cuentas de usuario, regalos guardados e historial de búsquedas
- Endpoint /api/scrape conectado a scraper.py
"""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
import secrets
import sqlite3
import urllib.parse

from scraper import scrape_chilean_stores

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "regalacl.db")
PORT = int(os.environ.get("PORT", "8000"))


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


class RegalaHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        return json.loads(raw)

    def _get_auth_user(self, conn):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[7:].strip()
        return conn.execute(
            """
            SELECT u.* FROM users u
            JOIN sessions s ON s.user_id = u.id
            WHERE s.token = ?
            """,
            (token,),
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

        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        data = self._read_json()

        if parsed.path == "/api/register":
            name = (data.get("name") or "").strip()
            email = (data.get("email") or "").strip().lower()
            password = data.get("password") or ""
            if not email or len(password) < 4:
                return self._send_json(400, {"error": "Correo inválido o contraseña muy corta."})
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
            email = (data.get("email") or "").strip().lower()
            password = data.get("password") or ""
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
                gift = data.get("gift") or {}
                gid = gift.get("id")
                if not gid:
                    return self._send_json(400, {"error": "Regalo inválido."})
                conn.execute(
                    """
                    INSERT OR REPLACE INTO saved_gifts (user_id, gift_id, gift_json, note)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user["id"], gid, json.dumps(gift, ensure_ascii=False), gift.get("note", "")),
                )
                conn.commit()
                return self._send_json(200, {"ok": True})

        if parsed.path == "/api/saved-searches":
            with get_db() as conn:
                user = self._get_auth_user(conn)
                if not user:
                    return self._send_json(401, {"error": "Inicia sesión para guardar búsquedas."})
                search = data.get("search") or {}
                conn.execute(
                    "INSERT INTO saved_searches (user_id, search_json) VALUES (?, ?)",
                    (user["id"], json.dumps(search, ensure_ascii=False)),
                )
                conn.commit()
                return self._send_json(200, {"ok": True})

        if parsed.path == "/api/scrape":
            result = scrape_chilean_stores(
                interests=data.get("interests") or [],
                reference=data.get("reference") or "",
                budget_min=int(data.get("budgetMin") or 0),
                budget_max=int(data.get("budgetMax") or 500000),
                stores=data.get("stores") or [],
                recipient=data.get("recipient") or "cualquiera",
            )
            return self._send_json(200, result)

        return self._send_json(404, {"error": "Ruta no encontrada"})

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/saved-gifts/") and parsed.path.endswith("/note"):
            gift_id = urllib.parse.unquote(parsed.path[len("/api/saved-gifts/"):-len("/note")])
            data = self._read_json()
            with get_db() as conn:
                user = self._get_auth_user(conn)
                if not user:
                    return self._send_json(401, {"error": "No autorizado"})
                conn.execute(
                    "UPDATE saved_gifts SET note = ? WHERE user_id = ? AND gift_id = ?",
                    (data.get("note", ""), user["id"], gift_id),
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
