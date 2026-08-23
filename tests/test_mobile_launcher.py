"""Tests for Kai Mobile Launcher — Sub-project 6.

Tests the PWA launcher routes: HTML page, manifest, service worker,
icons, tiles API, and status enrichment.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))


# Import the FastAPI app (reuse the test pattern from other test suites)
@pytest.fixture
def client():
    """Create a TestClient with just the mobile launcher routes."""
    from fastapi import FastAPI
    from core.mobile_launcher_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestLauncherPage:
    """The main launcher HTML page."""

    def test_get_launcher_returns_html(self, client):
        resp = client.get("/mobile")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_launcher_contains_key_elements(self, client):
        resp = client.get("/mobile")
        html = resp.text

        # Core structural elements
        assert "Kai Mobile" in html
        assert "Command Node" in html
        assert "tile-grid" in html
        assert '<html lang="en">' in html

    def test_launcher_has_manifest_link(self, client):
        resp = client.get("/mobile")
        html = resp.text

        assert 'rel="manifest"' in html
        assert '/mobile/manifest' in html

    def test_launcher_has_mobile_meta_tags(self, client):
        resp = client.get("/mobile")
        html = resp.text

        assert "viewport-fit=cover" in html
        assert "apple-mobile-web-app-capable" in html
        assert "theme-color" in html

    def test_trailing_slash_also_serves_launcher(self, client):
        resp = client.get("/mobile/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_inline_svg_icons_present(self, client):
        resp = client.get("/mobile")
        html = resp.text

        # Each icon should have its SVG inline
        assert 'brain-circuit' in html
        assert 'terminal' in html
        assert 'heart-pulse' in html
        assert 'bell' in html
        assert 'shield' in html


class TestManifest:
    """PWA web app manifest."""

    def test_manifest_returns_json(self, client):
        resp = client.get("/mobile/manifest")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    def test_manifest_has_required_fields(self, client):
        resp = client.get("/mobile/manifest")
        data = resp.json()

        assert data["name"] == "Kai Mobile Command"
        assert data["short_name"] == "Kai"
        assert data["start_url"] == "/mobile"
        assert data["display"] == "standalone"
        assert "icons" in data
        assert len(data["icons"]) == 2

    def test_manifest_icons_have_sizes(self, client):
        resp = client.get("/mobile/manifest")
        data = resp.json()

        sizes = {icon["sizes"] for icon in data["icons"]}
        assert "192x192" in sizes
        assert "512x512" in sizes

    def test_manifest_theme_colors_match_design(self, client):
        resp = client.get("/mobile/manifest")
        data = resp.json()

        assert data["background_color"] == "#020617"  # deep slate
        assert data["theme_color"] == "#16A34A"        # kai green


class TestServiceWorker:
    """Service worker endpoint."""

    def test_sw_returns_javascript(self, client):
        resp = client.get("/mobile/sw.js")
        assert resp.status_code == 200
        ct = resp.headers["content-type"]
        assert "javascript" in ct or "application/javascript" in ct

    def test_sw_contains_cache_version(self, client):
        resp = client.get("/mobile/sw.js")
        sw = resp.text

        assert "kai-launcher-v1" in sw
        assert "install" in sw
        assert "activate" in sw
        assert "fetch" in sw

    def test_sw_caches_launcher_assets(self, client):
        resp = client.get("/mobile/sw.js")
        sw = resp.text

        assert "/mobile" in sw
        assert "/mobile/manifest" in sw


class TestIcons:
    """PWA icon endpoints."""

    def test_icon_192_returns_svg(self, client):
        resp = client.get("/mobile/icon-192")
        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers["content-type"]
        # Should be valid SVG
        assert "<svg" in resp.text
        assert "viewBox" in resp.text

    def test_icon_512_returns_svg(self, client):
        resp = client.get("/mobile/icon-512")
        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers["content-type"]
        assert "<svg" in resp.text


class TestTilesAPI:
    """Tile data API endpoint."""

    def test_tiles_returns_json(self, client):
        resp = client.get("/mobile/tiles")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    def test_tiles_has_core_services(self, client):
        resp = client.get("/mobile/tiles")
        data = resp.json()

        tile_ids = {t["id"] for t in data["tiles"]}
        assert "kai-dashboard" in tile_ids
        assert "command-center" in tile_ids
        assert "health" in tile_ids
        assert "notifications" in tile_ids
        assert "wireguard" in tile_ids

    def test_tiles_has_external_services(self, client):
        resp = client.get("/mobile/tiles")
        data = resp.json()

        tile_ids = {t["id"] for t in data["tiles"]}
        assert "it-manager" in tile_ids
        assert "airdrop-hunter" in tile_ids
        assert "proxdash" in tile_ids
        assert "code-server" in tile_ids

    def test_tiles_have_required_fields(self, client):
        resp = client.get("/mobile/tiles")
        data = resp.json()

        for tile in data["tiles"]:
            assert "id" in tile
            assert "name" in tile
            assert "icon" in tile
            assert "url" in tile
            assert "type" in tile
            assert "status" in tile
            assert tile["type"] in ("internal", "external")

    def test_tiles_has_updated_at(self, client):
        resp = client.get("/mobile/tiles")
        data = resp.json()

        assert "updated_at" in data

    def test_tiles_count(self, client):
        resp = client.get("/mobile/tiles")
        data = resp.json()

        assert len(data["tiles"]) >= 10  # minimum coverage

    def test_internal_tiles_use_relative_urls(self, client):
        resp = client.get("/mobile/tiles")
        data = resp.json()

        # Invariant: internal tiles are same-origin relative paths (never
        # absolute/external). Tiles may point at /kai/*, /mobile, or other
        # API-served pages like /command-center.
        for tile in data["tiles"]:
            if tile["type"] == "internal":
                assert tile["url"].startswith("/")

    def test_each_tile_has_color_and_description(self, client):
        resp = client.get("/mobile/tiles")
        data = resp.json()

        for tile in data["tiles"]:
            assert "color" in tile
            assert "description" in tile
            assert tile["color"].startswith("#")
