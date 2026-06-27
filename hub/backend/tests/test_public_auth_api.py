from __future__ import annotations

import http.cookiejar
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path

import uvicorn
from eth_account import Account
from eth_account.messages import encode_defunct

from hub.app import create_app
from hub.config import Settings


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class PublicAuthApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = None
        self.server = None
        self.thread = None
        self.jar = None
        self.opener = None

    def start_server(self, secure_mode: str) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.settings = Settings(
            HUB_REPO_ROOT=str(Path.cwd()),
            HUB_RUNTIME_DIR=str(Path(self.tmpdir.name) / "runtime"),
            HUB_INSTANCES_ROOT=str(Path(self.tmpdir.name) / "instances"),
            HUB_BIND_ADDR=f"127.0.0.1:{self.port}",
            HUB_PUBLIC_BASE_URL="https://hub.example.com",
            HUB_SESSION_COOKIE_SECURE_MODE=secure_mode,
        )
        self.app = create_app(self.settings)
        self.config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self._wait_until_ready()
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def tearDown(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.tmpdir is not None:
            self.tmpdir.cleanup()

    def _wait_until_ready(self) -> None:
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base}/api/v1/public/health", timeout=0.5):
                    return
            except Exception:  # noqa: BLE001
                time.sleep(0.05)
        raise RuntimeError("server did not start in time")

    def request(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict | str | None, list[tuple[str, str]]]:
        data = None
        request_headers = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=request_headers, method=method)
        try:
            with self.opener.open(req, timeout=5) as response:
                body = response.read().decode("utf-8")
                parsed = json.loads(body) if body else None
                return response.status, parsed, list(response.headers.items())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = body
            return exc.code, parsed, list(exc.headers.items())

    def login(self, account: Account, secure_mode: str = "never") -> dict[str, str]:
        if self.server is None:
            self.start_server(secure_mode)
        status, challenge_payload, _ = self.request(
            "/api/v1/public/auth/wallet/challenge",
            method="POST",
            payload={"wallet_id": account.address, "chain_id": "1"},
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "hub.example.com"},
        )
        self.assertEqual(status, 200)
        assert isinstance(challenge_payload, dict)
        signed = Account.sign_message(
            encode_defunct(text=challenge_payload["challenge"]),
            private_key=account.key,
        )
        status, _, response_headers = self.request(
            "/api/v1/public/auth/wallet/verify",
            method="POST",
            payload={
                "wallet_id": account.address,
                "chain_id": "1",
                "challenge": challenge_payload["challenge"],
                "challenge_token": challenge_payload["challenge_token"],
                "signature": signed.signature.to_0x_hex(),
            },
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "hub.example.com"},
        )
        self.assertEqual(status, 200)
        cookie_headers = [value for key, value in response_headers if key.lower() == "set-cookie"]
        self.assertTrue(cookie_headers)
        cookie = SimpleCookie()
        cookie.load(cookie_headers[0])
        session_value = cookie["hub_session"].value
        return {"Cookie": f"hub_session={session_value}"}

    def test_auth_gate_login_and_logout(self) -> None:
        self.start_server("never")
        status, _, _ = self.request("/api/v1/public/robots")
        self.assertEqual(status, 401)
        status, _, _ = self.request("/api/v1/public/robots/trader/summary")
        self.assertEqual(status, 401)

        account = Account.create()
        auth_headers = self.login(account)

        status, _, _ = self.request("/api/v1/public/auth/me", headers=auth_headers)
        self.assertEqual(status, 200)
        status, _, _ = self.request("/api/v1/public/robots", headers=auth_headers)
        self.assertEqual(status, 200)

        status, _, _ = self.request("/api/v1/public/auth/logout", method="POST", headers=auth_headers)
        self.assertEqual(status, 200)
        status, _, _ = self.request("/api/v1/public/auth/me")
        self.assertEqual(status, 401)
        status, _, _ = self.request("/api/v1/public/robots")
        self.assertEqual(status, 401)

    def test_secure_cookie_header_when_enabled(self) -> None:
        self.start_server("always")
        account = Account.create()
        status, challenge_payload, _ = self.request(
            "/api/v1/public/auth/wallet/challenge",
            method="POST",
            payload={"wallet_id": account.address, "chain_id": "1"},
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "hub.example.com"},
        )
        self.assertEqual(status, 200)
        assert isinstance(challenge_payload, dict)

        signed = Account.sign_message(
            encode_defunct(text=challenge_payload["challenge"]),
            private_key=account.key,
        )
        status, _, response_headers = self.request(
            "/api/v1/public/auth/wallet/verify",
            method="POST",
            payload={
                "wallet_id": account.address,
                "chain_id": "1",
                "challenge": challenge_payload["challenge"],
                "challenge_token": challenge_payload["challenge_token"],
                "signature": signed.signature.to_0x_hex(),
            },
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "hub.example.com"},
        )
        self.assertEqual(status, 200)
        cookie_headers = [value for key, value in response_headers if key.lower() == "set-cookie"]
        self.assertTrue(cookie_headers)
        self.assertTrue(any("Secure" in value for value in cookie_headers))

    def test_messenger_workspace_summary_and_unsupported_actions(self) -> None:
        self.start_server("never")
        wallet = Account.create()
        auth_headers = self.login(wallet)

        status, summary, _ = self.request("/api/v1/public/robots/messenger/summary", headers=auth_headers)
        self.assertEqual(status, 200)
        assert isinstance(summary, dict)
        self.assertEqual(summary["broker"], "multi-channel")
        self.assertIn("instanceCount", summary["state"])

        status, detail, _ = self.request(
            "/api/v1/public/robots/messenger/actions/start",
            method="POST",
            headers=auth_headers,
        )
        self.assertEqual(status, 405)
        assert isinstance(detail, dict)
        self.assertIn("does not support direct actions", detail["detail"])

    def test_instance_owner_isolation(self) -> None:
        self.start_server("never")
        owner = Account.create()
        other = Account.create()
        owner_headers = self.login(owner)
        other_headers = self.login(other)

        status, created, _ = self.request(
            "/api/v1/public/robot/instances",
            method="POST",
            payload={"kind": "whatsapp", "name": "Owner Bot"},
            headers=owner_headers,
        )
        self.assertEqual(status, 200)
        assert isinstance(created, dict)
        instance_id = created["id"]

        status, listing, _ = self.request("/api/v1/public/robot/instances", headers=owner_headers)
        self.assertEqual(status, 200)
        assert isinstance(listing, dict)
        self.assertEqual(len(listing["items"]), 1)
        self.assertEqual(listing["items"][0]["id"], instance_id)

        status, listing_other, _ = self.request("/api/v1/public/robot/instances", headers=other_headers)
        self.assertEqual(status, 200)
        assert isinstance(listing_other, dict)
        self.assertEqual(listing_other["items"], [])

        status, _, _ = self.request(f"/api/v1/public/robot/instances/{instance_id}", headers=owner_headers)
        self.assertEqual(status, 200)
        status, other_detail, _ = self.request(f"/api/v1/public/robot/instances/{instance_id}", headers=other_headers)
        self.assertEqual(status, 404)
        assert isinstance(other_detail, dict)
        self.assertEqual(other_detail["detail"], "instance not found")

        status, _, _ = self.request(
            f"/api/v1/public/robot/instances/{instance_id}",
            method="DELETE",
            headers=other_headers,
        )
        self.assertEqual(status, 404)

        status, deleted, _ = self.request(
            f"/api/v1/public/robot/instances/{instance_id}",
            method="DELETE",
            headers=owner_headers,
        )
        self.assertEqual(status, 200)
        assert isinstance(deleted, dict)
        self.assertEqual(deleted["id"], instance_id)


if __name__ == "__main__":
    unittest.main()
