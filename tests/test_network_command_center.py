"""Tests for Task 8: Network Topology Panel in Command Center UI."""

import pytest
import sys
import threading
import http.server
import socketserver
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class QuietTCPServer(socketserver.TCPServer):
    """TCP server that silently handles requests without logging."""
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass  # suppress "Address already in use" noise


class Handler(http.server.SimpleHTTPRequestHandler):
    """Handler that serves the HTML file and logs which network API paths were called."""

    network_calls = []

    def log_message(self, format, *args):
        pass  # suppress request logging

    def do_GET(self):
        path = self.path
        if path == "/network/topology" or path == "/network/topology/":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(Handler.mock_topo).encode())
        elif path == "/network/connectivity" or path == "/network/connectivity/":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(Handler.mock_conn).encode())
        else:
            # Serve static file (the HTML page)
            super().do_GET()


def make_handler(html_path):
    """Create a Handler class that serves from the HTML file's directory."""
    class LocalHandler(Handler):
        _html_path = html_path
        _directory = str(Path(html_path).parent)

        def translate_path(self, path):
            if path == "/" or path == "":
                return self._html_path
            return super().translate_path(path)

    return LocalHandler


class TestNetworkTopologyPanel:
    """Verify the network topology panel renders correctly in the Command Center."""

    @pytest.fixture
    def topology_html(self):
        """Return the absolute path to the Command Center HTML file."""
        return str(Path(__file__).parent.parent / "core" / "kai" / "command_center.html")

    @pytest.fixture
    def mock_topo_response(self):
        """Sample topology data matching /network/topology endpoint shape."""
        return {
            "schema_version": 1,
            "generated_at": "2026-08-30T14:00:00Z",
            "sites": [
                {
                    "name": "SITE-A",
                    "hostname": "pve",
                    "status": "online",
                    "lan_cidr": "192.168.99.0/24",
                    "gateway": "192.168.99.254",
                    "tailscale_ip": "100.83.4.27",
                    "routes": ["192.168.99.0/24"],
                    "lxc_count": 12,
                    "container_count": 14,
                },
                {
                    "name": "SITE-B",
                    "hostname": "pve-b",
                    "status": "online",
                    "lan_cidr": "192.168.1.0/24",
                    "gateway": "192.168.1.1",
                    "tailscale_ip": "100.89.97.76",
                    "routes": ["192.168.1.0/24"],
                    "lxc_count": 8,
                    "container_count": 10,
                },
            ],
        }

    @pytest.fixture
    def mock_conn_response(self):
        """Sample connectivity data matching /network/connectivity endpoint shape."""
        return {
            "tunnel": {
                "status": "healthy",
                "latency_a_to_b": 218,
                "latency_b_to_a": 220,
                "packet_loss": 0.0,
                "routes_a_to_b": True,
                "routes_b_to_a": True,
                "last_test": "2026-08-30T14:22:01Z",
            }
        }

    def _run_server(self, html_path, topo_data, conn_data):
        """Start a local HTTP server in a background thread."""
        Handler.mock_topo = topo_data
        Handler.mock_conn = conn_data
        Handler.network_calls = []
        handler_cls = make_handler(html_path)
        handler_cls.mock_topo = topo_data
        handler_cls.mock_conn = conn_data
        handler_cls.network_calls = []

        server = QuietTCPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, f"http://127.0.0.1:{port}"

    def test_panel_renders_healthy_topology(self, topology_html, mock_topo_response, mock_conn_response):
        """Network panel shows HEALTHY status when all sites and tunnel are up."""
        from playwright.sync_api import sync_playwright

        server, base_url = self._run_server(topology_html, mock_topo_response, mock_conn_response)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()

                def handle_route(route):
                    url = route.request.url
                    # Let fonts and external resources through
                    if url.startswith("https://fonts.") or url.startswith("https://cdn."):
                        route.continue_()
                    else:
                        route.continue_()

                # Let all non-network requests pass through
                page.route("**", handle_route)

                page.goto(f"{base_url}#home")
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(500)

                page.evaluate("navigateTo('#infrastructure')")
                page.wait_for_timeout(1500)

                content = page.locator("#infra-network").inner_html()

                assert "SITE-TO-SITE" in content
                assert "TAILSCALE" in content
                assert "HEALTHY" in content

                assert "pve" in content
                assert "pve-b" in content
                assert "192.168.99.0/24" in content
                assert "192.168.1.0/24" in content
                assert "100.83.4.27" in content
                assert "100.89.97.76" in content

                assert "TUNNEL" in content
                assert "218" in content
                assert "220" in content

                browser.close()
        finally:
            server.shutdown()

    def test_panel_shows_critical_when_site_offline(self, topology_html, mock_conn_response):
        """Panel shows CRITICAL when one site is offline."""
        topo = {
            "sites": [
                {
                    "name": "SITE-A",
                    "hostname": "pve",
                    "status": "online",
                    "lan_cidr": "192.168.99.0/24",
                    "gateway": "192.168.99.254",
                    "tailscale_ip": "100.83.4.27",
                    "routes": [],
                    "lxc_count": 12,
                    "container_count": 14,
                },
                {
                    "name": "SITE-B",
                    "hostname": "pve-b",
                    "status": "offline",
                    "lan_cidr": "192.168.1.0/24",
                    "gateway": "192.168.1.1",
                    "tailscale_ip": "100.89.97.76",
                    "routes": [],
                    "lxc_count": 0,
                    "container_count": 0,
                },
            ],
        }

        from playwright.sync_api import sync_playwright

        server, base_url = self._run_server(topology_html, topo, mock_conn_response)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()

                def handle_route(route):
                    url = route.request.url
                    if url.startswith("https://fonts.") or url.startswith("https://cdn."):
                        route.continue_()
                    else:
                        route.continue_()

                page.route("**", handle_route)

                page.goto(f"{base_url}#home")
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(500)
                page.evaluate("navigateTo('#infrastructure')")
                page.wait_for_timeout(1500)

                content = page.locator("#infra-network").inner_html()
                assert "CRITICAL" in content
                assert "OFFLINE" in content

                browser.close()
        finally:
            server.shutdown()

    def test_panel_shows_critical_when_tunnel_down(self, topology_html, mock_topo_response):
        """Panel shows CRITICAL when tunnel is down."""
        conn = {
            "tunnel": {
                "status": "down",
                "latency_a_to_b": None,
                "latency_b_to_a": None,
                "packet_loss": None,
                "routes_a_to_b": False,
                "routes_b_to_a": False,
                "last_test": None,
            }
        }

        from playwright.sync_api import sync_playwright

        server, base_url = self._run_server(topology_html, mock_topo_response, conn)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()

                def handle_route(route):
                    url = route.request.url
                    if url.startswith("https://fonts.") or url.startswith("https://cdn."):
                        route.continue_()
                    else:
                        route.continue_()

                page.route("**", handle_route)

                page.goto(f"{base_url}#home")
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(500)
                page.evaluate("navigateTo('#infrastructure')")
                page.wait_for_timeout(1500)

                content = page.locator("#infra-network").inner_html()
                assert "CRITICAL" in content
                assert "DOWN" in content

                browser.close()
        finally:
            server.shutdown()

    def test_panel_graceful_when_apis_fail(self, topology_html):
        """Panel renders without crashing when APIs return errors."""
        from playwright.sync_api import sync_playwright

        server, base_url = self._run_server(topology_html, {}, {})
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()

                # Abort network API calls to simulate failures
                def handle_route(route):
                    url = route.request.url
                    if "/network/" in url:
                        route.abort(error_code="failed")
                    elif url.startswith("https://fonts.") or url.startswith("https://cdn."):
                        route.continue_()
                    else:
                        route.continue_()

                page.route("**", handle_route)

                page.goto(f"{base_url}#home")
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(500)
                page.evaluate("navigateTo('#infrastructure')")
                page.wait_for_timeout(1500)

                assert "infra-network" in page.content()
                content = page.locator("#infra-network").inner_html()
                assert len(content) > 0

                browser.close()
        finally:
            server.shutdown()
