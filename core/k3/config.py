from enum import Enum
from pathlib import Path


class PersistPolicy(str, Enum):
    DISCARD = "discard"
    REPORT = "report"
    COMMIT = "commit"
    ARTIFACTS = "artifacts"


class NetworkPolicy(str, Enum):
    NONE = "none"
    HOST = "host"


class K3Config:
    def __init__(
        self,
        workspace_path,
        command,
        persist=PersistPolicy.DISCARD,
        network=NetworkPolicy.NONE,
        env=None,
        timeout=300,
        memory_limit=None,
        cpu_limit=None,
        artifact_patterns=None,
        artifact_output_dir=None,
    ):
        self.workspace_path = Path(workspace_path).resolve()
        self.command = list(command) if isinstance(command, (list, tuple)) else [command]
        self.persist = PersistPolicy(persist)
        self.network = NetworkPolicy(network)
        self.env = dict(env) if env else {}
        self.timeout = int(timeout)
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.artifact_patterns = list(artifact_patterns) if artifact_patterns else []
        self.artifact_output_dir = Path(artifact_output_dir).resolve() if artifact_output_dir else None

    def validate(self):
        if not self.command:
            raise ValueError("Command must not be empty")
        if self.timeout < 1:
            raise ValueError(f"timeout must be >= 1, got {self.timeout}")
        if not self.workspace_path.exists():
            raise ValueError(f"Workspace path does not exist: {self.workspace_path}")
        if not self.workspace_path.is_dir():
            raise ValueError(f"Workspace path is not a directory: {self.workspace_path}")
        if self.persist == PersistPolicy.ARTIFACTS:
            if not self.artifact_patterns:
                raise ValueError("artifact_patterns required for ARTIFACTS policy")
            if not self.artifact_output_dir:
                raise ValueError("artifact_output_dir required for ARTIFACTS policy")
        if self.timeout < 1:
            raise ValueError("timeout must be >= 1")

    @classmethod
    def from_yaml(cls, path):
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)

    def to_dict(self):
        return {
            "workspace_path": str(self.workspace_path),
            "command": self.command,
            "persist": self.persist.value,
            "network": self.network.value,
            "env": self.env,
            "timeout": self.timeout,
            "memory_limit": self.memory_limit,
            "cpu_limit": self.cpu_limit,
            "artifact_patterns": self.artifact_patterns,
            "artifact_output_dir": str(self.artifact_output_dir) if self.artifact_output_dir else None,
        }

    def __repr__(self):
        return f"K3Config(workspace={self.workspace_path}, cmd={' '.join(self.command)}, persist={self.persist.value})"
