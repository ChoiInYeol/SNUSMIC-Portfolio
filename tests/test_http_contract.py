"""Real HTTP against a production build: /api/v2 status codes, headers and bodies vs openapi.json.

The file-level contract (tests/test_api_v2_contract.py) cannot see what Next.js serves: header-rule
precedence, rewrites, the method guard in src/proxy.ts, redirects. This module starts `next start` from
the existing build (`npm run build` first) on a free port and checks those over the wire.
Set API_HTTP_BASE=http(s)://host[:port] to test an already running server or a deployment instead.
Skipped, with the reason, when there is no build or the build is older than the API serving sources.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import subprocess
import time
import urllib.parse
from pathlib import Path

import pytest

import export_api_v2 as v2
from _contract import openapi

ROOT = v2.ROOT
API = v2.API
OPENAPI = openapi(API / "openapi.json")
SPEC = OPENAPI.spec
INDEX = json.loads(v2.RESOURCE_INDEX.read_text(encoding="utf-8"))
SERVING_SOURCES = [ROOT / "next.config.ts", ROOT / "src" / "proxy.ts", v2.RESOURCE_INDEX,
                   ROOT / "src" / "app" / "api" / "v2" / "[...path]" / "route.ts", API / "openapi.json"]


def _first_example(param_ref: dict) -> str:
    param = OPENAPI.resolve(param_ref)
    return next(iter(param["examples"].values()))["value"]


def _example_uri(path: str, op: dict) -> str:
    """The URI Swagger "Try it out" pre-fills: every parameter's first example."""
    values = {OPENAPI.resolve(p)["name"]: _first_example(p) for p in op.get("parameters", [])}
    return re.sub(r"\{(\w+)\}", lambda m: values[m.group(1)], path)


EXAMPLE_URIS = [(path, _example_uri(path, item["get"])) for path, item in SPEC["paths"].items()]


def _key(uri: str) -> str:
    rest = uri.removeprefix(v2.PREFIX).lstrip("/").removesuffix(".json")
    return rest or "index"


@pytest.mark.parametrize(("path", "uri"), EXAMPLE_URIS, ids=[p for p, _ in EXAMPLE_URIS])
def test_swagger_default_examples_name_published_resources(path: str, uri: str):
    assert _key(uri) in INDEX["resources"], f"{path}: Try-it default {uri} would be 404"


def test_every_parameter_example_is_a_published_id():
    # On paths whose resource depends on the id (trade marks exist only for traded instruments, a revision hash
    # belongs to one strategy) only the default examples must resolve together.
    for path, item in SPEC["paths"].items():
        defaults = {OPENAPI.resolve(p)["name"]: _first_example(p) for p in item["get"].get("parameters", [])}
        for ref in item["get"].get("parameters", []):
            param = OPENAPI.resolve(ref)
            for example in param["examples"].values():
                if path.endswith(("/trade-marks", "/{sha256}")) and example["value"] != defaults[param["name"]]:
                    continue
                values = {**defaults, param["name"]: example["value"]}
                uri = re.sub(r"\{(\w+)\}", lambda m: values[m.group(1)], path)
                assert _key(uri) in INDEX["resources"], f"{path}: example {example['value']} gives 404 {uri}"


def test_spec_offers_a_same_origin_server_for_try_it_out():
    assert SPEC["servers"][0]["url"] == v2.SITE
    assert any(server["url"] == "/" for server in SPEC["servers"])


