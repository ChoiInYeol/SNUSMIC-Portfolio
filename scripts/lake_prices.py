# -*- coding: utf-8 -*-
"""Bronze raw price capture and the silver price writer.

Bronze: every vendor response is recorded as fetched (before any cleaning) and written to
`data/bronze/prices/{vendor}/ingest_date=YYYY-MM-DD/{run_id}-{seq:05d}.csv.gz`, with one line per
fetch appended to `data/bronze/prices/_ingest.jsonl`. Bronze is never behind silver: every
`write_silver_prices` call first flushes the process recorder, a SIGTERM flushes before exiting, and
producers call `flush_bronze()` at the end of `main()` so a failed flush exits non-zero (atexit is
only a last resort). Files are published by hard link from a temp file, so a bronze file is either
complete or absent and is never overwritten; log blocks are single O_APPEND writes.

Silver: `write_silver_prices` is the only writer of `data/silver/prices/{symbol}.csv`. It standardises
(date index named `date`, lower-case OHLCV), de-duplicates by date (last wins), sorts, validates,
quarantines failing rows to `data/silver/_rejects/{symbol}.jsonl` and replaces the file atomically.
Replays from bronze live in scripts/rebuild_silver_prices.py.
"""
from __future__ import annotations

import atexit
import datetime as dt
import gzip
import hashlib
import json
import os
import signal
import tempfile
import threading
from importlib import metadata
from pathlib import Path

import pandas as pd

from lake import BRONZE_PRICE_LOG, BRONZE_PRICES, SILVER_PRICES, SILVER_REJECTS

PRICE_COLUMNS = ("open", "high", "low", "close", "volume")
# Index header for new writes. Files written before the medallion layout carry `날짜` (pykrx) or
# `Date` (yfinance); every reader uses `index_col=0`, so all three read the same.
DATE_INDEX = "date"


# ── Durable file primitives ─────────────────────────────────────────────────

def _lock(fd: int, release: bool = False) -> None:
    """Exclusive advisory lock on the whole file (POSIX flock) or on its first byte (Windows).

    Windows emulates O_APPEND as seek-then-write, so concurrent appenders interleave without a lock.
    """
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)  # msvcrt locks from the current position
        if release:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return
        for _ in range(60):  # each LK_LOCK attempt already retries for ~10 s
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError:
                continue
        raise TimeoutError("could not lock append target")
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_UN if release else fcntl.LOCK_EX)


def append_block(path: Path, data: bytes) -> None:
    """Append `data` as one locked O_APPEND write, so concurrent writers never interleave blocks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        _lock(fd)
        try:
            if os.write(fd, data) != len(data):
                raise OSError(f"short append to {path}")
            os.fsync(fd)
        finally:
            _lock(fd, release=True)
    finally:
        os.close(fd)


def publish_new(path: Path, data: bytes) -> None:
    """Create `path` with `data` atomically; FileExistsError if it exists (never overwrites)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp, path)  # atomic, and fails instead of replacing an existing file
    finally:
        os.unlink(tmp)


