"""Full-text report search: golden queries, measured recall/precision, and evidence provenance.

The golden judgments live in tests/fixtures/search-golden.json and are shared with
tests/report-search.test.cjs, which runs the same queries through the TypeScript engine the web uses.
Recall/precision thresholds are regression floors set just under the measured values — they say
nothing about completeness of the corpus (aliases cover only companies the archive itself covers).
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest

import build_fulltext_index as ft
import lake

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "search-golden.json").read_text(encoding="utf-8"))
QUERIES = {q["id"]: q for q in GOLDEN["queries"]}


@pytest.fixture(scope="module")
def index():
    return ft.Index()


def slugs(index, result):
    return [index.manifest["stocks"][g["stock"]]["slug"] for g in result["stocks"]]


def measure(index, q):
    """(recall@k, precision@k) of the related-stock list against the frozen judgments."""
    got = slugs(index, index.search(q["q"]))
    relevant, acceptable = set(q["relevant_stocks"]), set(q["relevant_stocks"]) | set(q["acceptable_stocks"])
    recall = len(relevant & set(got[:q["recall_at"]])) / len(relevant)
    precision = len(acceptable & set(got[:q["precision_at"]])) / q["precision_at"]
    return recall, precision


def test_robot_topic_recall_and_precision(index):
    q = QUERIES["robot_topic"]
    recall, precision = measure(index, q)
    print(f"\nrobot topic: recall@{q['recall_at']}={recall:.2f} precision@{q['precision_at']}={precision:.2f}")
    assert slugs(index, index.search(q["q"]))[0] == q["top1_stock"]
    assert recall >= q["min_recall"]
    assert precision >= q["min_precision"]


@pytest.mark.parametrize("qid", [k for k in QUERIES if k != "robot_topic"])
def test_golden_query(index, qid):
    q = QUERIES[qid]
    res = index.search(q["q"])
    stocks = index.manifest["stocks"]
    got = slugs(index, res)
    if q.get("empty"):
        assert res["reports"] == [] and res["stocks"] == []
        return
    if "plan_stock" in q:
        assert stocks[res["plan"]["stock"]]["slug"] == q["plan_stock"]
    if "top1_stock" in q:
        assert got[0] == q["top1_stock"]
    for slug in q.get("stocks_in_top", []):
        assert slug in got[:q["top_n"]], f"{slug} not in top {q['top_n']}: {got[:q['top_n']]}"
    if q.get("primary_reports_first"):
        target = res["plan"]["stock"]
        flags = [index.manifest["reports"][d]["primary"] == target for d, _ in res["reports"]]
        assert flags[0] and flags == sorted(flags, reverse=True), "a mention-only report outranked a primary report"
    if "passing_groups_below" in q:
        anchor = got.index(q["passing_groups_below"])
        passing = [i for i, g in enumerate(res["stocks"]) if g["kinds"].most_common(1)[0][0] == "passing"]
        assert all(i > anchor for i in passing)


def test_filters_never_leak_reports(index):
    res = index.search("로봇", clubs={"yig"}, date_from="2025-01-01", collected_by="2026-09-01")
    reports = index.manifest["reports"]
    assert res["reports"]
    for d, _ in res["reports"]:
        r = reports[d]
        assert r["club"] == "yig" and r["date"] >= "2025-01-01" and r["first_seen"] <= "2026-09-01"


# ── index contract ─────────────────────────────────────────────────────────────

def test_manifest_contract(index):
    m = index.manifest
    assert m["schema_version"] == ft.SCHEMA_VERSION
    assert m["params"]["shards"] == ft.SHARDS and m["stats"]["reports"] == len(m["reports"])
    assert len({r["id"] for r in m["reports"]}) == len(m["reports"])
    for r in m["reports"]:
        assert r["id"] == ft.report_id(r["path"])
        assert (lake.ROOT / r["path"]).is_file(), r["path"]
        if r["primary"] is not None:
            assert r["primary_conf"] in ft.CONFS
        assert r["primary"] not in {m_[0] for m_ in r["mentions"]}, "primary subject listed as a mention"
    shard_files = sorted((ft.OUT / "terms").glob("*.json"))
    assert len(shard_files) == ft.SHARDS
    sample = json.loads(shard_files[7].read_text(encoding="utf-8"))
    assert sample["version"] == m["shard_versions"][7] == ft._shard_version(sample["terms"])
    assert all(ft.shard_of(t) == 7 for t in sample["terms"])


def _source_tokens(lines: list[str], start: int, end: int) -> set[str]:
    return set(re.findall(r"[가-힣]+|[A-Za-z]+", "\n".join(lines[start - 1:end])))


def test_evidence_text_comes_from_the_cited_source_lines(index):
    """Every word of every quoted chunk occurs in the transcript lines the chunk cites (sampled reports)."""
    for r in index.manifest["reports"][::25]:
        pack = json.loads(gzip.decompress((ft.OUT / "reports" / f"{r['id']}.json.gz").read_bytes()))
        text = (lake.ROOT / r["path"]).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert pack["file_sha256"] == ft.hashlib.sha256(text.encode()).hexdigest(), r["path"]
        lines = text.split("\n")
        for para, start, end, digest, quote in pack["chunks"]:
            assert ft.hashlib.sha256(quote.encode()).hexdigest()[:16] == digest
            missing = set(re.findall(r"[가-힣]+|[A-Za-z]+", quote)) - _source_tokens(lines, start, end)
            # a window cut at CHUNK_MAX may split one word at each edge
            assert len(missing) <= 2, (r["path"], start, sorted(missing)[:5])
        for slug, kind, conf, count, evidence in pack["mentions"]:
            assert kind in ft.KINDS[1:] and conf in ft.CONFS and count >= 1
            assert all(0 <= i < len(pack["chunks"]) for i in evidence)


# ── units shared with the TypeScript engine ────────────────────────────────────

def test_postings_round_trip_and_hash_vectors():
    postings = [(0, 1), (1, 31), (40, 7), (100000, 12)]
    assert ft.decode_postings(ft.encode_postings(postings)) == postings
    # Fixed vectors: tests/report-search.test.cjs asserts the same values for the TS shardOf.
    assert [ft.shard_of(t) for t in ("로봇", "휴머", "robot", "감속")] == [84, 65, 223, 102]
    assert ft.terms("협동로봇을 AI2 공급") == ["협동", "동로", "로봇", "봇을", "ai2", "공급"]


def test_mention_boundaries_and_relation_cues():
    finder = ft.MentionFinder({"레이": "kr-1", "SK": "kr-2", "SK하이닉스": "kr-3", "로보티즈": "kr-4"})
    found = lambda text: [slug for _, _, slug in finder.find(text)]
    assert found("레이저 장비와 레이는 다르다") == ["kr-1"]           # 2-syllable name only before a particle
    assert found("SK하이닉스와 SK텔레콤, 그리고 SK.") == ["kr-3", "kr-2"]  # longest match; no Latin+Hangul bleed
    assert found("국내로보티즈") == []                                  # no match inside a longer Hangul word
    kind, conf, ev = ft.classify([(0, "로보티즈를 고객사로 확보", ""), (1, "로보티즈 납품 확대", ""),
                                  (2, "로보티즈 공급망", "")], sector_report=False)
    assert (kind, conf, ev) == ("supplier_customer", "high", [0, 1, 2])
    assert ft.classify([(3, "로보티즈 주가", "")], sector_report=False)[:2] == ("passing", "low")
    assert ft.classify([(3, "경쟁사 로보티즈", "")], sector_report=True)[:2] == ("peer", "medium")


def test_writer_check_detects_drift_and_extra_files(tmp_path):
    out = {"manifest.json": {"schema_version": "1.0"}, "terms/000.json": {"terms": {}},
           "reports/abc.json.gz": {"chunks": [[1, 1, 1, "0" * 16, "본문"]]}}
    root = tmp_path / "fulltext"
    assert ft.write(out, root) == 3 and ft.stale(out, root) == []
    assert ft.write(out, root) == 0                                   # unchanged files are not rewritten
    assert gzip.decompress((root / "reports/abc.json.gz").read_bytes()).decode().startswith('{"chunks"')
    (root / "terms/000.json").write_text('{"terms":{"x":"1"}}\n', encoding="utf-8")
    (root / "terms/999.json").write_text("{}\n", encoding="utf-8")
    assert sorted(ft.stale(out, root)) == ["terms/000.json", "terms/999.json"]
    ft.write(out, root)
    assert ft.stale(out, root) == [] and not (root / "terms/999.json").exists()