# ── live server ─────────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base() -> tuple[str, int, bool]:
    if url := os.getenv("API_HTTP_BASE"):
        parsed = urllib.parse.urlsplit(url)
        tls = parsed.scheme == "https"
        yield parsed.hostname, parsed.port or (443 if tls else 80), tls
        return
    project = Path(os.getenv("API_HTTP_PROJECT", ROOT))
    build_id = project / ".next" / "BUILD_ID"
    if not build_id.exists():
        pytest.skip(f"no production build in {project} — run `npm run build`")
    built = build_id.stat().st_mtime
    stale = [p.relative_to(ROOT).as_posix() for p in SERVING_SOURCES if p.stat().st_mtime > built]
    if stale and not os.getenv("API_HTTP_PROJECT"):
        pytest.skip(f"build is older than {', '.join(stale)} — rebuild before the HTTP contract test")
    port = _free_port()
    proc = subprocess.Popen(["node", str(ROOT / "node_modules" / "next" / "dist" / "bin" / "next"), "start",
                             "-H", "127.0.0.1", "-p", str(port)],
                            cwd=project, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                if proc.poll() is not None:
                    pytest.fail("next start exited before listening")
                time.sleep(0.5)
        yield "127.0.0.1", port, False
    finally:
        proc.terminate()
        proc.wait(timeout=30)


def fetch(base: tuple[str, int, bool], method: str, uri: str, headers: dict | None = None):
    host, port, tls = base
    conn = (http.client.HTTPSConnection if tls else http.client.HTTPConnection)(host, port, timeout=30)
    try:
        conn.request(method, uri, headers=headers or {})
        res = conn.getresponse()
        return res.status, {k.lower(): v for k, v in res.getheaders()}, res.read()
    finally:
        conn.close()


def _header_example(ref: dict) -> str:
    return OPENAPI.resolve(ref)["example"]


@pytest.mark.parametrize(("path", "uri"), EXAMPLE_URIS, ids=[p for p, _ in EXAMPLE_URIS])
def test_example_resource_matches_its_spec_response(base, path: str, uri: str):
    ok = SPEC["paths"][path]["get"]["responses"]["200"]
    status, headers, body = fetch(base, "GET", uri)
    assert status == 200, (uri, status, body[:200])
    assert headers["content-type"].startswith("application/json")
    assert headers["cache-control"] == _header_example(ok["headers"]["Cache-Control"])
    assert headers["access-control-allow-origin"] == "*"
    assert headers["x-content-type-options"] == "nosniff"
    payload = json.loads(body)
    assert OPENAPI.errors(payload, ok["content"]["application/json"]["schema"], uri) == []
    if "Link" in ok["headers"]:
        assert headers["link"] == f'<{uri.rsplit("/revisions/", 1)[0]}>; rel="up"'

    etag = headers["etag"]
    h_status, h_headers, h_body = fetch(base, "HEAD", uri)
    assert (h_status, h_body) == (200, b"")
    assert h_headers["etag"] == etag and h_headers["cache-control"] == headers["cache-control"]

    c_status, c_headers, c_body = fetch(base, "GET", uri, {"If-None-Match": etag})
    assert (c_status, c_body) == (304, b"")
    assert c_headers["etag"] == etag


MISSING = {
    "unknown collection": "/api/v2/no-such-resource",
    "unknown item": "/api/v2/strategies/UNKNOWN",
    "future dated snapshot": "/api/v2/signals/2099-01-01",
    "future dated snapshot (.json)": "/api/v2/signals/2099-01-01.json",
    "unknown revision hash": f"/api/v2/strategies/W_allweather_chandelier/revisions/{'0' * 64}",
    "unknown instrument prices": "/api/v2/instruments/kr-000000/prices",
    "encoded slash": "/api/v2/clubs%2Fewha",
    "second URI of the entry point": "/api/v2/index",
    "second URI of the entry point (.json)": "/api/v2/index.json",
}


@pytest.mark.parametrize("uri", MISSING.values(), ids=MISSING.keys())
def test_missing_resource_is_an_uncached_problem(base, uri: str):
    not_found = OPENAPI.resolve(SPEC["paths"][v2.href("strategies", "{strategy_id}")]["get"]["responses"]["404"])
    status, headers, body = fetch(base, "GET", uri)
    assert status == 404
    assert headers["content-type"] == "application/problem+json"
    assert headers["cache-control"] == _header_example(not_found["headers"]["Cache-Control"]) == "no-store"
    assert headers["access-control-allow-origin"] == "*"
    problem = json.loads(body)
    assert OPENAPI.errors(problem, not_found["content"]["application/problem+json"]["schema"], uri) == []
    assert (problem["status"], problem["instance"]) == (404, uri)
    h_status, h_headers, h_body = fetch(base, "HEAD", uri)
    assert (h_status, h_body, h_headers["cache-control"]) == (404, b"", "no-store")


@pytest.mark.parametrize("uri", ["/api/v2/strategies", "/api/v2/no-such-resource",
                                 f"/api/v2/strategies/W_allweather_chandelier/revisions/{'0' * 64}"])
def test_methods_other_than_get_and_head(base, uri: str):
    status, headers, body = fetch(base, "OPTIONS", uri)
    assert (status, body) == (204, b"")
    assert headers["allow"] == "GET, HEAD, OPTIONS" and headers["cache-control"] == "no-store"
    assert headers["access-control-allow-methods"] == "GET, HEAD, OPTIONS"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        status, headers, body = fetch(base, method, uri)
        assert status == 405
        assert headers["allow"] == "GET, HEAD, OPTIONS" and headers["cache-control"] == "no-store"
        assert headers["content-type"] == "application/problem+json"
        assert json.loads(body)["status"] == 405


def test_uri_spellings_and_query_strings_name_the_same_resource(base):
    _, _, plain = fetch(base, "GET", "/api/v2/strategies/D+_chandelier_optuna")
    for uri in ("/api/v2/strategies/D%2B_chandelier_optuna", "/api/v2/strategies/D+_chandelier_optuna.json",
                "/api/v2/strategies/D+_chandelier_optuna?unused=1"):
        status, _, body = fetch(base, "GET", uri)
        assert (status, body) == (200, plain), uri


def test_redirects(base):
    status, headers, _ = fetch(base, "GET", "/api/v2/")
    assert (status, headers["location"]) == (308, "/api/v2")
    iid = next(k for k in INDEX["resources"] if k.endswith("/prices")).split("/")[1]
    status, headers, _ = fetch(base, "GET", f"/prices/{iid}.json")
    assert (status, headers["location"]) == (308, f"/api/v2/instruments/{iid}/prices")


def test_v1_is_deprecated_and_shares_revision_bytes(base):
    status, headers, latest = fetch(base, "GET", "/api/v1/signals/latest.json")
    assert status == 200
    assert headers["deprecation"] == "@1792368000"
    assert headers["link"] == '</api/v2>; rel="successor-version"'
    # The extensionless alias is a rewrite to the same static file: same bytes, same headers.
    alias_status, alias_headers, alias = fetch(base, "GET", "/api/v1/signals/latest")
    assert (alias_status, alias) == (200, latest)
    for name in ("cache-control", "content-type", "etag", "deprecation", "access-control-allow-origin"):
        assert alias_headers.get(name) == headers.get(name), name
    revision = next(k for k in INDEX["resources"] if "/revisions/" in k)
    sid, digest = revision.removeprefix("strategies/").split("/revisions/")
    _, _, v2_bytes = fetch(base, "GET", f"/api/v2/{revision}")
    status, headers, v1_bytes = fetch(base, "GET", f"/api/v1/strategy-details/{sid}/{digest}.json")
    assert (status, v1_bytes) == (200, v2_bytes)
    assert headers["deprecation"] == "@1792368000"

    for missing in (f"/api/v1/strategy-details/{sid}/{'0' * 64}.json", "/api/v1/signals/2099-01-01.json"):
        status, headers, _ = fetch(base, "GET", missing)
        assert (status, headers["cache-control"]) == (404, "no-store"), missing
    for method, expected in (("OPTIONS", 204), ("POST", 405), ("DELETE", 405)):
        status, headers, _ = fetch(base, method, "/api/v1/signals/latest.json")
        assert (status, headers["allow"]) == (expected, "GET, HEAD, OPTIONS"), method
