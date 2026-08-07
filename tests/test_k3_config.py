from pathlib import Path
import pytest
from core.k3.config import K3Config, PersistPolicy, NetworkPolicy


def test_default_config():
    config = K3Config(
        workspace_path="/tmp/ws",
        command=["make", "build"],
    )
    assert config.workspace_path == Path("/tmp/ws")
    assert config.command == ["make", "build"]
    assert config.persist == PersistPolicy.DISCARD
    assert config.network == NetworkPolicy.NONE
    assert config.timeout == 300
    assert config.env == {}
    assert config.memory_limit is None
    assert config.cpu_limit is None
    assert config.artifact_patterns == []
    assert config.artifact_output_dir is None


def test_all_policies():
    for policy in PersistPolicy:
        config = K3Config(
            workspace_path="/tmp/ws",
            command=["echo", "hello"],
            persist=policy,
        )
        assert config.persist == policy


def test_network_policy_host():
    config = K3Config(
        workspace_path="/tmp/ws",
        command=["echo", "hello"],
        network="host",
    )
    assert config.network == NetworkPolicy.HOST


def test_validate_raises_on_missing_workspace():
    config = K3Config(
        workspace_path="/nonexistent/path/xyz",
        command=["echo", "hello"],
    )
    with pytest.raises(ValueError, match="does not exist"):
        config.validate()


def test_validate_raises_on_file_not_dir(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hi")
    config = K3Config(
        workspace_path=str(f),
        command=["echo", "hello"],
    )
    with pytest.raises(ValueError, match="not a directory"):
        config.validate()


def test_validate_workspace_is_dir(tmp_path):
    config = K3Config(
        workspace_path=str(tmp_path),
        command=["echo", "hello"],
    )
    config.validate()


def test_validate_empty_command():
    config = K3Config(
        workspace_path="/tmp/ws",
        command=[],
    )
    with pytest.raises(ValueError, match="Command must not be empty"):
        config.validate()


def test_validate_artifacts_policy_requires_patterns(tmp_path):
    config = K3Config(
        workspace_path=str(tmp_path),
        command=["echo", "hello"],
        persist=PersistPolicy.ARTIFACTS,
        artifact_output_dir="/tmp/out",
    )
    with pytest.raises(ValueError, match="artifact_patterns"):
        config.validate()


def test_validate_artifacts_policy_requires_output_dir(tmp_path):
    config = K3Config(
        workspace_path=str(tmp_path),
        command=["echo", "hello"],
        persist=PersistPolicy.ARTIFACTS,
        artifact_patterns=["*.tar.gz"],
    )
    with pytest.raises(ValueError, match="artifact_output_dir"):
        config.validate()


def test_validate_timeout_positive():
    config = K3Config(
        workspace_path="/tmp/ws",
        command=["echo"],
        timeout=0,
    )
    with pytest.raises(ValueError, match="timeout"):
        config.validate()


def test_string_command_converted_to_list():
    config = K3Config(
        workspace_path="/tmp/ws",
        command="echo hello",
    )
    assert config.command == ["echo hello"]


def test_to_dict():
    config = K3Config(
        workspace_path="/tmp/ws",
        command=["make", "build"],
        persist=PersistPolicy.REPORT,
        network=NetworkPolicy.HOST,
        env={"FOO": "bar"},
        timeout=600,
        memory_limit="1g",
        cpu_limit="2.0",
        artifact_patterns=["*.tar.gz"],
        artifact_output_dir="/tmp/out",
    )
    d = config.to_dict()
    assert d["workspace_path"] == "/tmp/ws"
    assert d["command"] == ["make", "build"]
    assert d["persist"] == "report"
    assert d["network"] == "host"
    assert d["env"] == {"FOO": "bar"}
    assert d["timeout"] == 600
    assert d["memory_limit"] == "1g"
    assert d["cpu_limit"] == "2.0"
    assert d["artifact_patterns"] == ["*.tar.gz"]
    assert d["artifact_output_dir"] == "/tmp/out"
