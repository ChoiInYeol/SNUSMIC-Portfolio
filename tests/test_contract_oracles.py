"""The shared oracles in tests/_contract.py must reject what the contract tests rely on them to reject.

Every published-artifact contract test passes when an oracle returns "no problems", so each oracle
needs its own negative cases: a validator that accepted everything would turn ~2,300 checks green.
"""
from __future__ import annotations

import subprocess

import pytest

from _contract import OpenApi, canonical_sha256, changed_since_head

SPEC = OpenApi({"components": {
    "schemas": {
        "Doc": {
            "type": "object",
            "required": ["id", "day"],
            "properties": {
                "id": {"type": "string", "pattern": "^[a-z]+$"},
                "day": {"type": "string", "format": "date"},
                "at": {"type": "string", "format": "date-time"},
                "count": {"type": "integer", "minimum": 0, "maximum": 10},
                "kind": {"enum": ["a", "b"]},
                "version": {"const": "2.0"},
                "ref": {"$ref": "#/components/schemas/Ref"},
                "either": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "items": {"type": "array", "items": {"type": "number"}},
                "extra": {"type": "object", "additionalProperties": {"type": "boolean"}},
            },
        },
        "Ref": {"type": "object", "properties": {"x": {"type": "number"}}},
        "Sealed": {"type": "object", "additionalProperties": False},
    },
}})
VALID = {"id": "abc", "day": "2026-10-20"}

REJECTED = {
    "undocumented-field": ({**VALID, "surprise": 1}, "Doc", "undocumented field surprise"),
    "undocumented-field-in-ref": ({**VALID, "ref": {"y": 1}}, "Doc", "undocumented field y"),
    "sealed-object": ({"a": 1}, "Sealed", "undocumented field a"),
    "missing-required": ({"id": "abc"}, "Doc", "missing required day"),
    "wrong-type": ({**VALID, "id": 3}, "Doc", "expected ['string']"),
    "bool-is-not-integer": ({**VALID, "count": True}, "Doc", "expected ['integer']"),
    "out-of-range": ({**VALID, "count": 11}, "Doc", "out of range"),
    "enum": ({**VALID, "kind": "c"}, "Doc", "not in"),
    "const": ({**VALID, "version": "1.0"}, "Doc", "!= const"),
    "pattern": ({**VALID, "id": "ABC"}, "Doc", "does not match"),
    "date": ({**VALID, "day": "2026-02-30"}, "Doc", "is not a date"),
    "date-time": ({**VALID, "at": "yesterday"}, "Doc", "is not a date-time"),
    "anyOf": ({**VALID, "either": 1}, "Doc", "matches no anyOf option"),
    "array-item": ({**VALID, "items": [1, "x"]}, "Doc", "items[1]"),
    "additionalProperties-schema": ({**VALID, "extra": {"k": "yes"}}, "Doc", "extra.k"),
}


@pytest.mark.parametrize("value,schema,message", REJECTED.values(), ids=REJECTED.keys())
def test_openapi_oracle_rejects(value, schema, message):
    errors = SPEC.schema_errors(value, schema, "doc")
    assert errors and any(message in e for e in errors), errors


def test_openapi_oracle_accepts_a_fully_valid_document():
    doc = {**VALID, "at": "2026-10-20T09:00:00+09:00", "count": 0, "kind": "a", "version": "2.0",
           "ref": {"x": 1.5}, "either": None, "items": [1, 2.5], "extra": {"k": True}}
    assert SPEC.schema_errors(doc, "Doc", "doc") == []


def test_canonical_hash_ignores_key_order_and_rejects_nan():
    assert canonical_sha256({"b": 1, "a": "é"}) == canonical_sha256({"a": "é", "b": 1})
    assert canonical_sha256({"a": 1}) != canonical_sha256({"a": 1.0})
    with pytest.raises(ValueError):
        canonical_sha256({"a": float("nan")})


def test_append_only_oracle_flags_edited_bytes_and_skips_uncommitted(tmp_path):
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false"]
    frozen, edited, new = (tmp_path / "s" / name for name in ("a.json", "b.json", "c.json"))
    frozen.parent.mkdir()
    frozen.write_bytes(b'{"x": 1}\n')
    edited.write_bytes(b'{"x": 2}\n')
    subprocess.run([*git, "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run([*git, "add", "."], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    edited.write_bytes(b'{"x": 3}\n')
    new.write_bytes(b"{}")
    assert changed_since_head([frozen, edited, new], tmp_path) == [edited]
