import core.docker_analyzer as docker_analyzer


def test_stopped_container_is_detected_as_critical_service_crash(monkeypatch):
    monkeypatch.setattr(
        docker_analyzer,
        "inspect_containers",
        lambda: {"containers": [{"Names": "pulse", "State": "exited", "HealthStatus": ""}]}
    )

    findings = docker_analyzer.analyze_docker()

    assert len(findings) == 1
    assert findings[0]["severity"] == "critical"
    assert findings[0]["service"] == "pulse"
    assert findings[0]["issue"] == "Container stopped"


def test_unhealthy_running_container_is_a_warning(monkeypatch):
    monkeypatch.setattr(
        docker_analyzer,
        "inspect_containers",
        lambda: {"containers": [{"Names": "pulse", "State": "running", "HealthStatus": "unhealthy"}]}
    )

    findings = docker_analyzer.analyze_docker()

    assert findings[0]["severity"] == "warning"
    assert findings[0]["issue"] == "Container unhealthy"


def test_healthy_running_container_produces_no_findings(monkeypatch):
    monkeypatch.setattr(
        docker_analyzer,
        "inspect_containers",
        lambda: {"containers": [{"Names": "pulse", "State": "running", "HealthStatus": "healthy"}]}
    )

    assert docker_analyzer.analyze_docker() == []
