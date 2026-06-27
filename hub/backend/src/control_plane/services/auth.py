from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request

from control_plane.config import Settings
from control_plane.models import AuthSessionConnectRequest, AuthSessionView

SESSION_COOKIE_NAME = "hub_session"


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


@dataclass
class AuthSessionService:
    secret: str
    ttl_seconds: int

    def _sign(self, payload_text: str) -> str:
        digest = hmac.new(
            self.secret.encode("utf-8"),
            payload_text.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return _b64_encode(digest)

    def encode_session(self, session: AuthSessionView) -> str:
        payload_text = json.dumps(session.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
        payload_token = _b64_encode(payload_text.encode("utf-8"))
        signature = self._sign(payload_text)
        return f"{payload_token}.{signature}"

    def decode_session(self, token: str | None) -> AuthSessionView | None:
        if not token or "." not in token:
            return None
        payload_token, signature = token.rsplit(".", 1)
        try:
            payload_text = _b64_decode(payload_token).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        expected_signature = self._sign(payload_text)
        if not hmac.compare_digest(signature, expected_signature):
            return None
        try:
            payload = json.loads(payload_text)
            session = AuthSessionView.model_validate(payload)
        except (json.JSONDecodeError, ValueError):
            return None
        if self.is_expired(session):
            return None
        return session

    def create_session(self, payload: AuthSessionConnectRequest) -> AuthSessionView:
        issued_at = _now_utc()
        expires_at = issued_at + timedelta(seconds=self.ttl_seconds)
        return AuthSessionView(
            wallet_id=payload.wallet_id,
            chain_id=payload.chain_id,
            auth_type="wallet_plugin",
            issued_at=issued_at.isoformat(),
            expires_at=expires_at.isoformat(),
            ucan_session=payload.ucan_session,
            ucan_signature=payload.ucan_signature,
        )

    def is_expired(self, session: AuthSessionView) -> bool:
        try:
            expires_at = datetime.fromisoformat(session.expires_at)
        except ValueError:
            return True
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= _now_utc()

    def current_session(self, request: Request) -> AuthSessionView | None:
        return self.decode_session(request.cookies.get(SESSION_COOKIE_NAME))


def auth_service(settings: Settings) -> AuthSessionService:
    return AuthSessionService(
        secret=settings.session_secret,
        ttl_seconds=settings.session_ttl_seconds,
    )
