import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class RegistryStatus(str, enum.Enum):
    registered = "registered"
    building = "building"
    deployed = "deployed"
    failed = "failed"
    archived = "archived"


class ChangeRecord(BaseModel):
    field: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    timestamp: str


class AppRecord(BaseModel):
    id: str
    app_name: str
    description: str = ""
    version: str = "0.1.0"
    repo: str
    status: RegistryStatus = RegistryStatus.registered
    deployed_url: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str
    history: list[ChangeRecord] = Field(default_factory=list)


class AppCreate(BaseModel):
    app_name: str = Field(min_length=1, max_length=255)
    description: str = ""
    version: str = "0.1.0"
    repo: str = Field(min_length=1)
    status: RegistryStatus = RegistryStatus.registered
    deployed_url: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class AppUpdate(BaseModel):
    description: Optional[str] = None
    version: Optional[str] = None
    status: Optional[RegistryStatus] = None
    deployed_url: Optional[str] = None
    repo: Optional[str] = None
    metadata: Optional[dict] = None


class RegistryFile(BaseModel):
    schema_version: int = 1
    records: list[AppRecord] = Field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