def replace_atomic(path: Path, write) -> None:
    """Write via `write(fileobj)` to a unique temp file in the same directory, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            write(fh)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ── Bronze ──────────────────────────────────────────────────────────────────

def _library_version(library: str) -> str | None:
    try:
        return metadata.version(library)
    except metadata.PackageNotFoundError:
        return None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


class BronzePriceRecorder:
    """Buffers raw vendor frames and writes them out on flush, append-only."""

    def __init__(self, root: Path = BRONZE_PRICES, log_path: Path | None = None) -> None:
        self.root = root
        self.log_path = log_path or root / BRONZE_PRICE_LOG.name
        now = dt.datetime.now(dt.timezone.utc)
        self.run_id = f"{now:%Y%m%dT%H%M%S%fZ}-{os.getpid()}"
        self.ingest_date = now.date().isoformat()
        self._frames: dict[str, list[pd.DataFrame]] = {}
        self._log: list[dict] = []
        self._seq = 0
        self._lock = threading.Lock()

    @property
    def pending(self) -> bool:
        return bool(self._log)

    def record(self, vendor: str, symbol: str, raw: pd.DataFrame, *, library: str, request: dict,
               fetched_at: str | None = None) -> None:
        """Keep `raw` exactly as the vendor returned it. `symbol` is the silver dataset key."""
        frame = raw.copy()
        if isinstance(frame.columns, pd.MultiIndex):  # yfinance single-ticker download: (field, ticker)
            frame.columns = [str(c[0]) for c in frame.columns]
        payload = frame.to_csv().encode("utf-8")
        fetched_at = fetched_at or _now()
        if not frame.empty:
            frame = frame.reset_index()
            frame.insert(0, "symbol", symbol)
            frame["fetched_at"] = fetched_at
        with self._lock:
            if not frame.empty:
                self._frames.setdefault(vendor, []).append(frame)
            self._log.append({
                "vendor": vendor,
                "symbol": symbol,
                "request": request,
                "fetched_at": fetched_at,
                "rows": int(len(raw)),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "library": library,
                "library_version": _library_version(library),
                "run_id": self.run_id,
            })

    def flush(self) -> list[Path]:
        """Publish buffered frames, then append their log lines. Nothing buffered → nothing written.

        File names carry a zero-padded flush sequence, so name order is write order within a run.
        """
        with self._lock:
            if not self._log:
                return []
            # Reserve the name first: a flush retried after a failure never reuses a published name.
            stem = f"{self.run_id}-{self._seq:05d}"
            self._seq += 1
            written: list[Path] = []
            for vendor in sorted(self._frames):
                path = self.root / vendor / f"ingest_date={self.ingest_date}" / f"{stem}.csv.gz"
                body = pd.concat(self._frames[vendor], ignore_index=True).to_csv(index=False).encode("utf-8")
                publish_new(path, gzip.compress(body, mtime=0))
                # Bind each fetch to its published file immediately. If appending the log fails
                # and another fetch arrives before retry, the earlier fetch keeps its own file.
                for entry in self._log:
                    if entry["vendor"] == vendor and entry["rows"] and "file" not in entry:
                        entry["file"] = path.relative_to(self.root).as_posix()
                del self._frames[vendor]
                written.append(path)
            lines = []
            for line in self._log:
                line.setdefault("file", None)
                lines.append(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
            append_block(self.log_path, "".join(lines).encode("utf-8"))
            self._log.clear()
            return written


_recorder: BronzePriceRecorder | None = None


def _flush_on_sigterm(signum, frame) -> None:
    """Unwind the interrupted code (releasing the recorder lock), then flush on the way out.

    Flushing inside the handler could re-enter a flush the signal interrupted; raising SystemExit lets
    `with` blocks finish and the atexit flush run, and the exit code stays non-zero (128 + signum).
    """
    raise SystemExit(128 + signum)


def bronze_recorder() -> BronzePriceRecorder:
    """Process-wide recorder. Flushed before every silver write, on SIGTERM, and (last resort) at exit."""
    global _recorder
    if _recorder is None:
        _recorder = BronzePriceRecorder()
        atexit.register(_recorder.flush)
        if threading.current_thread() is threading.main_thread() \
                and signal.getsignal(signal.SIGTERM) in (signal.SIG_DFL, None):
            signal.signal(signal.SIGTERM, _flush_on_sigterm)
    return _recorder


def record_raw_prices(vendor: str, symbol: str, raw: pd.DataFrame, *, library: str, request: dict) -> None:
    bronze_recorder().record(vendor, symbol, raw, library=library, request=request)


def flush_bronze() -> list[Path]:
    """Flush the process recorder if one exists. Producers call this at the end of main()."""
    return _recorder.flush() if _recorder is not None else []


# ── Silver ──────────────────────────────────────────────────────────────────

class SilverValidationError(ValueError):
    pass


def standardise_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Date index named `date`, lower-case columns in OHLCV order, de-duplicated (last wins), sorted."""
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    out.index = pd.to_datetime(out.index)
    out.index.name = DATE_INDEX
    out = out[~out.index.duplicated(keep="last")].sort_index()
    ordered = [c for c in PRICE_COLUMNS if c in out.columns]
    return out[ordered + [c for c in out.columns if c not in ordered]]


# Vendor-adjusted prices are rounded per field, so a bar can miss the OHLC envelope by a few units
# (a 2026-10-19 scan of 771 files: 99 files miss at 0.01%, 19 at 0.1%, 2 at 0.5%). Bars with a zero
# open/high/low are trading halts (pykrx reports 0 and carries the close) and are not compared.
OHLC_TOLERANCE = 0.005


