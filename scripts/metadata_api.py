"""Read-only metadata sidecar: uv run uvicorn scripts.metadata_api:app --port 8000.

Set METADATA_DATABASE_URL to reflect a database; otherwise serves the actual file lake.
Credentials stay server-side. No data rows, SQL execution or database mutation endpoints.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_metadata import SchemaMetadata, catalog_metadata, database_metadata  # noqa: E402

app = FastAPI(title="Verdict Archive Metadata", version="1.0.0")
app.add_middleware(CORSMiddleware,
                   allow_origins=os.getenv("METADATA_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3100").split(","),
                   allow_methods=["GET"], allow_headers=["If-None-Match"], expose_headers=["ETag"])


def load_metadata() -> SchemaMetadata:
    url = os.getenv("METADATA_DATABASE_URL")
    if not url:
        return catalog_metadata()
    engine = create_engine(url)
    try:
        layers = json.loads(os.environ["METADATA_SCHEMA_LAYERS"]) if os.getenv("METADATA_SCHEMA_LAYERS") else None
        schemas = os.environ["METADATA_SCHEMAS"].split(",") if os.getenv("METADATA_SCHEMAS") else None
        lineage_path = os.getenv("METADATA_LINEAGE_PATH")
        etl = json.loads(Path(lineage_path).read_text(encoding="utf-8")) if lineage_path else None
        return database_metadata(engine, layers, schemas, etl)
    finally:
        engine.dispose()


@app.get("/metadata/schema", response_model=SchemaMetadata)
def get_metadata(request: Request):
    try:
        doc = load_metadata().model_dump()
    except Exception:
        logging.getLogger(__name__).warning("Metadata inspection failed; verify server configuration")
        return JSONResponse(status_code=503, content={"type": "about:blank", "title": "Metadata unavailable",
                            "status": 503, "detail": "Metadata inspection failed. Check server configuration."},
                            media_type="application/problem+json", headers={"Cache-Control": "no-store"})
    stable = {k: v for k, v in doc.items() if k != "generated_at"}
    etag = '"' + hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(doc, headers=headers)
