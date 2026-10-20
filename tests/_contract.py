"""Shared oracles for the published-artifact contract tests (not collected: no test_ prefix).

- `openapi(path)` loads a spec once per session and validates payloads against it.
  Objects with documented `properties` are closed: an undocumented field is drift.
- `canonical_sha256` is the test-side content-hash oracle, independent of the writers.
- `changed_since_head` compares working files to their committed blobs in one git call.
"""
from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import re
import subprocess
from pathlib import Path

JSON_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
    "integer": int, "number": (int, float),
}


class OpenApi:
    def __init__(self, spec: dict):
        self.spec = spec
        self.components = spec["components"]

    def resolve(self, node: dict) -> dict:
        while "$ref" in node:
            section, name = node["$ref"].removeprefix("#/components/").split("/")
            node = self.components[section][name]
        return node

    def schema_errors(self, value: object, name: str, where: str) -> list[str]:
        return self.errors(value, {"$ref": f"#/components/schemas/{name}"}, where)

    def errors(self, value: object, schema: dict, where: str) -> list[str]:
        schema = self.resolve(schema)
        if "anyOf" in schema:
            if any(not self.errors(value, option, where) for option in schema["anyOf"]):
                return []
            return [f"{where}: matches no anyOf option"]
        if "const" in schema and value != schema["const"]:
            return [f"{where}: {value!r} != const {schema['const']!r}"]
        types = schema.get("type")
        if types is not None:
            types = [types] if isinstance(types, str) else types
            ok = any(isinstance(value, JSON_TYPES[t]) and not (t in ("integer", "number") and isinstance(value, bool))
                     for t in types)
            if not ok:
                return [f"{where}: expected {types}, got {type(value).__name__}"]
        if "enum" in schema and value not in schema["enum"]:
            return [f"{where}: {value!r} not in {schema['enum']}"]
        if isinstance(value, str):
            if "pattern" in schema and not re.search(schema["pattern"], value):
                return [f"{where}: {value!r} does not match {schema['pattern']}"]
            try:
                if schema.get("format") == "date":
                    dt.date.fromisoformat(value)
                elif schema.get("format") == "date-time":
                    dt.datetime.fromisoformat(value)
            except ValueError:
                return [f"{where}: {value!r} is not a {schema['format']}"]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value < schema.get("minimum", value) or value > schema.get("maximum", value):
                return [f"{where}: {value} out of range"]
        out: list[str] = []
        if isinstance(value, dict):
            props = schema.get("properties", {})
            out += [f"{where}: missing required {k}" for k in schema.get("required", []) if k not in value]
            extra = schema.get("additionalProperties")
            for key, item in value.items():
                if key in props:
                    out += self.errors(item, props[key], f"{where}.{key}")
                elif isinstance(extra, dict):
                    out += self.errors(item, extra, f"{where}.{key}")
                elif extra is False or (props and extra is not True):
                    out.append(f"{where}: undocumented field {key}")
        if isinstance(value, list) and "items" in schema:
            for i, item in enumerate(value):
                out += self.errors(item, schema["items"], f"{where}[{i}]")
                if len(out) > 20:
                    break
        return out


@functools.cache
def openapi(path: Path) -> OpenApi:
    return OpenApi(json.loads(path.read_text(encoding="utf-8")))


def canonical_sha256(body: dict) -> str:
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def committed_blobs(paths: list[Path], root: Path) -> dict[Path, str]:
    """HEAD blob id of each path that is committed, from a single `git ls-tree`."""
    if not paths:
        return {}
    dirs = sorted({p.parent.relative_to(root).as_posix() for p in paths})
    listing = subprocess.run(["git", "ls-tree", "-r", "-z", "HEAD", "--", *dirs],
                             cwd=root, capture_output=True, check=True).stdout.decode()
    blobs = {}
    for entry in filter(None, listing.split("\0")):
        meta, rel = entry.split("\t", 1)
        blobs[rel] = meta.split()[2]
    return {p: blobs[rel] for p in paths if (rel := p.relative_to(root).as_posix()) in blobs}


def changed_since_head(paths: list[Path], root: Path) -> list[Path]:
    """Committed files whose bytes differ from their HEAD blob (same bytes `git show HEAD:path` prints)."""
    changed = []
    for path, blob in committed_blobs(paths, root).items():
        data = path.read_bytes()
        if hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() != blob:
            changed.append(path)
    return changed
