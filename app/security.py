from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from typing import Any

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings

ADMIN_COOKIE = "lenslink_admin"
PIN_COOKIE_PREFIX = "lenslink_pin_"
SESSION_MAX_AGE = 60 * 60 * 12


def _serializer(settings: Settings, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt=salt)


def create_admin_session(settings: Settings, username: str) -> str:
    return _serializer(settings, "admin-session").dumps(
        {"u": username, "csrf": secrets.token_urlsafe(24)}
    )


def read_admin_session(settings: Settings, request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(ADMIN_COOKIE)
    if not token:
        return None
    try:
        data = _serializer(settings, "admin-session").loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if data.get("u") != settings.admin_username:
        return None
    return data


def verify_admin_password(settings: Settings, username: str, password: str) -> bool:
    if not settings.admin_password:
        return False
    return hmac.compare_digest(username, settings.admin_username) and hmac.compare_digest(
        password, settings.admin_password
    )


def verify_csrf(session: dict[str, Any], supplied: str) -> None:
    expected = str(session.get("csrf", ""))
    if not expected or not hmac.compare_digest(expected, supplied or ""):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, encoded: str | None) -> bool:
    if not encoded:
        return True
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", pin.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def create_pin_session(settings: Settings, link_id: int) -> str:
    return _serializer(settings, f"pin-{link_id}").dumps({"link": link_id})


def has_pin_session(settings: Settings, request: Request, link_id: int) -> bool:
    token = request.cookies.get(f"{PIN_COOKIE_PREFIX}{link_id}")
    if not token:
        return False
    try:
        data = _serializer(settings, f"pin-{link_id}").loads(token, max_age=SESSION_MAX_AGE)
        return int(data.get("link", -1)) == link_id
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return False


def _in_cidrs(ip: str, cidrs: tuple[str, ...]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in ipaddress.ip_network(cidr, strict=False) for cidr in cidrs)
    except ValueError:
        return False


def client_ip(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else ""
    if (
        settings.trust_proxy_headers
        and peer
        and _in_cidrs(peer, settings.trusted_proxy_cidrs)
    ):
        # Prefer X-Real-IP because a correctly configured reverse proxy overwrites it
        # with the actual connecting client address instead of appending user input.
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip and _in_cidrs(real_ip, ("0.0.0.0/0", "::/0")):
            return real_ip

        # For X-Forwarded-For, walk from right to left and take the first address
        # that is not itself a trusted proxy. This avoids trusting a spoofed
        # left-most value when a proxy appends to an existing header.
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            chain = [x.strip() for x in forwarded.split(",") if x.strip()]
            for candidate in reversed(chain):
                if not _in_cidrs(candidate, settings.trusted_proxy_cidrs):
                    return candidate
            if chain:
                return chain[0]
    return peer


def admin_network_allowed(request: Request, settings: Settings) -> bool:
    return _in_cidrs(client_ip(request, settings), settings.admin_allowed_cidrs)
