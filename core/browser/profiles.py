"""Profile isolation — one persistent browser profile per (identity, provider).

Layout (under the operator data root)::

    profiles/<identity-slug>/<provider-slug>/
        user-data-dir/          # Chromium persistent profile
        storage_state.json      # Playwright storage_state (cookies/localStorage)
        profile.json            # non-secret metadata mirror

``profiles.json`` is the atomic index. Secrets (proxy credentials etc.) are
never written here — the profile carries a ``vault_namespace`` reference only.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from core.browser import storage
from core.browser.schema import (
    BrowserProfile,
    browser_vault_namespace,
    now_iso,
    profile_id_for,
    slug,
)

INDEX_FILE = "profiles.json"


class ProfileNotFound(KeyError):
    def __init__(self, profile_id: str):
        super().__init__(f"browser profile not found: {profile_id}")
        self.profile_id = profile_id


class ProfileStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        storage.ensure_dir(self.root / "profiles")

    # -- paths -----------------------------------------------------------------
    def _index_path(self) -> Path:
        return self.root / INDEX_FILE

    def _profile_dir(self, identity_id: str, provider_id: str) -> Path:
        d = (self.root / "profiles" / slug(identity_id) / slug(provider_id)).resolve()
        if not str(d).startswith(str(self.root.resolve())):
            raise ValueError("profile path escapes data root")
        return d

    # -- index -----------------------------------------------------------------
    def _records(self) -> list[dict]:
        data = storage.read_json(self._index_path(), {"records": []})
        if isinstance(data, dict):
            data = data.get("records", [])
        return list(data or [])

    def _write(self, records: list[dict]) -> None:
        storage.atomic_write_json(self._index_path(), {"records": records})

    def _model(self, record: dict) -> BrowserProfile:
        return BrowserProfile(**record)

    # -- API -------------------------------------------------------------------
    def create(self, identity_id: str, provider_id: str) -> BrowserProfile:
        existing = self.get_by_identity_provider(identity_id, provider_id)
        if existing is not None:
            return existing
        profile_id = profile_id_for(identity_id, provider_id)
        pdir = self._profile_dir(identity_id, provider_id)
        user_data_dir = pdir / "user-data-dir"
        storage_state_path = pdir / "storage_state.json"
        storage.ensure_dir(user_data_dir)
        now = now_iso()
        record = BrowserProfile(
            profile_id=profile_id,
            identity_id=identity_id,
            provider_id=provider_id,
            dir=str(pdir),
            user_data_dir=str(user_data_dir),
            storage_state_path=str(storage_state_path),
            vault_namespace=browser_vault_namespace(identity_id),
            created_at=now,
            updated_at=now,
        ).model_dump(mode="json")
        storage.atomic_write_json(pdir / "profile.json", record)
        storage.update_json(
            self._index_path(),
            lambda data: {"records": self._records() + [record]},
            default={"records": []},
        )
        return self._model(record)

    def get(self, profile_id: str) -> Optional[BrowserProfile]:
        for rec in self._records():
            if rec.get("profile_id") == profile_id:
                return self._model(rec)
        return None

    def get_by_identity_provider(
        self, identity_id: str, provider_id: str
    ) -> Optional[BrowserProfile]:
        return self.get(profile_id_for(identity_id, provider_id))

    def get_or_create(self, identity_id: str, provider_id: str) -> BrowserProfile:
        return self.get_by_identity_provider(identity_id, provider_id) or self.create(
            identity_id, provider_id
        )

    def list(self) -> list[BrowserProfile]:
        return [self._model(r) for r in self._records()]

    def touch(self, profile_id: str) -> None:
        def _mutate(_data):
            records = self._records()
            for rec in records:
                if rec.get("profile_id") == profile_id:
                    rec["updated_at"] = now_iso()
            return {"records": records}

        storage.update_json(self._index_path(), _mutate, default={"records": []})

    def delete(self, profile_id: str) -> bool:
        profile = self.get(profile_id)
        if profile is None:
            return False
        pdir = Path(profile.dir)
        if str(pdir.resolve()).startswith(str(self.root.resolve())) and pdir.exists():
            shutil.rmtree(pdir, ignore_errors=True)
        remaining = [r for r in self._records() if r.get("profile_id") != profile_id]
        self._write(remaining)
        return True
