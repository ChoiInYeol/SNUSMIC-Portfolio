"""One metadata contract for the file lake and read-only SQLAlchemy reflection.

No sample database is supplied. Catalog fields are observed from tracked artifacts, never
invented PK/FK constraints. Database constraints come only from the database inspector.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine, inspect

import build_catalog
import lake

Layer = Literal["bronze", "silver", "gold", "unassigned"]
OUTPUT = lake.PUBLIC / "metadata" / "schema.json"


class Column(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    data_type: str
    nullable: bool | None = None
    primary_key: bool = False
    description: str | None = None


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    schema_name: str
    layer: Layer
    kind: Literal["table", "view", "dataset"]
    columns: list[Column]
    column_status: Literal["declared", "observed", "unavailable"]
    location: str
    row_count: int | None = None
    description: str | None = None
    producers: list[str] = Field(default_factory=list)


class Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source: str
    target: str
    kind: Literal["foreign_key", "etl"]
    source_columns: list[str] = Field(default_factory=list)
    target_columns: list[str] = Field(default_factory=list)
    label: str


class SchemaMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    generated_at: str
    source: Literal["catalog", "database"]
    source_label: str
    entities: list[Entity]
    relationships: list[Relationship]

    @model_validator(mode="after")
    def validate_graph(self):
        entities = {e.id: e for e in self.entities}
        if len(entities) != len(self.entities):
            raise ValueError("duplicate entity ids")
        if len({r.id for r in self.relationships}) != len(self.relationships):
            raise ValueError("duplicate relationship ids")
        for e in self.entities:
            if len({c.name for c in e.columns}) != len(e.columns):
                raise ValueError(f"duplicate columns in {e.id}")
        for r in self.relationships:
            if r.source not in entities or r.target not in entities:
                raise ValueError(f"unresolved relationship {r.id}")
            if r.kind == "foreign_key":
                if not r.source_columns or len(r.source_columns) != len(r.target_columns):
                    raise ValueError(f"invalid FK column mapping {r.id}")
                for entity_id, columns in ((r.source, r.source_columns), (r.target, r.target_columns)):
                    if not set(columns) <= {c.name for c in entities[entity_id].columns}:
                        raise ValueError(f"unknown FK columns {r.id}")
        return self


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def observed_columns(spec: dict, tracked: bool | None = None) -> list[Column]:
    if tracked is None:
        tracked = build_catalog.tracked_in_git(spec["path"])
    if not tracked:
        return []
    if spec["format"] not in {"csv", "json", "jsonl"}:
        return []
    # Dataset writers designate a representative schema file for heterogeneous API
    # collections. Do not stat thousands of JSON/markdown files to find its first item.
    preferred = lake.ROOT / spec["schema_file"] if spec.get("schema_file") else None
    if preferred and preferred.is_file():
        candidate = preferred
    else:
        files = build_catalog._files(spec["path"])
        candidate = next((p for p in files if p.suffix in {".csv", ".json", ".jsonl"}), None)
    if candidate is None:
        return []
    if candidate.suffix == ".csv":
        with candidate.open(encoding="utf-8-sig", newline="") as fh:
            return [Column(name=name, data_type="csv text") for name in next(csv.reader(fh), [])]
    if candidate.suffix == ".jsonl":
        with candidate.open(encoding="utf-8") as fh:
            body = json.loads(next((line for line in fh if line.strip()), "{}"))
    else:
        body = json.loads(candidate.read_text(encoding="utf-8"))
    if isinstance(body, list):
        body = next((row for row in body if isinstance(row, dict)), {})
    if not isinstance(body, dict):
        return []
    return [Column(name=k, data_type=_type(v)) for k, v in body.items()]


def catalog_metadata() -> SchemaMetadata:
    specs = [s for s in build_catalog.DATASETS if s["id"] != "serving.schema_metadata"]
    # The versioned catalog already records Git policy. Re-running git check-ignore twice
    # per dataset made a metadata request exceed 15 seconds during CPU-bound OCR jobs.
    catalog = json.loads(lake.CATALOG.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != build_catalog.CATALOG_SCHEMA_VERSION:
        raise ValueError("unsupported data catalog schema")
    tracked_by_id = {entry["id"]: entry["tracked_in_git"] for entry in catalog["datasets"]}
    entities = []
    for spec in specs:
        tracked = tracked_by_id.get(spec["id"])
        if tracked is None:  # a newly added dataset, before its first catalog regeneration
            tracked = build_catalog.tracked_in_git(spec["path"])
        columns = observed_columns(spec, tracked)
        exact_path = lake.ROOT / spec["path"]
        rows = build_catalog._rows(exact_path, spec["format"]) if tracked and exact_path.is_file() else None
        entities.append(Entity(
            id=spec["id"], name=spec["id"].split(".", 1)[1], schema_name=spec["id"].split(".", 1)[0],
            layer=spec["layer"], kind="dataset", columns=columns,
            column_status="observed" if columns else "unavailable", location=spec["path"],
            row_count=rows, producers=spec["producer"],
            description=f"{spec['format']} · " + ("versioned artifact" if tracked else "local/cache; fields not inspected"),
        ))
    ids = {e.id for e in entities}
    relationships = [Relationship(id=f"etl:{source}:{spec['id']}", source=source, target=spec["id"],
                                  kind="etl", label="ETL")
                     for spec in specs for source in spec["inputs"] if source in ids]
    return SchemaMetadata(generated_at=now(), source="catalog", source_label="Verdict Archive · file lake",
                          entities=entities, relationships=relationships)


def database_metadata(engine: Engine, schema_layers: dict[str, Layer] | None = None,
                      schemas: list[str] | None = None, etl: list[dict] | None = None) -> SchemaMetadata:
    inspector = inspect(engine)
    layers = schema_layers or {"bronze": "bronze", "silver": "silver", "gold": "gold"}
    available = inspector.get_schema_names()
    selected = schemas if schemas is not None else [s for s in available if s in layers]
    if not selected:
        selected = [inspector.default_schema_name]
    if not set(selected) <= set(available):
        raise ValueError("configured schema does not exist")
    entities, relationships = [], []
    for schema in selected:
        views = inspector.get_view_names(schema=schema)
        for name in sorted(set(inspector.get_table_names(schema=schema)) | set(views)):
            entity_id = f"{schema}.{name}"
            pk = set(inspector.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])
            columns = [Column(name=c["name"], data_type=str(c["type"]), nullable=c.get("nullable"),
                              primary_key=c["name"] in pk, description=c.get("comment"))
                       for c in inspector.get_columns(name, schema=schema)]
            entities.append(Entity(id=entity_id, name=name, schema_name=schema,
                                   layer=layers.get(schema, "unassigned"), kind="view" if name in views else "table",
                                   columns=columns, column_status="declared", location=entity_id))
            for i, fk in enumerate(inspector.get_foreign_keys(name, schema=schema)):
                relationships.append(Relationship(
                    id=f"fk:{entity_id}:{i}", source=entity_id,
                    target=f"{fk.get('referred_schema') or schema}.{fk['referred_table']}", kind="foreign_key",
                    source_columns=fk["constrained_columns"], target_columns=fk["referred_columns"],
                    label=fk.get("name") or "FK",
                ))
    # Do not silently discard cross-schema FKs: include referenced schemas in METADATA_SCHEMAS.
    relationships.extend(Relationship.model_validate(r) for r in (etl or []))
    return SchemaMetadata(generated_at=now(), source="database", source_label=f"{engine.dialect.name} · reflected schema",
                          entities=entities, relationships=relationships)


def write_snapshot(check: bool = False) -> int:
    from metadata_api import app

    doc = catalog_metadata().model_dump()
    previous = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else None
    if previous and {k: v for k, v in doc.items() if k != "generated_at"} == {
        k: v for k, v in previous.items() if k != "generated_at"
    }:
        doc["generated_at"] = previous["generated_at"]
    outputs = {OUTPUT: doc, OUTPUT.with_name("schema-model.json"): SchemaMetadata.model_json_schema(),
               OUTPUT.with_name("openapi.json"): app.openapi()}
    if check:
        return int(any(not path.exists() or json.loads(path.read_text(encoding="utf-8")) != body
                       for path, body in outputs.items()))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    for path, body in outputs.items():
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export real catalog metadata for the static graph preview")
    parser.add_argument("--check", action="store_true")
    raise SystemExit(write_snapshot(parser.parse_args().check))
