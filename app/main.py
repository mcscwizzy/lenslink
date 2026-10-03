from __future__ import annotations

import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import load_settings
from .db import db, init_db, now_iso
from .media import cached_preview, list_images, safe_album_folder, safe_filename, safe_image_path, stream_zip
from .security import (
    ADMIN_COOKIE,
    PIN_COOKIE_PREFIX,
    SESSION_MAX_AGE,
    admin_network_allowed,
    create_admin_session,
    create_pin_session,
    has_pin_session,
    hash_pin,
    read_admin_session,
    verify_admin_password,
    verify_csrf,
    verify_pin,
)

settings = load_settings()
init_db(settings)

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="LensLink", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def public_base(request: Request) -> str:
    if settings.public_base_url:
        return settings.public_base_url
    return str(request.base_url).rstrip("/")


def require_admin_network(request: Request) -> None:
    if not admin_network_allowed(request, settings):
        raise HTTPException(status_code=404, detail="Not found")


def require_admin(request: Request):
    require_admin_network(request)
    session = read_admin_session(settings, request)
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")
    return session


def get_link(token: str):
    with db(settings) as conn:
        row = conn.execute(
            """
            SELECT s.*, a.name AS album_name, a.folder AS album_folder
            FROM share_links s JOIN albums a ON a.id = s.album_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
    return row


def link_status(row) -> str:
    if not row:
        return "missing"
    if row["revoked"]:
        return "revoked"
    if row["expires_at"]:
        expires = datetime.fromisoformat(row["expires_at"])
        if expires <= datetime.now(UTC):
            return "expired"
    return "active"


def require_active_link(request: Request, token: str, require_pin: bool = True):
    row = get_link(token)
    status = link_status(row)
    if status != "active":
        raise HTTPException(status_code=404, detail="Gallery not found or no longer available")
    if require_pin and row["pin_hash"] and not has_pin_session(settings, request, row["id"]):
        raise HTTPException(status_code=403, detail="PIN required")
    return row


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/g/"):
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    return response


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {})


@app.get("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")


@app.get("/api/health")
def health():
    return {"status": "healthy"}


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, error: str = ""):
    require_admin_network(request)
    if read_admin_session(settings, request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "admin_login.html", {"error": error})


@app.post("/admin/login")
def admin_login(request: Request, username: str = Form(...), password: str = Form(...)):
    require_admin_network(request)
    if not verify_admin_password(settings, username, password):
        return templates.TemplateResponse(
            request, "admin_login.html", {"error": "Invalid username or password."}, status_code=401
        )
    session = create_admin_session(settings, username)
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        ADMIN_COOKIE,
        session,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/admin",
    )
    return response


@app.post("/admin/logout")
def admin_logout(request: Request, csrf: str = Form(...)):
    session = require_admin(request)
    verify_csrf(session, csrf)
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE, path="/admin")
    return response


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    session = require_admin(request)
    with db(settings) as conn:
        albums = conn.execute(
            """
            SELECT a.*,
                   COUNT(DISTINCT s.id) AS link_count,
                   SUM(CASE WHEN s.revoked = 0 AND (s.expires_at IS NULL OR s.expires_at > ?) THEN 1 ELSE 0 END) AS active_links
            FROM albums a LEFT JOIN share_links s ON s.album_id = a.id
            GROUP BY a.id ORDER BY a.created_at DESC
            """,
            (now_iso(),),
        ).fetchall()
    enriched = []
    for album in albums:
        try:
            photo_count = len(list_images(safe_album_folder(settings, album["folder"])))
        except Exception:
            photo_count = None
        enriched.append({**dict(album), "photo_count": photo_count})
    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {"albums": enriched, "csrf": session["csrf"], "photo_root": str(settings.photo_root)},
    )


@app.post("/admin/albums")
def create_album(
    request: Request,
    name: str = Form(...),
    folder: str = Form(...),
    csrf: str = Form(...),
):
    session = require_admin(request)
    verify_csrf(session, csrf)
    name = name.strip()
    folder = folder.strip().strip("/")
    if not name:
        raise HTTPException(status_code=400, detail="Album name is required")
    try:
        safe_album_folder(settings, folder)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        with db(settings) as conn:
            cur = conn.execute(
                "INSERT INTO albums(name, folder, created_at) VALUES (?, ?, ?)",
                (name, folder, now_iso()),
            )
            album_id = cur.lastrowid
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(status_code=409, detail="That folder is already an album") from exc
        raise
    return RedirectResponse(f"/admin/albums/{album_id}", status_code=303)


@app.get("/admin/albums/{album_id}", response_class=HTMLResponse)
def admin_album(request: Request, album_id: int):
    session = require_admin(request)
    with db(settings) as conn:
        album = conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
        if not album:
            raise HTTPException(status_code=404)
        links = conn.execute(
            "SELECT * FROM share_links WHERE album_id = ? ORDER BY created_at DESC", (album_id,)
        ).fetchall()
    try:
        photo_count = len(list_images(safe_album_folder(settings, album["folder"])))
    except Exception:
        photo_count = None
    now = datetime.now(UTC)
    link_rows = []
    for link in links:
        item = dict(link)
        item["status"] = link_status(link)
        item["url"] = f"{public_base(request)}/g/{link['token']}"
        link_rows.append(item)
    return templates.TemplateResponse(
        request,
        "admin_album.html",
        {
            "album": dict(album),
            "links": link_rows,
            "photo_count": photo_count,
            "csrf": session["csrf"],
            "now": now,
        },
    )


@app.post("/admin/albums/{album_id}/links")
def create_share_link(
    request: Request,
    album_id: int,
    expires_days: int = Form(14),
    allow_individual_download: str | None = Form(None),
    allow_download_all: str | None = Form(None),
    pin: str = Form(""),
    csrf: str = Form(...),
):
    session = require_admin(request)
    verify_csrf(session, csrf)
    if expires_days not in {1, 3, 7, 14, 30, 90, 365}:
        raise HTTPException(status_code=400, detail="Invalid expiration")
    with db(settings) as conn:
        if not conn.execute("SELECT 1 FROM albums WHERE id = ?", (album_id,)).fetchone():
            raise HTTPException(status_code=404)
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(UTC) + timedelta(days=expires_days)).replace(microsecond=0).isoformat()
        conn.execute(
            """
            INSERT INTO share_links(
                album_id, token, expires_at, allow_individual_download,
                allow_download_all, pin_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                album_id,
                token,
                expires_at,
                1 if allow_individual_download else 0,
                1 if allow_download_all else 0,
                hash_pin(pin) if pin.strip() else None,
                now_iso(),
            ),
        )
    return RedirectResponse(f"/admin/albums/{album_id}", status_code=303)


