# tests/test_connectivity_monitor.py
import pytest, sys, os
sys.path.insert(0, "/project/ai-orchestrator")

class TestLatency:
    def test_latency_returns_dict(self):
        from core.connectivity_monitor import test_latency
        # Mock subprocess to avoid real network
        import unittest.mock as mock
        with mock.patch("subprocess.run") as m:
            m.return_value = mock.Mock(stdout=b"rtt min/avg/max/mdev = 1.2/2.3/3.4/0.5")
            result = test_latency("8.8.8.8", "1.1.1.1", count=1)
            assert "avg_ms" in result

    def test_latency_returns_nan_on_error(self):
        from core.connectivity_monitor import test_latency
        import unittest.mock as mock
        with mock.patch("subprocess.run") as m:
            m.side_effect = Exception("no network")
            result = test_latency("8.8.8.8", "1.1.1.1")
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
