#!/usr/bin/env python3
"""Account login and invite-code registration in front of Marzban.

Standard library only. Run `python3 auth_server.py --help` for the commands.
"""
import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USERNAME_RE = re.compile(r"[A-Za-z0-9_]{3,24}")
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MAX_BODY = 4096
GB = 1024**3

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  username TEXT PRIMARY KEY,
  pw_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS invites(
  code TEXT PRIMARY KEY,
  uses_left INTEGER NOT NULL,
  data_limit_gb REAL NOT NULL,
  expire_days INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);
"""


def load_config(env=None):
    env = os.environ if env is None else env
    return {
        "marzban_url": env.get("MARZBAN_URL", "").rstrip("/"),
        "admin_user": env.get("MARZBAN_ADMIN_USER", ""),
        "admin_pass": env.get("MARZBAN_ADMIN_PASS", ""),
        "db_path": env.get("AUTH_DB", "/opt/flclash-auth/data.db"),
        "host": env.get("AUTH_HOST", "::"),
        "port": int(env.get("AUTH_PORT", "9000")),
        "cert": env.get("AUTH_CERT", ""),
        "key": env.get("AUTH_KEY", ""),
        "proxies": [
            p
            for p in env.get(
                "MARZBAN_PROXIES", "vless,trojan,vmess,shadowsocks"
            ).split(",")
            if p
        ],
        "vless_flow": env.get("MARZBAN_VLESS_FLOW", "xtls-rprx-vision"),
        "insecure": env.get("MARZBAN_INSECURE", "") == "1",
        "sub_prefix": env.get("SUB_URL_PREFIX", "").rstrip("/"),
    }


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = hash_password(secrets.token_hex(8))


def new_invite_code():
    raw = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def normalize_invite(code):
    return re.sub(r"[\s-]", "", str(code or "")).upper()


def format_invite(raw):
    return f"{raw[:5]}-{raw[5:]}" if len(raw) == 10 else raw


class RateLimiter:
    def __init__(self, limit, window):
        self.limit = limit
        self.window = window
        self._hits = {}
        self._lock = threading.Lock()

    def _recent(self, key, now):
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        self._hits[key] = hits
        return hits

    def blocked(self, key):
        with self._lock:
            return len(self._recent(key, time.time())) >= self.limit

    def hit(self, key):
        with self._lock:
            now = time.time()
            self._recent(key, now).append(now)

    def clear(self, key):
        with self._lock:
            self._hits.pop(key, None)


class MarzbanError(Exception):
    def __init__(self, status, detail=""):
        super().__init__(f"Marzban {status}: {detail}")
        self.status = status


class Marzban:
    def __init__(self, cfg):
        self.cfg = cfg
        self._token = None
        self._token_at = 0.0
        self._lock = threading.Lock()
        self._ctx = None
        if cfg["insecure"]:
            self._ctx = ssl.create_default_context()
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    def _send(self, method, path, body=None, form=None, token=None):
        headers = {}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            self.cfg["marzban_url"] + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=15, context=self._ctx) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise MarzbanError(e.code, e.read().decode(errors="replace")[:200])
        except (urllib.error.URLError, OSError) as e:
            raise MarzbanError(0, str(e))
        return json.loads(raw) if raw else {}

    def _get_token(self, force=False):
        with self._lock:
            if not force and self._token and time.time() - self._token_at < 1800:
                return self._token
            res = self._send(
                "POST",
                "/api/admin/token",
                form={
                    "username": self.cfg["admin_user"],
                    "password": self.cfg["admin_pass"],
                },
            )
            self._token = res["access_token"]
            self._token_at = time.time()
            return self._token

    def _call(self, method, path, body=None):
        try:
            return self._send(method, path, body, token=self._get_token())
        except MarzbanError as e:
            if e.status != 401:
                raise
        return self._send(method, path, body, token=self._get_token(force=True))

    def get_user(self, username):
        try:
            return self._call("GET", f"/api/user/{urllib.parse.quote(username)}")
        except MarzbanError as e:
            if e.status == 404:
                return None
            raise

    def enabled_protocols(self):
        wanted = self.cfg["proxies"]
        try:
            available = self._call("GET", "/api/inbounds")
        except MarzbanError as e:
            sys.stderr.write(f"inbounds lookup failed, using config: {e}\n")
            return wanted
        if not isinstance(available, dict):
            return wanted
        usable = [n for n in wanted if available.get(n)]
        skipped = [n for n in wanted if n not in usable]
        if skipped:
            sys.stderr.write(f"no inbound on the panel for: {skipped}\n")
        return usable

    def create_user(self, username, data_limit_gb, expire_days, note=""):
        names = self.enabled_protocols()
        if not names:
            raise MarzbanError(400, "no usable protocol has an inbound")
        proxies = {}
        for name in names:
            if name == "vless" and self.cfg["vless_flow"]:
                proxies[name] = {"flow": self.cfg["vless_flow"]}
            else:
                proxies[name] = {}
        expire = int(time.time() + expire_days * 86400) if expire_days else 0
        return self._call(
            "POST",
            "/api/user",
            {
                "username": username,
                "proxies": proxies,
                "expire": expire,
                "data_limit": int(data_limit_gb * GB),
                "data_limit_reset_strategy": "no_reset",
                "status": "active",
                "note": note,
            },
        )

    def delete_user(self, username):
        try:
            self._call("DELETE", f"/api/user/{urllib.parse.quote(username)}")
        except MarzbanError as e:
            if e.status != 404:
                raise


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class Service:
    def __init__(self, cfg, marzban=None, db=None):
        self.cfg = cfg
        self.marzban = marzban or Marzban(cfg)
        self.db = db or open_db(cfg["db_path"])
        self.db_lock = threading.Lock()
        self.ip_limiter = RateLimiter(30, 300)
        self.user_limiter = RateLimiter(5, 900)

    def _info(self, username):
        user = self.marzban.get_user(username)
        if user is None:
            raise ApiError(
                410, "account_unlinked", "Account has no subscription."
            )
        url = user.get("subscription_url", "")
        if url and not url.startswith("http"):
            url = (self.cfg["sub_prefix"] or self.cfg["marzban_url"]) + url
        return {
            "ok": True,
            "username": username,
            "subscription_url": url,
            "status": user.get("status", ""),
            "used": user.get("used_traffic", 0),
            "limit": user.get("data_limit") or 0,
            "expire": user.get("expire") or 0,
        }

    def _check_credentials(self, username, password):
        key = f"user:{username.lower()}"
        if self.user_limiter.blocked(key):
            raise ApiError(429, "too_many_attempts", "Try again later.")
        with self.db_lock:
            row = self.db.execute(
                "SELECT pw_hash FROM accounts WHERE username=?", (username,)
            ).fetchone()
        ok = verify_password(password, row[0] if row else DUMMY_HASH)
        if not row or not ok:
            self.user_limiter.hit(key)
            raise ApiError(401, "bad_credentials", "Wrong account or password.")
        self.user_limiter.clear(key)

    def login(self, body):
        username = str(body.get("username", ""))
        self._check_credentials(username, str(body.get("password", "")))
        return self._info(username)

    def register(self, body):
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        invite = normalize_invite(body.get("invite", ""))
        if not USERNAME_RE.fullmatch(username):
            raise ApiError(
                400, "bad_username", "Use 3-24 letters, digits or underscore."
            )
        if not 8 <= len(password) <= 64:
            raise ApiError(400, "bad_password", "Password must be 8-64 characters.")
        with self.db_lock:
            return self._register_locked(username, password, invite)

    def _register_locked(self, username, password, invite):
        taken = ApiError(409, "username_taken", "That account name is taken.")
        if self.db.execute(
            "SELECT 1 FROM accounts WHERE username=?", (username,)
        ).fetchone():
            raise taken
        try:
            exists = self.marzban.get_user(username) is not None
        except MarzbanError as e:
            if e.status == 403:
                raise taken
            raise
        if exists:
            raise taken
        row = self.db.execute(
            "SELECT data_limit_gb, expire_days, note FROM invites "
            "WHERE code=? AND uses_left>0 AND (expires_at=0 OR expires_at>?)",
            (invite, int(time.time())),
        ).fetchone()
        if not row:
            raise ApiError(403, "invalid_invite", "Invite code is not valid.")
        limit_gb, expire_days, note = row
        self.db.execute(
            "UPDATE invites SET uses_left=uses_left-1 WHERE code=?", (invite,)
        )
        try:
            self.marzban.create_user(username, limit_gb, expire_days, note)
            self.db.execute(
                "INSERT INTO accounts(username, pw_hash, created_at) "
                "VALUES(?,?,?)",
                (username, hash_password(password), int(time.time())),
            )
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            sys.stderr.write(f"register failed for {username}: {e}\n")
            try:
                self.marzban.delete_user(username)
            except MarzbanError:
                pass
            if isinstance(e, MarzbanError):
                raise ApiError(502, "upstream_error", "Panel is unavailable.")
            raise
        return self._info(username)

    def change_password(self, body):
        username = str(body.get("username", ""))
        new = str(body.get("new_password", ""))
        if not 8 <= len(new) <= 64:
            raise ApiError(400, "bad_password", "Password must be 8-64 characters.")
        self._check_credentials(username, str(body.get("password", "")))
        with self.db_lock:
            self.db.execute(
                "UPDATE accounts SET pw_hash=? WHERE username=?",
                (hash_password(new), username),
            )
            self.db.commit()
        return {"ok": True}


def open_db(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.executescript(SCHEMA)
    return db


def make_handler(service):
    routes = {
        "/api/login": service.login,
        "/api/register": service.register,
        "/api/password": service.change_password,
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "auth"
        sys_version = ""
        timeout = 15

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.client_address[0], fmt % args))

        def _reply(self, status, payload):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _error(self, status, code, message):
            self._reply(
                status, {"ok": False, "error": code, "message": message}
            )

        def do_GET(self):
            if self.path == "/healthz":
                self._reply(200, {"ok": True})
            else:
                self._error(404, "not_found", "Not found.")

        def do_POST(self):
            handler = routes.get(self.path)
            if handler is None:
                return self._error(404, "not_found", "Not found.")
            ip = self.client_address[0]
            if service.ip_limiter.blocked(ip):
                return self._error(429, "too_many_requests", "Slow down.")
            service.ip_limiter.hit(ip)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                return self._error(400, "bad_request", "Invalid request.")
            try:
                self._reply(200, handler(body))
            except ApiError as e:
                self._error(e.status, e.code, e.message)
            except MarzbanError as e:
                sys.stderr.write(f"panel error: {e}\n")
                self._error(502, "upstream_error", "Panel is unavailable.")
            except Exception as e:
                sys.stderr.write(f"internal error: {e!r}\n")
                self._error(500, "internal_error", "Server error.")

    return Handler


class DualStackServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler):
        host, port = address
        if ":" not in host:
            return super().__init__(address, handler)
        self.address_family = socket.AF_INET6
        try:
            super().__init__(address, handler)
        except OSError:
            self.address_family = socket.AF_INET
            super().__init__(("0.0.0.0", port), handler)

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def build_server(cfg, service=None):
    service = service or Service(cfg)
    server = DualStackServer((cfg["host"], cfg["port"]), make_handler(service))
    if cfg["cert"] and cfg["key"]:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cfg["cert"], cfg["key"])
        server.socket = ctx.wrap_socket(
            server.socket, server_side=True, do_handshake_on_connect=False
        )
    return server


def cmd_serve(cfg, args):
    for name in ("marzban_url", "admin_user", "admin_pass"):
        if not cfg[name]:
            sys.exit(f"Missing setting: {name}")
    server = build_server(cfg)
    scheme = "https" if cfg["cert"] else "http"
    print(f"Listening on {scheme}://[{cfg['host']}]:{cfg['port']}", flush=True)
    server.serve_forever()


def cmd_invite_create(cfg, args):
    if args.code and args.count != 1:
        sys.exit("--code can only be used with --count 1")
    db = open_db(cfg["db_path"])
    expires_at = int(time.time() + args.valid_days * 86400) if args.valid_days else 0
    for _ in range(args.count):
        raw = normalize_invite(args.code) if args.code else "".join(
            secrets.choice(INVITE_ALPHABET) for _ in range(10)
        )
        db.execute(
            "INSERT INTO invites VALUES(?,?,?,?,?,?,?)",
            (raw, args.uses, args.gb, args.days, expires_at, args.note,
             int(time.time())),
        )
        print(format_invite(raw))
    db.commit()


def cmd_invite_list(cfg, args):
    db = open_db(cfg["db_path"])
    now = int(time.time())
    for code, uses, gb, days, exp, note in db.execute(
        "SELECT code, uses_left, data_limit_gb, expire_days, expires_at, note "
        "FROM invites ORDER BY created_at"
    ):
        state = "expired" if exp and exp <= now else f"{uses} left"
        print(f"{format_invite(code)}  {state}  {gb}GB  {days}d  {note}")


def cmd_invite_del(cfg, args):
    db = open_db(cfg["db_path"])
    db.execute("DELETE FROM invites WHERE code=?", (normalize_invite(args.code),))
    db.commit()


def cmd_user_list(cfg, args):
    db = open_db(cfg["db_path"])
    for name, created in db.execute(
        "SELECT username, created_at FROM accounts ORDER BY created_at"
    ):
        print(f"{name}  {time.strftime('%Y-%m-%d', time.localtime(created))}")


def cmd_user_passwd(cfg, args):
    db = open_db(cfg["db_path"])
    password = args.password or getpass.getpass("New password: ")
    if not 8 <= len(password) <= 64:
        sys.exit("Password must be 8-64 characters.")
    cur = db.execute(
        "UPDATE accounts SET pw_hash=? WHERE username=?",
        (hash_password(password), args.username),
    )
    db.commit()
    if cur.rowcount == 0:
        sys.exit("No such account.")


def cmd_user_del(cfg, args):
    db = open_db(cfg["db_path"])
    db.execute("DELETE FROM accounts WHERE username=?", (args.username,))
    db.commit()
    if args.marzban:
        Marzban(cfg).delete_user(args.username)


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("serve").set_defaults(func=cmd_serve)
    c = sub.add_parser("invite-create")
    c.add_argument("--count", type=int, default=1)
    c.add_argument("--uses", type=int, default=1)
    c.add_argument("--gb", type=float, default=100)
    c.add_argument("--days", type=int, default=30)
    c.add_argument("--valid-days", type=int, default=0)
    c.add_argument("--note", default="")
    c.add_argument("--code", default="", help="custom invite code (with --count 1)")
    c.set_defaults(func=cmd_invite_create)
    sub.add_parser("invite-list").set_defaults(func=cmd_invite_list)
    d = sub.add_parser("invite-del")
    d.add_argument("code")
    d.set_defaults(func=cmd_invite_del)
    sub.add_parser("user-list").set_defaults(func=cmd_user_list)
    u = sub.add_parser("user-passwd")
    u.add_argument("username")
    u.add_argument("--password")
    u.set_defaults(func=cmd_user_passwd)
    x = sub.add_parser("user-del")
    x.add_argument("username")
    x.add_argument("--marzban", action="store_true")
    x.set_defaults(func=cmd_user_del)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(load_config(), args)


if __name__ == "__main__":
    main()
