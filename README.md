# LensLink

**Private photo galleries, shared simply.**

LensLink is a deliberately small, self-hosted client gallery for photographers. It does not ingest, organize, rename, edit, or delete your photo library. It points at existing folders on your NAS and creates private, expiring gallery links.

## v1 features

- Docker-first deployment
- SQLite database in `/data`
- Existing photo library mounted read-only at `/photos`
- Admin UI restricted to private CIDRs and protected by login
- Albums point at existing folders under `/photos`
- 256-bit-ish random bearer links (`secrets.token_urlsafe(32)`)
- Expiring/revocable links
- Optional gallery PIN
- Responsive masonry-style gallery
- Full-screen lightbox with keyboard arrows and mobile swipe
- Individual photo download toggle
- Download All ZIP toggle
- ZIPs are streamed; LensLink does not create a second full-size ZIP on disk
- Cached WebP previews in `/data/cache`
- No public album index
- `robots.txt` blocks indexing and gallery responses send `X-Robots-Tag: noindex`

## Supported gallery files

v1 displays JPEG, PNG, WebP, GIF, and AVIF files. Photographer RAW files are intentionally out of scope; point LensLink at the exported client JPEG folder.

## Quick start

1. Edit `docker-compose.yml`.
2. Change these values before starting:
   - `LENSLINK_ADMIN_PASSWORD`
   - `LENSLINK_SESSION_SECRET`
   - `LENSLINK_PUBLIC_BASE_URL`
   - the two NAS volume paths
3. Generate a session secret, for example:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

4. Start it:

   ```bash
   docker compose up -d --build
   ```

5. On your LAN, open `http://NAS-IP:8080/admin` and sign in.

For local HTTP testing, set `LENSLINK_COOKIE_SECURE=false`. Use `true` behind HTTPS in production.

## NAS mounts

Example:

```yaml
volumes:
  - /volume1/Photography/ClientExports:/photos:ro
  - /volume1/docker/lenslink:/data
```

The `:ro` matters: LensLink can read originals but cannot modify or delete them.

If your NAS enforces host UID permissions, the container runs as UID `10001`. Ensure that UID can read the photo tree and write the LensLink data folder, or adjust the Dockerfile/user mapping for your NAS.

## Reverse proxy: block `/admin` publicly

LensLink performs its own CIDR check, but your reverse proxy should also be the outer security boundary.

### Nginx example

```nginx
server {
    listen 443 ssl http2;
    server_name photos.example.com;

    # Public galleries
    location / {
        proxy_pass http://lenslink:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Admin is LAN/VPN only
    location /admin {
        allow 10.0.0.0/8;
        allow 172.16.0.0/12;
        allow 192.168.0.0/16;
        deny all;

        proxy_pass http://lenslink:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

If you use Tailscale, add your tailnet range (normally `100.64.0.0/10`) to both the proxy allow-list and `LENSLINK_ADMIN_ALLOWED_CIDRS`.

## Important proxy/IP note

LensLink reads forwarded client IP headers only when the immediate peer is inside `LENSLINK_TRUSTED_PROXY_CIDRS`. Configure the proxy to overwrite `X-Real-IP` / `X-Forwarded-For` with `$remote_addr`, as shown above, rather than trusting a header supplied by the browser. Keep port 8080 off the public internet; expose LensLink through your reverse proxy instead.

## Album workflow

1. Export a finished client album to a folder under the photo mount, e.g. `/photos/Weddings/Smith-2026`.
2. In `/admin`, create album `Smith Wedding` with folder `Weddings/Smith-2026`.
3. Create a share link and choose expiration, PIN, individual-download permission, and ZIP permission.
4. Copy the private link to the client.
5. Revoke or extend it later from the album admin page.

## Data layout

```text
/photos                       read-only originals
/data/lenslink.db             SQLite metadata
/data/cache/<album-id>/...    generated WebP previews
```

Deleting an album in LensLink removes LensLink metadata and its generated preview cache. It does **not** delete the originals under `/photos`.

## Design boundary

LensLink v1 intentionally does not include client accounts, favorites, proofing, comments, uploads, editing, AI tagging, email delivery, or cloud storage adapters.

> Upload elsewhere. Organize elsewhere. LensLink only shares beautifully.
