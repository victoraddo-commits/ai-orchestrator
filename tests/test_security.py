import pytest

from core.security import is_dangerous_command, is_known_action, enforce_action_is_safe, SecurityViolation


def test_known_low_risk_action_passes():
    enforce_action_is_safe("restart_container", "docker restart svc-a")


def test_unclassified_action_is_blocked():
    assert is_known_action("delete_everything") is False

    with pytest.raises(SecurityViolation):
        enforce_action_is_safe("delete_everything", "rm nothing-dangerous")


@pytest.mark.parametrize("command", [
    "rm -rf /",
    "rm -rf /*",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    "iptables -F",
    "iptables --flush",
    "ufw disable",
    "useradd hacker",
    "passwd root",
    "chmod 777 /",
    "cat /etc/shadow",
    "cp id_rsa /tmp/",
    "echo pwned >> ~/.ssh/authorized_keys",
])
def test_dangerous_commands_are_blocked(command):
    assert is_dangerous_command(command) is True

    with pytest.raises(SecurityViolation):
        enforce_action_is_safe("restart_container", command)


@pytest.mark.parametrize("command", [
    "docker restart svc-a",
    "restart_container on svc-a",
    "systemctl restart nginx",
])
def test_safe_commands_are_not_blocked(command):
    assert is_dangerous_command(command) is False
