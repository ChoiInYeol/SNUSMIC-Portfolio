"""Medallion lake: silver price validation, bronze recorder, legacy paths, catalog determinism."""
from __future__ import annotations

import gzip
import json
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_catalog  # noqa: E402
import lake  # noqa: E402
import lake_prices  # noqa: E402
import rebuild_silver_prices  # noqa: E402
from lake_prices import (  # noqa: E402
    BronzePriceRecorder,
    SilverValidationError,
    append_block,
    publish_new,
    replace_atomic,
    validate_prices,
    write_silver_prices,
)


def ohlcv(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close"]).set_index("date")
    df.index = pd.to_datetime(df.index)
    df["volume"] = 1000
    return df


GOOD = [("2024-01-02", 100, 110, 95, 105), ("2024-01-03", 105, 108, 101, 102), ("2024-01-04", 102, 104, 99, 103)]


def leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.rglob("*.tmp"))


# ── silver validation ───────────────────────────────────────────────────────

def test_valid_frame_passes():
    assert validate_prices(ohlcv(GOOD)) == []


def test_duplicate_and_unsorted_dates_are_reported():
    df = ohlcv(GOOD + [("2024-01-03", 105, 108, 101, 102)])
    problems = validate_prices(df)
    assert any(p.startswith("duplicate dates") for p in problems)
    assert "dates not increasing" in problems


