# tests/test_connectivity_monitor.py
import pytest, sys, os
sys.path.insert(0, "/project/ai-orchestrator")

class TestLatency:
    def test_latency_returns_dict(self):
        from core.connectivity_monitor import test_latency
        # Mock subprocess to avoid real network
        import unittest.mock as mock
        with mock.patch("subprocess.run") as m:
            m.return_value = mock.Mock(returncode=0, stdout=b"rtt min/avg/max/mdev = 1.2/2.3/3.4/0.5")
            result = test_latency("1.1.1.1", count=1)
            assert "avg_ms" in result

    def test_latency_returns_nan_on_error(self):
        from core.connectivity_monitor import test_latency
        import unittest.mock as mock
        with mock.patch("subprocess.run") as m:
            m.side_effect = Exception("no network")
            result = test_latency("1.1.1.1")
            assert result.get("avg_ms") is None

class TestTcpConnect:
    def test_tcp_connect_success(self):
        from core.connectivity_monitor import test_tcp_connect
        import unittest.mock as mock
        with mock.patch("subprocess.run") as m:
            m.return_value = mock.Mock(returncode=0)
            assert test_tcp_connect("192.168.1.1", 8006) is True

    def test_tcp_connect_failure(self):
        from core.connectivity_monitor import test_tcp_connect
        import unittest.mock as mock
        with mock.patch("subprocess.run") as m:
            m.return_value = mock.Mock(returncode=1)
            assert test_tcp_connect("192.168.1.1", 8006) is False

class TestHttpHealth:
    def test_http_health_success(self):
        from core.connectivity_monitor import test_http_health
        import unittest.mock as mock
        with mock.patch("requests.head") as m:
            m.return_value = mock.Mock(status_code=200)
            with mock.patch("core.connectivity_monitor.datetime") as mdt:
                elapsed_mock = mock.Mock(total_seconds=lambda: 0.05)
                mdt.now.return_value = mock.Mock(timezone=mock.Mock())
                mdt.now.return_value.__sub__ = mock.Mock(return_value=elapsed_mock)
                result = test_http_health("http://example.com")
                assert result["ok"] is True
                assert result["status_code"] == 200

    def test_http_health_timeout(self):
        from core.connectivity_monitor import test_http_health
        import unittest.mock as mock, requests
        with mock.patch("requests.head") as m:
            m.side_effect = requests.exceptions.Timeout()
            result = test_http_health("http://example.com")
            assert result["ok"] is False
            assert result["error"] == "timeout"

class TestTraceroute:
    def test_traceroute_parses_output(self):
        from core.connectivity_monitor import traceroute
        import unittest.mock as mock
        with mock.patch("subprocess.run") as m:
            m.return_value = mock.Mock(
                stdout="traceroute to 1.1.1.1, 15 hops max, 60 byte packets\n 1  192.168.1.1 (192.168.1.1)  1.2 ms  1.3 ms  1.4 ms\n 2  10.0.0.1 (10.0.0.1)  5.1 ms  5.2 ms  5.3 ms\n",
                returncode=0
            )
            hops = traceroute("1.1.1.1")
            assert len(hops) == 2
            assert hops[0]["hop"] == 1
            assert hops[1]["hop"] == 2
