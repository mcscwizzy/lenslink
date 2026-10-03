from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    photo_root: Path
    admin_username: str
    admin_password: str
    session_secret: str
    cookie_secure: bool
    public_base_url: str
    admin_allowed_cidrs: tuple[str, ...]
    trusted_proxy_cidrs: tuple[str, ...]
    trust_proxy_headers: bool

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lenslink.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


def load_settings() -> Settings:
    data_dir = Path(os.getenv("LENSLINK_DATA_DIR", "/data")).resolve()
    photo_root = Path(os.getenv("LENSLINK_PHOTO_ROOT", "/photos")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "cache").mkdir(parents=True, exist_ok=True)

    secret = os.getenv("LENSLINK_SESSION_SECRET")
    if not secret:
        secret = secrets.token_urlsafe(48)
        print("WARNING: LENSLINK_SESSION_SECRET is unset; sessions will reset on restart.")

    allowed = tuple(
        x.strip()
        for x in os.getenv(
            "LENSLINK_ADMIN_ALLOWED_CIDRS",
            "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
        ).split(",")
        if x.strip()
    )
    proxies = tuple(
        x.strip()
        for x in os.getenv(
            "LENSLINK_TRUSTED_PROXY_CIDRS",
            "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
        ).split(",")
        if x.strip()
    )

    return Settings(
        data_dir=data_dir,
        photo_root=photo_root,
        admin_username=os.getenv("LENSLINK_ADMIN_USERNAME", "admin"),
        admin_password=os.getenv("LENSLINK_ADMIN_PASSWORD", ""),
        session_secret=secret,
        cookie_secure=_bool("LENSLINK_COOKIE_SECURE", True),
        public_base_url=os.getenv("LENSLINK_PUBLIC_BASE_URL", "").rstrip("/"),
        admin_allowed_cidrs=allowed,
        trusted_proxy_cidrs=proxies,
        trust_proxy_headers=_bool("LENSLINK_TRUST_PROXY_HEADERS", True),
    )
