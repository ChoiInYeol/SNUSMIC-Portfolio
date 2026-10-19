"""Actual catalog and real SQLite reflection; no production demo entities or invented keys."""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, text

import metadata_api
import schema_metadata as metadata


def test_catalog_is_real_and_has_no_invented_database_constraints():
    doc = metadata.catalog_metadata()
    assert len(doc.entities) >= 26
    assert {e.layer for e in doc.entities} == {"bronze", "silver", "gold"}
    assert all(e.kind == "dataset" and not any(c.primary_key for c in e.columns) for e in doc.entities)
    assert all(r.kind == "etl" for r in doc.relationships)
    parsed = next(e for e in doc.entities if e.id == "silver.report_parsed")
    assert "source_file" in {c.name for c in parsed.columns}


def test_real_database_reflects_composite_pk_fk_and_view(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.sqlite'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE parent (id INTEGER, version INTEGER, PRIMARY KEY(id, version))"))
        conn.execute(text("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL, version INTEGER, FOREIGN KEY(parent_id, version) REFERENCES parent(id, version))"))
        conn.execute(text("CREATE VIEW parent_view AS SELECT id FROM parent"))
    doc = metadata.database_metadata(engine, {"main": "silver"})
    entities = {e.id: e for e in doc.entities}
    assert {c.name for c in entities["main.parent"].columns if c.primary_key} == {"id", "version"}
    assert entities["main.parent_view"].kind == "view"
    assert entities["main.child"].layer == "silver"
    assert not next(c for c in entities["main.child"].columns if c.name == "parent_id").nullable
    [fk] = doc.relationships
    assert (fk.source, fk.target, fk.source_columns, fk.target_columns) == (
        "main.child", "main.parent", ["parent_id", "version"], ["id", "version"])
    assert metadata.database_metadata(engine).entities[0].layer == "unassigned"
    engine.dispose()


def test_schema_contract_rejects_broken_relationships():
    doc = metadata.catalog_metadata().model_dump()
    doc["relationships"][0]["target"] = "nonexistent"
    with pytest.raises(ValidationError, match="unresolved relationship"):
        metadata.SchemaMetadata.model_validate(doc)


def test_fastapi_serves_contract_conditional_requests_and_no_writes(monkeypatch):
    monkeypatch.delenv("METADATA_DATABASE_URL", raising=False)
    client = TestClient(metadata_api.app)
    response = client.get("/metadata/schema")
    assert response.status_code == 200
    metadata.SchemaMetadata.model_validate(response.json())
    unchanged = client.get("/metadata/schema", headers={"If-None-Match": response.headers["etag"]})
    assert unchanged.status_code == 304 and not unchanged.content
    assert client.post("/metadata/schema").status_code == 405
    preflight = client.options("/metadata/schema", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "/metadata/schema" in client.get("/openapi.json").json()["paths"]


def test_backend_failure_is_503_without_credentials_or_cached_payload(monkeypatch):
    def fail():
        raise RuntimeError("postgresql://secret:password@db/private")
    monkeypatch.setattr(metadata_api, "load_metadata", fail)
    response = TestClient(metadata_api.app).get("/metadata/schema")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "password" not in response.text and "entities" not in response.json()


def test_metadata_snapshot_and_openapi_match_sources():
    assert metadata.write_snapshot(check=True) == 0, "run uv run python scripts/schema_metadata.py"
    doc = json.loads(metadata.OUTPUT.read_text(encoding="utf-8"))
    metadata.SchemaMetadata.model_validate(doc)
