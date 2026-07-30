import core.health as health


def test_expected_services_covers_the_real_running_application_portfolio():
    # Confirmed live 2026-07-30: EXPECTED_SERVICES only listed 3 of the ~12
    # containers actually running on this box (pulse, proxdash-backend,
    # proxdash-frontend), so Kai had no health awareness at all of the
    # user's other applications (it-manager was the one that surfaced this
    # live) -- their absence/crash would never generate a "Missing
    # container" finding. This just asserts the real portfolio is covered;
    # update it if the set of persistent application containers changes.
    expected_portfolio = {
        "pulse",
        "proxdash-backend",
        "proxdash-frontend",
        "it-manager",
        "it-manager-mailpit",
        "watchtower",
        "code-server",
        "airdrop-hunter-airdrop-hunter-1",
        "portfolio-portfolio-1",
        "docker-watcher",
        "door-bridge",
    }

    assert set(health.EXPECTED_SERVICES) == expected_portfolio


def test_analyze_reports_missing_container_for_each_expected_service_absent(monkeypatch):
    monkeypatch.setattr(health, "load", lambda name: {"docker": {"available": True, "containers": ["pulse"]}})
    monkeypatch.setattr(health, "analyze_docker", lambda: [])
    monkeypatch.setattr(health, "check_services", lambda: [])
    monkeypatch.setattr(health, "analyze_proxmox_cluster", lambda: [])

    findings = health.analyze()

    missing = {f["service"] for f in findings if f["issue"].startswith("Missing container")}
    assert missing == set(health.EXPECTED_SERVICES) - {"pulse"}


def test_analyze_reports_nothing_missing_when_full_portfolio_is_running(monkeypatch):
    monkeypatch.setattr(
        health, "load",
        lambda name: {"docker": {"available": True, "containers": list(health.EXPECTED_SERVICES)}},
    )
    monkeypatch.setattr(health, "analyze_docker", lambda: [])
    monkeypatch.setattr(health, "check_services", lambda: [])
    monkeypatch.setattr(health, "analyze_proxmox_cluster", lambda: [])

    findings = health.analyze()

    assert not any(f["issue"].startswith("Missing container") for f in findings)


def test_analyze_reports_docker_unavailable_and_stops(monkeypatch):
    monkeypatch.setattr(health, "load", lambda name: {"docker": {"available": False}})

    findings = health.analyze()

    assert findings == [{"severity": "critical", "service": "docker", "issue": "Docker unavailable"}]
