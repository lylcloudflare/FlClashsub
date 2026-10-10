import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import contextlib
import io
import sqlite3
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer

import auth_server


class FakeMarzban:
    def __init__(self):
        self.users = {}
        self.created = []
        self.fail_create = False
        self.forbidden = set()
        self.inbounds = {"vless": [{}], "trojan": [{}], "vmess": [{}],
                         "shadowsocks": [{}]}
        self.inbounds_status = 200
        self.token_calls = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, status, payload):
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n)
                if self.path == "/api/admin/token":
                    outer.token_calls += 1
                    return self._json(200, {"access_token": "tok"})
                if self.headers.get("Authorization") != "Bearer tok":
                    return self._json(401, {"detail": "no"})
                if self.path == "/api/user":
                    if outer.fail_create:
                        return self._json(500, {"detail": "boom"})
                    data = json.loads(body)
                    for proto in data["proxies"]:
                        if not outer.inbounds.get(proto):
                            return self._json(
                                400,
                                {"detail": f"Protocol {proto} is disabled "
                                           "on your server"},
                            )
                    outer.created.append(data)
                    outer.users[data["username"]] = {
                        "subscription_url": "/sub/TOKEN_" + data["username"],
                        "status": "active",
                        "used_traffic": 5,
                        "data_limit": data["data_limit"],
                        "expire": data["expire"],
                    }
                    return self._json(200, outer.users[data["username"]])
                self._json(404, {})

            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer tok":
                    return self._json(401, {"detail": "no"})
                if self.path == "/api/inbounds":
                    if outer.inbounds_status != 200:
                        return self._json(outer.inbounds_status, {})
                    return self._json(200, outer.inbounds)
                name = self.path.rsplit("/", 1)[-1]
                if name in outer.forbidden:
                    return self._json(403, {"detail": "not yours"})
                if name in outer.users:
                    return self._json(200, outer.users[name])
                self._json(404, {"detail": "User not found"})

            def do_DELETE(self):
                name = self.path.rsplit("/", 1)[-1]
                outer.users.pop(name, None)
                self._json(200, {})

        self.server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class AuthServerTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMarzban()
        self.tmp = tempfile.mkdtemp()
        self.cfg = auth_server.load_config(
            {
                "MARZBAN_URL": self.fake.url,
                "MARZBAN_ADMIN_USER": "admin",
                "MARZBAN_ADMIN_PASS": "pw",
                "AUTH_DB": os.path.join(self.tmp, "d.db"),
                "AUTH_HOST": "127.0.0.1",
                "AUTH_PORT": "0",
                "SUB_URL_PREFIX": "https://panel.example.com:8000",
            }
        )
        self.service = auth_server.Service(self.cfg)
        self.server = auth_server.build_server(self.cfg, self.service)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.fake.stop()

    def post(self, path, body):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def invite(self, **kw):
        code = auth_server.new_invite_code()
        raw = auth_server.normalize_invite(code)
        self.service.db.execute(
            "INSERT INTO invites VALUES(?,?,?,?,?,?,?)",
            (raw, kw.get("uses", 1), kw.get("gb", 50), kw.get("days", 30),
             kw.get("expires_at", 0), "", 0),
        )
        self.service.db.commit()
        return code

    def register(self, code, name="alice", pw="password123"):
        return self.post(
            "/api/register", {"username": name, "password": pw, "invite": code}
        )
        
    def cli(self, *argv):
        """Run the flauth CLI against this test's database."""
        env = {"AUTH_DB": self.cfg["db_path"]}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            auth_server.main(list(argv))
        return out.getvalue().strip()
    
    def test_register_then_login(self):
        status, res = self.register(self.invite())
        self.assertEqual(status, 200)
        self.assertEqual(
            res["subscription_url"],
            "https://panel.example.com:8000/sub/TOKEN_alice",
        )
        self.assertEqual(self.fake.created[0]["data_limit"], 50 * 1024**3)
        self.assertEqual(
            self.fake.created[0]["proxies"]["vless"],
            {"flow": "xtls-rprx-vision"},
        )
        status, res = self.post(
            "/api/login", {"username": "alice", "password": "password123"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(res["used"], 5)
        self.assertEqual(res["status"], "active")

    def test_invite_is_single_use_and_case_insensitive(self):
        code = self.invite(uses=1)
        self.assertEqual(self.register(code.lower().replace("-", " "))[0], 200)
        status, res = self.register(code, name="bob")
        self.assertEqual((status, res["error"]), (403, "invalid_invite"))

    def test_expired_invite_rejected(self):
        code = self.invite(expires_at=1)
        self.assertEqual(self.register(code)[1]["error"], "invalid_invite")

    def test_unknown_invite_rejected(self):
        self.assertEqual(self.register("AAAAA-BBBBB")[0], 403)

    def test_duplicate_username_rejected(self):
        self.register(self.invite())
        status, res = self.register(self.invite(), pw="otherpass99")
        self.assertEqual((status, res["error"]), (409, "username_taken"))

    def test_existing_marzban_user_name_is_taken(self):
        self.fake.users["lr999"] = {"status": "active"}
        status, res = self.register(self.invite(), name="lr999")
        self.assertEqual((status, res["error"]), (409, "username_taken"))

    def test_name_owned_by_another_admin_is_taken(self):
        self.fake.forbidden = {"theirs"}
        status, res = self.register(self.invite(), name="theirs")
        self.assertEqual((status, res["error"]), (409, "username_taken"))

    def test_panel_down_gives_502_on_login(self):
        self.register(self.invite())
        self.fake.stop()
        status, res = self.post(
            "/api/login", {"username": "alice", "password": "password123"}
        )
        self.assertEqual((status, res["error"]), (502, "upstream_error"))
        self.fake = FakeMarzban()

    def test_only_protocols_with_an_inbound_are_enabled(self):
        self.fake.inbounds = {"vless": [{}], "shadowsocks": [{}]}
        status, _ = self.register(self.invite())
        self.assertEqual(status, 200)
        self.assertEqual(
            sorted(self.fake.created[0]["proxies"]), ["shadowsocks", "vless"]
        )

    def test_empty_inbound_list_counts_as_disabled(self):
        self.fake.inbounds = {"vless": [{}], "trojan": []}
        self.register(self.invite())
        self.assertEqual(list(self.fake.created[0]["proxies"]), ["vless"])

    def test_no_usable_protocol_fails_and_keeps_invite(self):
        self.fake.inbounds = {}
        code = self.invite(uses=1)
        status, res = self.register(code)
        self.assertEqual((status, res["error"]), (502, "upstream_error"))
        self.fake.inbounds = {"vless": [{}]}
        self.assertEqual(self.register(code)[0], 200)

    def test_inbounds_lookup_failure_falls_back_to_config(self):
        self.fake.inbounds_status = 404
        status, _ = self.register(self.invite())
        self.assertEqual(status, 200)
        self.assertEqual(
            sorted(self.fake.created[0]["proxies"]),
            ["shadowsocks", "trojan", "vless", "vmess"],
        )

    def test_validation(self):
        code = self.invite()
        self.assertEqual(self.register(code, name="a b")[1]["error"], "bad_username")
        self.assertEqual(self.register(code, pw="short")[1]["error"], "bad_password")

    def test_wrong_password_and_unknown_user_look_alike(self):
        self.register(self.invite())
        a = self.post("/api/login", {"username": "alice", "password": "nope12345"})
        b = self.post("/api/login", {"username": "ghost", "password": "nope12345"})
        self.assertEqual(a, b)
        self.assertEqual(a[0], 401)

    def test_account_locks_after_repeated_failures(self):
        self.register(self.invite())
        for _ in range(5):
            self.post("/api/login", {"username": "alice", "password": "bad-bad-1"})
        status, res = self.post(
            "/api/login", {"username": "alice", "password": "password123"}
        )
        self.assertEqual((status, res["error"]), (429, "too_many_attempts"))

    def test_failed_panel_call_restores_invite(self):
        code = self.invite(uses=1)
        self.fake.fail_create = True
        self.assertEqual(self.register(code)[0], 502)
        self.fake.fail_create = False
        self.assertEqual(self.register(code)[0], 200)
        row = self.service.db.execute("SELECT COUNT(*) FROM accounts").fetchone()
        self.assertEqual(row[0], 1)

    def test_change_password(self):
        self.register(self.invite())
        status, _ = self.post(
            "/api/password",
            {"username": "alice", "password": "password123",
             "new_password": "newpassword9"},
        )
        self.assertEqual(status, 200)
        ok = self.post(
            "/api/login", {"username": "alice", "password": "newpassword9"}
        )
        old = self.post(
            "/api/login", {"username": "alice", "password": "password123"}
        )
        self.assertEqual((ok[0], old[0]), (200, 401))

    def test_bad_json_and_unknown_path(self):
        req = urllib.request.Request(
            self.base + "/api/login", data=b"not json", method="POST",
            headers={"Content-Length": "8"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.post("/api/nope", {})[0], 404)

    def test_token_is_cached(self):
        self.register(self.invite())
        self.post("/api/login", {"username": "alice", "password": "password123"})
        self.assertEqual(self.fake.token_calls, 1)

    def test_cli_custom_invite_code(self):
        printed = self.cli(
            "invite-create", "--code", "xiao-wang 2026",
            "--uses", "1", "--gb", "20", "--days", "7",
        )
        self.assertEqual(printed, "XIAOWANG2026")
        # Any case / separators work when registering.
        status, _ = self.register("xiaowang-2026")
        self.assertEqual(status, 200)
        self.assertEqual(self.fake.created[0]["data_limit"], 20 * 1024**3)
        # Single use: the second registration is rejected.
        status, res = self.register("XIAOWANG2026", name="bob")
        self.assertEqual((status, res["error"]), (403, "invalid_invite"))

    def test_cli_duplicate_custom_code_fails(self):
        self.cli("invite-create", "--code", "SAMECODE88")
        with self.assertRaises(sqlite3.IntegrityError):
            self.cli("invite-create", "--code", "SAMECODE88")

    def test_cli_random_code_is_still_default(self):
        printed = self.cli("invite-create", "--count", "2").splitlines()
        self.assertEqual(len(printed), 2)
        self.assertNotEqual(printed[0], printed[1])
        for line in printed:
            self.assertRegex(line, r"^[A-Z2-9]{5}-[A-Z2-9]{5}$")
            
    def test_password_hash_roundtrip(self):
        stored = auth_server.hash_password("hello-world")
        self.assertTrue(auth_server.verify_password("hello-world", stored))
        self.assertFalse(auth_server.verify_password("hello-worlds", stored))
        self.assertFalse(auth_server.verify_password("x", "garbage"))


if __name__ == "__main__":
    unittest.main()
