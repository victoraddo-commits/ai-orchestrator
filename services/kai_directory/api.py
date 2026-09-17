from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse

from .conformance import check
from .discovery import discover_all
from .health import check_all
from .index import render_index
from .models import Record
from .namer import assign_names, policy_block, serve_commands
from .store import RecordStore


def create_app(store: RecordStore) -> FastAPI:
    app = FastAPI(title="Kai Service Directory")

    @app.get("/health")
    def health():
        return {"ok": True, "services": len(store.list())}

    @app.get("/services")
    def list_services(category: str = "", q: str = ""):
        return [r.to_dict() for r in store.list(category=category, q=q)]

    @app.get("/services/{id_}")
    def get_service(id_: str):
        r = store.get(id_)
        if not r:
            raise HTTPException(404, "not found")
        return r.to_dict()

    @app.post("/services", status_code=201)
    def create_service(body: dict):
        body["source"] = body.get("source", "manual")
        store.upsert(Record(**{k: v for k, v in body.items()
                               if k in Record.__dataclass_fields__}))
        return store.get(body["id"]).to_dict()

    @app.put("/services/{id_}")
    def update_service(id_: str, body: dict):
        body["id"] = id_
        store.upsert(Record(**{k: v for k, v in body.items() if k in Record.__dataclass_fields__}))
        return store.get(id_).to_dict()

    @app.delete("/services/{id_}")
    def delete_service(id_: str):
        if not store.get(id_):
            raise HTTPException(404, "not found")
        store.delete(id_)
        return {"deleted": id_}

    @app.post("/discover")
    def discover():
        recs = discover_all()
        result = store.reconcile(recs)
        return {"discovered": len(recs), **result}

    @app.post("/health/refresh")
    def refresh():
        recs = check_all(store.list())
        for r in recs:
            store.upsert(r)
        return {r.id: r.health_status for r in recs}

    @app.get("/conformance")
    def conformance():
        return check(discover_all(), store.list())

    def named() -> list[Record]:
        return assign_names(store.list(), {})

    @app.get("/policy")
    def policy():
        return Response(policy_block(named()), media_type="application/json")

    @app.get("/serve-commands")
    def serve_commands_ep(node: str):
        return serve_commands(named(), node)

    @app.get("/export")
    def export(format: str = "json"):
        rows = [r.to_dict() for r in store.list()]
        if format == "yaml":
            lines = []
            for r in rows:
                lines.append(f"- id: {r['id']}")
                for k, v in r.items():
                    if k != "id":
                        lines.append(f"  {k}: {v}")
            return Response("\n".join(lines) + "\n", media_type="text/yaml")
        return rows

    @app.get("/", response_class=HTMLResponse)
    def index():
        return render_index(store.list())

    return app
