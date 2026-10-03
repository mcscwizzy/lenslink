from __future__ import annotations

import hashlib
import io
import os
import re
import zipfile
from pathlib import Path
from typing import Iterator

from PIL import Image, ImageOps

from .config import Settings

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}


def safe_album_folder(settings: Settings, folder: str) -> Path:
    if not folder or Path(folder).is_absolute():
        raise ValueError("Album folder must be a relative path inside the photo root")
    root = settings.photo_root.resolve()
    candidate = (root / folder).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("Album folder escapes the photo root")
    if not candidate.is_dir():
        raise ValueError("Album folder does not exist")
    return candidate


def list_images(album_dir: Path) -> list[str]:
    files: list[str] = []
    for path in album_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            files.append(path.relative_to(album_dir).as_posix())
    files.sort(key=lambda x: x.lower())
    return files


def safe_image_path(album_dir: Path, relative: str) -> Path:
    candidate = (album_dir / relative).resolve()
    if album_dir.resolve() not in candidate.parents:
        raise ValueError("Invalid image path")
    if not candidate.is_file() or candidate.suffix.lower() not in IMAGE_EXTENSIONS:
        raise FileNotFoundError(relative)
    return candidate


def cached_preview(settings: Settings, album_id: int, source: Path, relative: str, width: int) -> Path:
    width = max(320, min(width, 2400))
    stat = source.stat()
    key = hashlib.sha256(
        f"{relative}|{stat.st_mtime_ns}|{stat.st_size}|{width}".encode()
    ).hexdigest()
    target_dir = settings.cache_dir / str(album_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{key}.webp"
    if target.exists():
        return target

    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        if image.mode == "RGBA":
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        if image.width > width:
            ratio = width / image.width
            image = image.resize((width, max(1, int(image.height * ratio))), Image.Resampling.LANCZOS)
        tmp = target.with_suffix(".tmp")
        image.save(tmp, format="WEBP", quality=84, method=5)
        os.replace(tmp, target)
    return target


def safe_filename(name: str, fallback: str = "gallery") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-._")
    return cleaned or fallback


class _ZipSink(io.RawIOBase):
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.position = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def write(self, b: bytes | bytearray) -> int:
        data = bytes(b)
        self.buffer.extend(data)
        self.position += len(data)
        return len(data)

    def tell(self) -> int:
        return self.position

    def flush(self) -> None:
        return None

    def drain(self) -> bytes:
        data = bytes(self.buffer)
        self.buffer.clear()
        return data


def stream_zip(album_dir: Path, relative_files: list[str]) -> Iterator[bytes]:
    sink = _ZipSink()
    with zipfile.ZipFile(
        sink, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True
    ) as archive:
        for relative in relative_files:
            source = safe_image_path(album_dir, relative)
            with source.open("rb") as src, archive.open(relative, "w", force_zip64=True) as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
                    if len(sink.buffer) >= 256 * 1024:
                        yield sink.drain()
            if sink.buffer:
                yield sink.drain()
    if sink.buffer:
        yield sink.drain()
