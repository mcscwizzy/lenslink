import os
from pathlib import Path

# Environment must be configured before app import.
TEST_ROOT = Path('/tmp/lenslink-test')
import shutil
shutil.rmtree(TEST_ROOT, ignore_errors=True)
(TEST_ROOT / 'data').mkdir(parents=True, exist_ok=True)
(TEST_ROOT / 'photos' / 'Demo').mkdir(parents=True, exist_ok=True)
os.environ['LENSLINK_DATA_DIR'] = str(TEST_ROOT / 'data')
os.environ['LENSLINK_PHOTO_ROOT'] = str(TEST_ROOT / 'photos')
os.environ['LENSLINK_ADMIN_USERNAME'] = 'admin'
os.environ['LENSLINK_ADMIN_PASSWORD'] = 'test-pass'
os.environ['LENSLINK_SESSION_SECRET'] = 'test-secret-that-is-long-enough-for-tests'
os.environ['LENSLINK_COOKIE_SECURE'] = 'false'
os.environ['LENSLINK_TRUST_PROXY_HEADERS'] = 'true'

from PIL import Image
from fastapi.testclient import TestClient
from app.main import app

Image.new('RGB', (800, 600), 'white').save(TEST_ROOT / 'photos' / 'Demo' / 'one.jpg')

client = TestClient(app, client=('127.0.0.1', 5555))


def test_health():
    r = client.get('/api/health')
    assert r.status_code == 200
    assert r.json()['status'] == 'healthy'


def test_admin_album_share_gallery_and_zip():
    r = client.post('/admin/login', data={'username':'admin','password':'test-pass'}, follow_redirects=False)
    assert r.status_code == 303
    client.cookies.update(r.cookies)

    dashboard = client.get('/admin')
    assert dashboard.status_code == 200
    import re
    csrf = re.search(r'name="csrf" value="([^"]+)"', dashboard.text).group(1)

    r = client.post('/admin/albums', data={'name':'Demo Album','folder':'Demo','csrf':csrf}, follow_redirects=False)
    assert r.status_code == 303
    album_url = r.headers['location']
    album_page = client.get(album_url)
    assert album_page.status_code == 200
    csrf = re.search(r'name="csrf" value="([^"]+)"', album_page.text).group(1)

    r = client.post(album_url + '/links', data={
        'expires_days':'14', 'allow_individual_download':'on', 'allow_download_all':'on', 'pin':'', 'csrf':csrf
    }, follow_redirects=False)
    assert r.status_code == 303
    album_page = client.get(album_url)
    match = re.search(r'value="(http://testserver/g/[^\"]+)"', album_page.text)
    assert match
    gallery_url = match.group(1).replace('http://testserver','')

    gallery = client.get(gallery_url)
    assert gallery.status_code == 200
    assert 'Demo Album' in gallery.text
    assert 'one.jpg' in gallery.text

    token = gallery_url.rsplit('/',1)[-1]
    preview = client.get(f'/g/{token}/preview/one.jpg?w=400')
    assert preview.status_code == 200
    assert preview.headers['content-type'].startswith('image/webp')

    archive = client.get(f'/g/{token}/download-all')
    assert archive.status_code == 200
    assert archive.content[:2] == b'PK'


def test_admin_is_hidden_from_public_ips_and_spoofed_headers():
    public = TestClient(app, client=('203.0.113.50', 5555))
    assert public.get('/admin/login').status_code == 404
    assert public.get('/admin/login', headers={'x-forwarded-for':'192.168.1.50','x-real-ip':'192.168.1.50'}).status_code == 404

    # A trusted/private reverse proxy reporting a public client must also be denied.
    proxy = TestClient(app, client=('172.20.0.5', 5555))
    assert proxy.get('/admin/login', headers={'x-real-ip':'203.0.113.50'}).status_code == 404