def test_writer_dedupes_by_date_keeping_last_and_sorts(tmp_path):
    df = ohlcv([GOOD[2], GOOD[0], ("2024-01-02", 100, 112, 95, 107), GOOD[1]])
    out = write_silver_prices("KR_000001", df, out_dir=tmp_path, rejects_dir=tmp_path / "rej")
    assert list(out.index.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert out.loc["2024-01-02", "close"] == 107  # last write wins
    written = (tmp_path / "KR_000001.csv").read_text(encoding="utf-8").splitlines()
    assert written[0] == "date,open,high,low,close,volume"
    assert not (tmp_path / "rej").exists()
    assert leftovers(tmp_path) == []


def test_missing_close_column_fails_the_write(tmp_path):
    df = ohlcv(GOOD).drop(columns="close")
    with pytest.raises(SilverValidationError, match="missing close column"):
        write_silver_prices("KR_000001", df, out_dir=tmp_path, rejects_dir=tmp_path / "rej")
    assert not (tmp_path / "KR_000001.csv").exists()


def _rejects(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_bad_rows_are_quarantined_not_dropped_silently(tmp_path, capsys):
    df = ohlcv(GOOD + [("2024-01-05", 100, 90, 95, 100),               # high below open/close
                       ("2024-01-08", 100, 101, 99, float("nan")),      # no close
                       ("2024-01-09", 100, 101, 99, -1)])               # non-positive close
    problems = validate_prices(df)
    assert {"high < max(open, close): 1", "NaN close: 1", "close <= 0: 1"} <= set(problems)
    out = write_silver_prices("KR_000001", df, out_dir=tmp_path, rejects_dir=tmp_path / "rej")
    assert len(out) == 3  # the refresh continues with the valid rows
    rejects = _rejects(tmp_path / "rej" / "KR_000001.jsonl")
    assert sorted(r["reason"] for r in rejects) == ["NaN close", "close <= 0", "high < max(open, close)"]
    assert next(r for r in rejects if r["reason"] == "close <= 0")["row"]["close"] == -1
    assert "quarantined" in capsys.readouterr().out
    # quarantining the same rows again does not duplicate them
    write_silver_prices("KR_000001", df, out_dir=tmp_path, rejects_dir=tmp_path / "rej")
    assert len(_rejects(tmp_path / "rej" / "KR_000001.jsonl")) == 3


def test_adjustment_rounding_and_trading_halts_are_tolerated():
    df = ohlcv([("2024-01-02", 64698, 65963, 62981, 65964),  # pykrx adjusted: high 1 won under close
                ("2024-01-03", 0, 0, 0, 35850)])              # halt: OHLC reported as 0
    assert validate_prices(df) == []


# ── atomic / concurrent writes ──────────────────────────────────────────────

def test_silver_replace_is_atomic_and_keeps_the_old_file_on_failure(tmp_path):
    target = tmp_path / "KR_000001.csv"
    target.write_text("old", encoding="utf-8")

    def boom(fh):
        fh.write("partial")
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        replace_atomic(target, boom)
    assert target.read_text(encoding="utf-8") == "old"
    assert leftovers(tmp_path) == []


def test_publish_new_never_overwrites_and_leaves_no_temp(tmp_path):
    target = tmp_path / "v" / "a.csv.gz"
    publish_new(target, b"first")
    with pytest.raises(FileExistsError):
        publish_new(target, b"second")
    assert target.read_bytes() == b"first"
    assert leftovers(tmp_path) == []


def test_concurrent_appends_never_interleave(tmp_path):
    log = tmp_path / "log.jsonl"
    child = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(ROOT / "scripts")!r})
        from lake_prices import append_block
        from pathlib import Path
        writer = sys.argv[1]
        for i in range(150):
            block = "".join(json.dumps({{"w": writer, "i": i, "k": k, "pad": "x" * 300}}) + "\\n" for k in range(5))
            append_block(Path({str(log)!r}), block.encode())
    """)
    procs = [subprocess.Popen([sys.executable, "-c", child, str(w)]) for w in range(4)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0, 0]
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4 * 150 * 5
    for start in range(0, len(rows), 5):  # each 5-line block stays contiguous and in order
        block = rows[start:start + 5]
        assert {(r["w"], r["i"]) for r in block} == {(block[0]["w"], block[0]["i"])}
        assert [r["k"] for r in block] == list(range(5))


# ── bronze recorder ─────────────────────────────────────────────────────────

def _raw_pykrx(close=(105, 102)) -> pd.DataFrame:
    df = pd.DataFrame({"시가": [100, 105], "고가": [110, 108], "저가": [95, 101], "종가": list(close),
                       "거래량": [1000, 1200]}, index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
    df.index.name = "날짜"
    return df


def test_recorder_appends_log_and_publishes_gz(tmp_path):
    first = BronzePriceRecorder(root=tmp_path)
    first.record("pykrx_stock", "KR_000001", _raw_pykrx(), library="pykrx", request={"ticker": "000001"})
    first.record("pykrx_stock", "KR_000002", _raw_pykrx().iloc[:0], library="pykrx", request={"ticker": "000002"})
    [gz] = first.flush()
    assert gz.name == f"{first.run_id}-00000.csv.gz"
    log_before = (tmp_path / "_ingest.jsonl").read_text(encoding="utf-8")
    lines = [json.loads(line) for line in log_before.splitlines()]
    assert [(r["symbol"], r["rows"]) for r in lines] == [("KR_000001", 2), ("KR_000002", 0)]
    assert lines[0]["file"] == gz.relative_to(tmp_path).as_posix() and lines[1]["file"] is None
    assert {"vendor", "request", "fetched_at", "sha256", "library", "library_version", "run_id"} <= set(lines[0])
    with gzip.open(gz, "rt", encoding="utf-8") as fh:
        stored = pd.read_csv(fh)
    assert list(stored.columns) == ["symbol", "날짜", "시가", "고가", "저가", "종가", "거래량", "fetched_at"]

    second = BronzePriceRecorder(root=tmp_path)
    second.record("pykrx_stock", "KR_000001", _raw_pykrx(), library="pykrx", request={"ticker": "000001"})
    second.flush()
    log_after = (tmp_path / "_ingest.jsonl").read_text(encoding="utf-8")
    assert log_after.startswith(log_before) and len(log_after.splitlines()) == 3  # append-only
    assert first.flush() == []  # nothing buffered → nothing written
    assert leftovers(tmp_path) == []


def test_recorder_never_overwrites_an_existing_file(tmp_path):
    rec = BronzePriceRecorder(root=tmp_path)
    target = tmp_path / "yfinance" / f"ingest_date={rec.ingest_date}" / f"{rec.run_id}-00000.csv.gz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"earlier run")
    rec.record("yfinance", "US_AAA", _raw_pykrx(), library="yfinance", request={})
    with pytest.raises(FileExistsError):
        rec.flush()
    assert target.read_bytes() == b"earlier run"
    [retry] = rec.flush()  # the failed name is not reused
    assert retry.name == f"{rec.run_id}-00001.csv.gz"


def test_flushes_sort_in_write_order(tmp_path):
    rec = BronzePriceRecorder(root=tmp_path)
    names = []
    for _ in range(11):
        rec.record("yfinance", "US_AAA", _raw_pykrx(), library="yfinance", request={})
        names += [p.name for p in rec.flush()]
    assert names == sorted(names) and len(set(names)) == 11


@pytest.fixture
def process_recorder(tmp_path, monkeypatch):
    rec = BronzePriceRecorder(root=tmp_path / "bronze")
    monkeypatch.setattr(lake_prices, "_recorder", rec)
    return rec


def test_silver_write_flushes_bronze_first(tmp_path, process_recorder, monkeypatch):
    lake_prices.record_raw_prices("pykrx_stock", "KR_000001", _raw_pykrx(), library="pykrx", request={})
    seen = {}
    real_replace = lake_prices.replace_atomic

    def checking_replace(path, write):
        seen["bronze_files"] = list((tmp_path / "bronze").rglob("*.csv.gz"))
        seen["pending"] = process_recorder.pending
        real_replace(path, write)

    monkeypatch.setattr(lake_prices, "replace_atomic", checking_replace)
    write_silver_prices("KR_000001", ohlcv(GOOD), out_dir=tmp_path / "silver", rejects_dir=tmp_path / "rej")
    assert len(seen["bronze_files"]) == 1 and seen["pending"] is False


def test_failed_bronze_flush_raises(tmp_path, process_recorder):
    process_recorder.log_path = tmp_path / "is-a-directory"
    process_recorder.log_path.mkdir()
    lake_prices.record_raw_prices("pykrx_stock", "KR_000001", _raw_pykrx(), library="pykrx", request={})
    with pytest.raises(OSError):
        lake_prices.flush_bronze()


def test_log_retry_preserves_each_fetch_file_after_a_new_fetch(tmp_path, monkeypatch):
    rec = BronzePriceRecorder(root=tmp_path)
    rec.record("pykrx_stock", "KR_000001", _raw_pykrx(), library="pykrx", request={})
    real_append = lake_prices.append_block

    def fail_append(*args):
        raise OSError("log unavailable")

    monkeypatch.setattr(lake_prices, "append_block", fail_append)
    with pytest.raises(OSError, match="log unavailable"):
        rec.flush()
    rec.record("pykrx_stock", "KR_000002", _raw_pykrx(close=(106, 103)), library="pykrx", request={})
    monkeypatch.setattr(lake_prices, "append_block", real_append)
    rec.flush()
    rows = _rejects(tmp_path / "_ingest.jsonl")
    assert len(rows) == 2 and rows[0]["file"] != rows[1]["file"]
    for row in rows:
        with gzip.open(tmp_path / row["file"], "rt", encoding="utf-8") as fh:
            assert set(pd.read_csv(fh)["symbol"]) == {row["symbol"]}
    assert rec.flush() == []


def test_flush_bronze_without_a_recorder_is_a_no_op(monkeypatch):
    monkeypatch.setattr(lake_prices, "_recorder", None)
    assert lake_prices.flush_bronze() == []


def test_sigterm_flushes_buffered_bronze_and_exits_non_zero(tmp_path):
    child = textwrap.dedent(f"""
        import signal, sys
        sys.path.insert(0, {str(ROOT / "scripts")!r})
        from pathlib import Path
        import pandas as pd
        import lake_prices
        lake_prices._recorder = None
        rec = lake_prices.bronze_recorder()
        rec.root = Path({str(tmp_path)!r}); rec.log_path = rec.root / "_ingest.jsonl"
        df = pd.DataFrame({{"close": [1.0]}}, index=pd.to_datetime(["2024-01-02"]))
        lake_prices.record_raw_prices("yfinance", "US_AAA", df, library="yfinance", request={{}})
        signal.raise_signal(signal.SIGTERM)
        print("not reached")
    """)
    result = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, timeout=120)
    assert result.returncode == 128 + signal.SIGTERM, result.stderr
    assert "not reached" not in result.stdout
    assert len(list(tmp_path.rglob("*.csv.gz"))) == 1
    assert len((tmp_path / "_ingest.jsonl").read_text(encoding="utf-8").splitlines()) == 1


# ── replay ──────────────────────────────────────────────────────────────────

def _replay(root: Path, symbol: str, out: Path) -> list[str]:
    frames = rebuild_silver_prices.replay(root, None)
    write_silver_prices(symbol, rebuild_silver_prices.in_fetch_order(frames[symbol]), out_dir=out,
                        rejects_dir=out / "_rejects")
    return (out / f"{symbol}.csv").read_text(encoding="utf-8").splitlines()


def test_bronze_replays_into_identical_silver(tmp_path):
    rec = BronzePriceRecorder(root=tmp_path / "bronze")
    rec.record("pykrx_stock", "KR_000001", _raw_pykrx(), library="pykrx", request={})
    rec.flush()
    assert _replay(tmp_path / "bronze", "KR_000001", tmp_path / "silver") == [
        "date,open,high,low,close,volume", "2024-01-02,100,110,95,105,1000", "2024-01-03,105,108,101,102,1200"]


def test_replay_orders_by_fetch_time_not_file_name(tmp_path):
    bronze = tmp_path / "bronze"
    rec = BronzePriceRecorder(root=bronze)
    # Flush 00000 holds the NEWER fetch, flush 00001 the older one: names alone would pick the wrong value.
    rec.record("pykrx_stock", "KR_000001", _raw_pykrx(close=(105, 104)), library="pykrx", request={},
               fetched_at="2024-01-03T10:00:00.000000+00:00")
    rec.flush()
    rec.record("pykrx_stock", "KR_000001", _raw_pykrx(close=(105, 102)), library="pykrx", request={},
               fetched_at="2024-01-03T09:00:00.000000+00:00")
    rec.flush()
    # A legacy name without a sequence suffix sorts after "<run>-00000"; its fetch is the oldest.
    legacy = BronzePriceRecorder(root=bronze)
    legacy.run_id = "00000000T000000000000Z-0"
    legacy.record("legacy_import", "KR_000001",
                  _raw_pykrx(close=(99, 99)).set_axis(["open", "high", "low", "close", "volume"], axis=1),
                  library="pandas", request={}, fetched_at="2024-01-01T00:00:00+00:00")
    legacy.flush()
    lines = _replay(bronze, "KR_000001", tmp_path / "silver")
    assert lines[-1] == "2024-01-03,105,108,101,104,1200"


# ── legacy paths ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("legacy", "current"), [
    ("data/pdfs/ewha/a b.pdf", "data/bronze/reports/pdfs/ewha/a b.pdf"),
    ("data/pdfs/2020-12-05_x.pdf", "data/bronze/reports/pdfs/2020-12-05_x.pdf"),
    (".\\data\\markdown\\yig\\x.md", "data/silver/reports/markdown/yig/x.md"),
    ("./data/prices/KR_005930.csv", "data/silver/prices/KR_005930.csv"),
    ("data/silver/reports/markdown/yig/x.md", "data/silver/reports/markdown/yig/x.md"),
    ("src/data/stats.json", "src/data/stats.json"),
])
def test_legacy_paths_resolve_to_the_medallion_layout(legacy, current):
    assert lake.current_rel(legacy) == current
    assert lake.resolve_data_path(legacy) == lake.ROOT / current


# ── catalog ─────────────────────────────────────────────────────────────────

SMALL = {"bronze.report_manifest", "silver.corrections", "gold.health", "serving.strategy_trades_csv",
         "silver.prices"}


@pytest.fixture
def small_catalog(monkeypatch):
    monkeypatch.setattr(build_catalog, "DATASETS",
                        [dict(d, inputs=[i for i in d["inputs"] if i in SMALL])
                         for d in build_catalog.DATASETS if d["id"] in SMALL])


def test_catalog_is_deterministic_and_sorted(small_catalog):
    first, second = build_catalog.render(build_catalog.build()), build_catalog.render(build_catalog.build())
    assert first == second
    ids = [d["id"] for d in json.loads(first)["datasets"]]
    assert ids == sorted(ids) == sorted(SMALL)
    entries = {d["id"]: d for d in json.loads(first)["datasets"]}
    assert entries["silver.prices"]["tracked_in_git"] is False and entries["silver.prices"]["bytes"] is None
    assert entries["bronze.report_manifest"]["tracked_in_git"] is True
    assert entries["bronze.report_manifest"]["rows"] > 0


def test_catalog_check_detects_drift(small_catalog, tmp_path, monkeypatch):
    out = tmp_path / "catalog.json"
    monkeypatch.setattr(sys, "argv", ["build_catalog.py", "--output", str(out)])
    assert build_catalog.main() == 0
    monkeypatch.setattr(sys, "argv", ["build_catalog.py", "--output", str(out), "--check"])
    assert build_catalog.main() == 0
    out.write_text(out.read_text(encoding="utf-8").replace('"gold"', '"silver"'), encoding="utf-8")
    assert build_catalog.main() == 1


def test_full_catalog_inputs_reference_known_datasets():
    ids = {d["id"] for d in build_catalog.DATASETS}
    assert len(ids) == len(build_catalog.DATASETS)
    for d in build_catalog.DATASETS:
        assert set(d["inputs"]) <= ids, d["id"]
        assert d["layer"] in {"bronze", "silver", "gold"}