def structural_problems(df: pd.DataFrame) -> list[str]:
    """Frame-level violations: these fail the write outright."""
    problems: list[str] = []
    if df.index.has_duplicates:
        problems.append(f"duplicate dates: {int(df.index.duplicated().sum())}")
    if not df.index.is_monotonic_increasing:
        problems.append("dates not increasing")
    if "close" not in df.columns:
        problems.append("missing close column")
    return problems


def row_problems(df: pd.DataFrame) -> pd.Series:
    """Per-row violation reason ('' = valid): NaN or non-positive close, or OHLC outside the envelope."""
    reasons = pd.Series("", index=df.index, dtype=object)
    if "close" not in df.columns:
        return reasons
    close = pd.to_numeric(df["close"], errors="coerce")
    reasons[close.isna()] = "NaN close"
    reasons[close <= 0] = "close <= 0"
    cols = {c: pd.to_numeric(df[c], errors="coerce") for c in ("open", "high", "low") if c in df.columns}
    traded = pd.Series(True, index=df.index)
    for s in cols.values():
        traded &= ~(s == 0)
    open_ = cols.get("open", close)
    if "high" in cols:
        bad = traded & (cols["high"] < pd.concat([close, open_], axis=1).max(axis=1) * (1 - OHLC_TOLERANCE))
        reasons[bad & (reasons == "")] = "high < max(open, close)"
    if "low" in cols:
        bad = traded & (cols["low"] > pd.concat([close, open_], axis=1).min(axis=1) * (1 + OHLC_TOLERANCE))
        reasons[bad & (reasons == "")] = "low > min(open, close)"
    return reasons


def validate_prices(df: pd.DataFrame) -> list[str]:
    """All violations of the silver price contract (empty = valid)."""
    problems = structural_problems(df)
    counts = row_problems(df).loc[lambda s: s != ""].value_counts()
    return problems + [f"{reason}: {int(n)}" for reason, n in counts.sort_index().items()]


def _json_cell(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value.item() if hasattr(value, "item") else value


def quarantine_rows(symbol: str, rows: pd.DataFrame, reasons: pd.Series, rejects_dir: Path) -> Path:
    """Append rejected rows to `{rejects_dir}/{symbol}.jsonl` (one O_APPEND block; rows already logged
    with the same date and reason are skipped)."""
    path = rejects_dir / f"{symbol}.jsonl"
    seen: set[tuple[str, str]] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                seen.add((r["date"], r["reason"]))
    rejected_at = _now()
    block = []
    for (ts, row), reason in zip(rows.iterrows(), reasons):
        key = (ts.strftime("%Y-%m-%d"), reason)
        if key in seen:
            continue
        seen.add(key)
        block.append(json.dumps({"date": key[0], "reason": reason, "rejected_at": rejected_at,
                                 "row": {c: _json_cell(v) for c, v in row.items()}},
                                ensure_ascii=False, sort_keys=True) + "\n")
    if block:
        append_block(path, "".join(block).encode("utf-8"))
    return path


def write_silver_prices(symbol: str, df: pd.DataFrame, *, out_dir: Path = SILVER_PRICES,
                        rejects_dir: Path = SILVER_REJECTS) -> pd.DataFrame:
    """Standardise, validate and atomically replace `{out_dir}/{symbol}.csv`; returns the written frame.

    The process bronze recorder is flushed first, so silver never holds a fetch bronze lacks.
    Structural violations raise SilverValidationError and nothing is written. Rows failing a row rule
    are moved to `{rejects_dir}/{symbol}.jsonl` with their reason and a warning — never dropped silently.
    """
    flush_bronze()
    std = standardise_prices(df)
    problems = structural_problems(std)
    if problems:
        raise SilverValidationError(f"{symbol}: {'; '.join(problems)}")
    reasons = row_problems(std)
    bad = reasons != ""
    if bad.any():
        path = quarantine_rows(symbol, std[bad], reasons[bad], rejects_dir)
        print(f"WARNING silver {symbol}: {int(bad.sum())} row(s) quarantined to {path.name} "
              f"({', '.join(sorted(set(reasons[bad])))})", flush=True)
        std = std[~bad]
    replace_atomic(out_dir / f"{symbol}.csv", lambda fh: std.to_csv(fh))
    return std
