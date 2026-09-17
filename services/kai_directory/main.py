from __future__ import annotations

import os

import uvicorn

from .api import create_app, persist_names
from .store import RecordStore

DB = os.environ.get("KAI_DIRECTORY_DB", "/var/lib/kai-directory/services.db")


def main():
    os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
    store = RecordStore(DB)
    persist_names(store)
    app = create_app(store)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("KAI_DIRECTORY_PORT", "8097")))


if __name__ == "__main__":
    main()