@app.post("/admin/links/{link_id}/revoke")
def revoke_link(request: Request, link_id: int, csrf: str = Form(...)):
    session = require_admin(request)
    verify_csrf(session, csrf)
    with db(settings) as conn:
        row = conn.execute("SELECT album_id FROM share_links WHERE id = ?", (link_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        conn.execute("UPDATE share_links SET revoked = 1 WHERE id = ?", (link_id,))
    return RedirectResponse(f"/admin/albums/{row['album_id']}", status_code=303)


@app.post("/admin/links/{link_id}/extend")
def extend_link(request: Request, link_id: int, days: int = Form(14), csrf: str = Form(...)):
    session = require_admin(request)
    verify_csrf(session, csrf)
    if days not in {7, 14, 30, 90}:
        raise HTTPException(status_code=400, detail="Invalid extension")
    with db(settings) as conn:
        row = conn.execute("SELECT album_id, expires_at FROM share_links WHERE id = ?", (link_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        current = datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else datetime.now(UTC)
        base = max(current, datetime.now(UTC))
        new_exp = (base + timedelta(days=days)).replace(microsecond=0).isoformat()
        conn.execute("UPDATE share_links SET expires_at = ?, revoked = 0 WHERE id = ?", (new_exp, link_id))
    return RedirectResponse(f"/admin/albums/{row['album_id']}", status_code=303)


@app.post("/admin/albums/{album_id}/delete")
def delete_album(request: Request, album_id: int, csrf: str = Form(...)):
    session = require_admin(request)
    verify_csrf(session, csrf)
    with db(settings) as conn:
        conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    shutil.rmtree(settings.cache_dir / str(album_id), ignore_errors=True)
    return RedirectResponse("/admin", status_code=303)


@app.get("/g/{token}", response_class=HTMLResponse)
def gallery(request: Request, token: str):
    row = get_link(token)
    status = link_status(row)
    if status != "active":
        return templates.TemplateResponse(request, "unavailable.html", {}, status_code=404)
    if row["pin_hash"] and not has_pin_session(settings, request, row["id"]):
        return templates.TemplateResponse(
            request, "pin.html", {"token": token, "album_name": row["album_name"], "error": ""}
        )
    album_dir = safe_album_folder(settings, row["album_folder"])
    images = list_images(album_dir)
    with db(settings) as conn:
        conn.execute(
            "UPDATE share_links SET last_accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
            (now_iso(), row["id"]),
        )
    items = [
        {
            "name": Path(relative).name,
            "relative": relative,
            "encoded": quote(relative, safe="/"),
        }
        for relative in images
    ]
    return templates.TemplateResponse(
        request,
        "gallery.html",
        {
            "album_name": row["album_name"],
            "token": token,
            "images": items,
            "allow_individual_download": bool(row["allow_individual_download"]),
            "allow_download_all": bool(row["allow_download_all"]),
        },
    )


@app.post("/g/{token}/unlock")
def unlock_gallery(request: Request, token: str, pin: str = Form(...)):
    row = get_link(token)
    if link_status(row) != "active":
        return templates.TemplateResponse(request, "unavailable.html", {}, status_code=404)
    if not verify_pin(pin, row["pin_hash"]):
        return templates.TemplateResponse(
            request,
            "pin.html",
            {"token": token, "album_name": row["album_name"], "error": "That PIN is not correct."},
            status_code=401,
        )
    response = RedirectResponse(f"/g/{token}", status_code=303)
    response.set_cookie(
        f"{PIN_COOKIE_PREFIX}{row['id']}",
        create_pin_session(settings, row["id"]),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=f"/g/{token}",
    )
    return response


@app.get("/g/{token}/preview/{image_path:path}")
def preview(request: Request, token: str, image_path: str, w: int = 640):
    row = require_active_link(request, token)
    album_dir = safe_album_folder(settings, row["album_folder"])
    try:
        source = safe_image_path(album_dir, image_path)
        preview_path = cached_preview(settings, row["album_id"], source, image_path, w)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404)
    return FileResponse(preview_path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/g/{token}/original/{image_path:path}")
def original(request: Request, token: str, image_path: str):
    row = require_active_link(request, token)
    album_dir = safe_album_folder(settings, row["album_folder"])
    try:
        source = safe_image_path(album_dir, image_path)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404)
    return FileResponse(source, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/g/{token}/download/{image_path:path}")
def download_image(request: Request, token: str, image_path: str):
    row = require_active_link(request, token)
    if not row["allow_individual_download"]:
        raise HTTPException(status_code=403)
    album_dir = safe_album_folder(settings, row["album_folder"])
    try:
        source = safe_image_path(album_dir, image_path)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404)
    return FileResponse(source, filename=source.name)


@app.get("/g/{token}/download-all")
def download_all(request: Request, token: str):
    row = require_active_link(request, token)
    if not row["allow_download_all"]:
        raise HTTPException(status_code=403)
    album_dir = safe_album_folder(settings, row["album_folder"])
    images = list_images(album_dir)
    filename = f"{safe_filename(row['album_name'])}.zip"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"}
    return StreamingResponse(stream_zip(album_dir, images), media_type="application/zip", headers=headers)
