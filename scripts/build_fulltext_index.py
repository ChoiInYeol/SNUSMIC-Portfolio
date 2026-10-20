# -*- coding: utf-8 -*-
"""Gold full-text report search: Silver Markdown → sharded static index under public/research/fulltext/.

Layout (all served statically, loaded on demand by /search — never by the home bundle):
  manifest.json           reports (club, dates, primary stock, relation summary), stocks + query aliases,
                          synonym groups, ranking parameters. schema_version, zod-validated in the client.
  terms/{000..511}.json   term → encoded posting list (report index, quantised BM25 weight).
  reports/{id}.json.gz    one evidence pack per report: chunk texts with line ranges and content hashes,
                          plus every mentioned stock with its relation kind, confidence and evidence chunks.

Terms are Hangul syllable bigrams (robust to particles, compounds and one-syllable typos without a
morphological analyser) and lowercase Latin words. Weights are BM25 (k1=1.2, b=0.75) at report level;
paragraph ranking happens in the browser over the fetched pack, so the index never duplicates the
corpus per term. Evidence is only ever text present in the Silver transcript: chunk text is a cleaned
copy of source lines (Markdown table pipes and HTML comments removed), addressed by line range.
kordoc output has no page markers, so `page` is not recorded; the paragraph ordinal and lines are.

Usage:
    python scripts/build_fulltext_index.py            # write
    python scripts/build_fulltext_index.py --check    # exit 1 if the committed index is stale
    python scripts/build_fulltext_index.py --query 로봇  # run the reference ranking (same as the web)
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lake  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCHEMA_VERSION = "1.0"
OUT = lake.FULLTEXT_INDEX
SHARDS = 512
K1, B = 1.2, 0.75
QMAX = 31                      # BM25 tf part quantised to 1..31 (5 bits) inside the posting varint
CHUNK_TARGET, CHUNK_MAX = 520, 760
BASIS_DRIFT = 0.05             # BM25 statistics stay frozen until the corpus size drifts by more than this
BOILERPLATE_REPORTS = 3        # identical chunk text in this many reports = disclaimer/boilerplate, not evidence
GITHUB_BLOB = "https://github.com/ChoiInYeol/SNUSMIC-Portfolio/blob/main"
SCHOOLS = ("smic", "yig", "star", "kuvic", "ewha", "voera")

KINDS = ("primary", "supplier_customer", "peer", "sector", "passing")
CONFS = ("high", "medium", "low")
# Relation cue words looked up in the sentence around a company mention. They describe the relation,
# never a topic: which companies are related always comes from the mention in the text.
CUES = {
    "supplier_customer": re.compile(r"고객사|납품|공급사|공급업체|공급처|공급망|공급하|공급받|수주|매출처|협력사|벤더|밴더|발주"
                                    r"|customer|supplier|vendor"),
    "peer": re.compile(r"경쟁사|경쟁업체|경쟁|피어|peer|비교기업|비교 기업|유사기업|유사 기업|동종|comparable|competitor"),
    "sector": re.compile(r"산업|섹터|업종|밸류체인|벨류체인|관련주|업체들|기업들|점유율"),
}
JOSA_AFTER_SHORT = ("에서", "으로", "에게", "까지", "부터", "보다", "처럼", "은", "는", "이", "가", "을", "를", "의",
                    "와", "과", "도", "로", "에", "만")
QUERY_JOSA = ("에서", "으로", "은", "는", "을", "를", "의")
US_SUFFIX = re.compile(r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|s\.?a|se|n\.?v|ag|"
                       r"holdings?|group)\.?$", re.I)

COMMENT = re.compile(r"<!--.*?-->", re.S)
TABLE_SEP = re.compile(r"^\|\s*:?-")
LETTER = re.compile(r"[가-힣A-Za-z]")
HANGUL_CHAR = re.compile(r"[가-힣]")
TOKEN = re.compile(r"[가-힣]+|[a-z][a-z0-9]+")
SENTENCE = re.compile(r"(?<=[.!?。])\s+|(?<=다\.)|\s·\s")
B64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"


# ── text normalisation and terms (mirrored by src/lib/report-search.ts) ─────────

def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def terms(text: str) -> list[str]:
    """Hangul bigrams + Latin words of a normalised text (with repeats, for term frequency)."""
    out: list[str] = []
    for tok in TOKEN.findall(normalize(text)):
        if "가" <= tok[0] <= "힣":
            out.extend(tok[i:i + 2] for i in range(len(tok) - 1))
        else:
            out.append(tok)
    return out


def shard_of(term: str) -> int:
    """FNV-1a over UTF-16 code units (what JS strings index), mod SHARDS."""
    h = 0x811C9DC5
    data = term.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h % SHARDS


def encode_postings(postings: list[tuple[int, int]]) -> str:
    """(report index, q) sorted by index → varint base64 of (delta << 5 | q); 5 data bits per char."""
    out, prev = [], 0
    for idx, q in postings:
        v = ((idx - prev) << 5) | q
        prev = idx
        while True:
            chunk = v & 31
            v >>= 5
            out.append(B64[chunk | (32 if v else 0)])
            if not v:
                break
    return "".join(out)


def decode_postings(text: str) -> list[tuple[int, int]]:
    res, prev, v, shift = [], 0, 0, 0
    for ch in text:
        d = B64.index(ch)
        v |= (d & 31) << shift
        shift += 5
        if not d & 32:
            prev += v >> 5
            res.append((prev, v & 31))
            v = shift = 0
    return res


# ── Silver Markdown → chunks ────────────────────────────────────────────────────

def _clean(line: str) -> str:
    return COMMENT.sub(" ", line).replace("<br>", " ")


def _blocks(text: str):
    """Yield (kind, cleaned text, line_start, line_end) per paragraph or table row; lines are 1-based."""
    lines = text.split("\n")
    block: list[tuple[int, str]] = []

    def flush():
        if not block:
            return
        if block[0][1].lstrip().startswith("|"):
            for n, row in block:
                if TABLE_SEP.match(row.strip()):
                    continue
                cells = [c.strip() for c in _clean(row).strip().strip("|").split("|")]
                s = " · ".join(c for c in cells if c)
                letters = len(LETTER.findall(s))
                if letters >= 8 and letters >= 0.4 * len(re.sub(r"\s", "", s)):
                    yield_list.append(("table", s, n, n))
        else:
            s = re.sub(r"\s+", " ", _clean(" ".join(l for _, l in block))).strip()
            if s:
                yield_list.append(("prose", s, block[0][0], block[-1][0]))
        block.clear()

    yield_list: list[tuple[str, str, int, int]] = []
    for n, line in enumerate(lines, start=1):
        if n == 1 and line.startswith("<!-- kordoc"):
            continue
        if line.strip():
            block.append((n, line))
        else:
            flush()
    flush()
    return yield_list


def _windows(s: str) -> list[str]:
    if len(s) <= CHUNK_MAX:
        return [s]
    out, cur = [], ""
    for piece in re.split(r"(?<=[.!?다])\s+|\s+", s):
        if cur and len(cur) + 1 + len(piece) > CHUNK_TARGET:
            out.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}" if cur else piece
        while len(cur) > CHUNK_MAX:
            out.append(cur[:CHUNK_MAX])
            cur = cur[CHUNK_MAX:]
    if cur:
        out.append(cur)
    return out


def chunk_report(text: str) -> list[dict]:
    """Merge consecutive same-kind blocks up to CHUNK_TARGET chars; keep the first block's ordinal."""
    chunks: list[dict] = []
    cur: list[tuple[str, int, int, int]] = []
    kind = None

    def emit():
        if not cur:
            return
        joined = " ".join(c[0] for c in cur)
        for piece in _windows(joined):
            if len(LETTER.findall(piece)) >= 12:
                chunks.append({"text": piece, "para": cur[0][3], "lines": [cur[0][1], cur[-1][2]]})
        cur.clear()

    for ordinal, (k, s, start, end) in enumerate(_blocks(text), start=1):
        size = sum(len(c[0]) for c in cur)
        if cur and (k != kind or size + len(s) > CHUNK_TARGET):
            emit()
        kind = k
        cur.append((s, start, end, ordinal))
    emit()
    for c in chunks:
        c["hash"] = hashlib.sha256(c["text"].encode()).hexdigest()[:16]
    return chunks


# ── metadata (Silver parsed rows, serving records, first-seen) ──────────────────

def _school_of(path: Path) -> str:
    return path.parent.name if path.parent != lake.SILVER_MARKDOWN else "smic"


def _slug(market: str | None, ticker: str | None) -> str | None:
    return f"{market or 'KR'}-{ticker}".lower() if ticker else None


def _strip_us(name: str) -> str:
    prev = None
    while prev != name:
        prev, name = name, US_SUFFIX.sub("", name).strip(" ,.")
    return name


def load_inputs():
    synonyms = json.loads(lake.SEARCH_SYNONYMS.read_text(encoding="utf-8"))
    if synonyms.get("schema_version") != "1.0":
        raise ValueError("search_synonyms.json: unsupported schema_version")
    performance = json.loads((lake.WEB_DATA / "report-performance.json").read_text(encoding="utf-8"))
    first_seen_doc = json.loads((lake.WEB_DATA / "report-first-seen.json").read_text(encoding="utf-8"))
    first_seen = {lake.current_rel(k): v for k, v in first_seen_doc["first_seen"].items()}
    records = {lake.current_rel(r["source_file"]): r for r in performance["records"]}
    with lake.SILVER_REPORT_PARSED.open(encoding="utf-8-sig", newline="") as fh:
        parsed = {lake.current_rel(r["source_file"]): r for r in csv.DictReader(fh)}
    return synonyms, records, parsed, first_seen


def build_stocks(records: dict, parsed: dict, synonyms: dict):
    """slug → {name, market, ticker, page, names(set)} from the archive's own coverage."""
    stocks: dict[str, dict] = {}
    for rec in records.values():
        slug = _slug(rec.get("market"), rec.get("ticker"))
        if not slug:
            continue
        s = stocks.setdefault(slug, {"slug": slug, "name": rec.get("display_name") or rec.get("company") or rec["ticker"],
                                     "market": rec.get("market") or "KR", "ticker": rec["ticker"], "page": True,
                                     "names": set()})
        for n in (rec.get("display_name"), rec.get("company")):
            if n:
                s["names"].add(n.strip())
    for row in parsed.values():
        slug = _slug(row.get("market"), row.get("ticker"))
        if not slug or row.get("report_type") != "company" or (slug in stocks and stocks[slug]["page"]):
            continue
        s = stocks.setdefault(slug, {"slug": slug, "name": row.get("company") or row["ticker"], "market": row.get("market") or "KR",
                                     "ticker": row["ticker"], "page": False, "names": set()})
        if row.get("company"):
            s["names"].add(row["company"].strip())
    for slug, extra in synonyms.get("stock_aliases", {}).items():
        if slug not in stocks:
            raise ValueError(f"search_synonyms.json: stock_aliases names unknown slug {slug!r}")
        stocks[slug]["names"].update(extra)
    for s in stocks.values():
        if s["market"] == "US":
            s["names"] = {n for n in {_strip_us(n) for n in s["names"]} | s["names"] if not HANGUL_CHAR.search(n)}
    return stocks


def _valid_text_alias(name: str, stoplist: set[str]) -> bool:
    if name in stoplist or "_" in name or len(name) > 25 or name.count(" ") >= 3:
        return False
    if HANGUL_CHAR.match(name):
        return len(name) >= 2
    return len(name) >= 2 and bool(re.match(r"[A-Za-z]", name))


class MentionFinder:
    """Find covered companies named in a chunk, with script-aware boundaries and longest match."""

    def __init__(self, aliases: dict[str, str]):
        self.by_head: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for alias, slug in aliases.items():
            self.by_head[alias[:2].lower()].append((alias, slug))
        for v in self.by_head.values():
            v.sort(key=lambda a: -len(a[0]))

    @staticmethod
    def _ok(text: str, start: int, alias: str) -> bool:
        end = start + len(alias)
        before = text[start - 1] if start else " "
        after = text[end] if end < len(text) else " "
        if HANGUL_CHAR.match(alias[0]):
            if HANGUL_CHAR.match(before):
                return False
            if len(alias) >= 4 or not HANGUL_CHAR.match(after):
                return True
            rest = text[end:end + 3]
            return any(rest.startswith(j) and (len(rest) == len(j) or not HANGUL_CHAR.match(rest[len(j)]))
                       for j in JOSA_AFTER_SHORT)
        if re.match(r"[A-Za-z0-9]", before) or HANGUL_CHAR.match(before):
            return False
        last_latin = re.match(r"[A-Za-z0-9.]", alias[-1])
        return not (last_latin and (re.match(r"[A-Za-z0-9]", after) or HANGUL_CHAR.match(after)))

    def find(self, text: str) -> list[tuple[int, int, str]]:
        low = text.lower()
        found = []
        for i in range(len(low) - 1):
            for alias, slug in self.by_head.get(low[i:i + 2], ()):
                cand = text[i:i + len(alias)]
                same = cand == alias if (not HANGUL_CHAR.match(alias[0]) and len(alias) < 5) else cand.lower() == alias.lower()
                if same and self._ok(text, i, alias):
                    found.append((i, i + len(alias), slug))
                    break
        found.sort(key=lambda m: (m[0], -(m[1] - m[0])))
        out, last_end = [], -1
        for m in found:
            if m[0] >= last_end:
                out.append(m)
                last_end = m[1]
        return out


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(". ", 0, start), text.rfind(" · ", 0, start), text.rfind("다 ", 0, start))
    right_candidates = [p for p in (text.find(". ", end), text.find(" · ", end), text.find("다 ", end)) if p != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return text[max(left, 0, start - 60):min(right + 1, end + 60)]


def classify(occurrences: list[tuple[int, str, str]], sector_report: bool):
    """occurrences: (chunk idx, sentence, chunk text). → (kind, confidence, evidence chunk idxs)."""
    sentence_hits: Counter = Counter()
    evidence: dict[str, list[int]] = defaultdict(list)
    for idx, sentence, _ in occurrences:
        low = sentence.lower()
        for kind in ("supplier_customer", "peer", "sector"):
            if CUES[kind].search(low):
                sentence_hits[kind] += 1
                if idx not in evidence[kind]:
                    evidence[kind].append(idx)
                break
    if sentence_hits:
        order = {k: i for i, k in enumerate(("supplier_customer", "peer", "sector"))}
        kind = max(sentence_hits, key=lambda k: (sentence_hits[k], -order[k]))
        conf = "high" if sentence_hits[kind] >= 3 else "medium"
        return kind, conf, evidence[kind][:3]
    idxs = list(dict.fromkeys(i for i, _, _ in occurrences))[:3]
    return ("sector" if sector_report else "passing"), "low", idxs


def report_id(rel_path: str) -> str:
    return hashlib.sha256(rel_path.encode()).hexdigest()[:12]


def previous_manifest(root: Path = OUT) -> dict | None:
    """The committed manifest, whose report order and BM25 basis the next build keeps."""
    try:
        doc = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if doc.get("schema_version") == SCHEMA_VERSION else None


def build(prev: dict | None = None, exclude: frozenset[str] = frozenset()) -> dict[str, dict]:
    """Return {relative output path: JSON document} for every file of the index.

    Stability (keeps refresh commits small): reports already in `prev` keep their order, so their
    posting indexes never move, and new reports are appended. The BM25 basis (document count,
    average length, weight scale) is reused from `prev` while the corpus size stays within
    BASIS_DRIFT, so existing postings keep their quantised weights; an added report mostly appends
    postings. `exclude` drops transcripts by repo path (used to simulate an arriving report).
    """
    synonyms, records, parsed, first_seen = load_inputs()
    stocks = build_stocks(records, parsed, synonyms)
    stoplist = set(synonyms.get("alias_stoplist", []))

    files = sorted(p for p in lake.SILVER_MARKDOWN.rglob("*.md") if lake.rel(p) not in exclude)
    reports = []
    for path in files:
        rel = lake.rel(path)
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        rec, row = records.get(rel), parsed.get(rel) or {}
        primary = _slug(rec.get("market"), rec.get("ticker")) if rec else None
        primary_conf = "high"
        if not primary and row.get("report_type") == "company":
            primary, primary_conf = _slug(row.get("market"), row.get("ticker")), "medium"
        date = (rec or {}).get("report_date") or row.get("report_date") or path.name[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
            date = None
        title = (rec or {}).get("display_name") or row.get("company") or path.stem
        scope = rec["era"] if rec else "unscored"
        reports.append({
            "rel": rel, "id": report_id(rel), "club": _school_of(path), "date": date,
            "first_seen": first_seen.get(rel), "primary": primary, "primary_conf": primary_conf,
            "sector": row.get("report_type") == "sector", "scope": scope, "title": title,
            "sha256": hashlib.sha256(text.encode()).hexdigest(), "chunks": chunk_report(text),
        })
    # Append-only order: previously indexed reports keep their committed position; new ones follow
    # (by first_seen, unknown last, then path). A removed report shifts only the reports after it.
    known = {r["path"]: i for i, r in enumerate((prev or {}).get("reports", []))}
    reports.sort(key=lambda r: (0, known[r["rel"]], "", "") if r["rel"] in known
                 else (1, 0, r["first_seen"] or "9999-99-99", r["rel"]))
    if len({r["id"] for r in reports}) != len(reports):
        raise ValueError("report id collision")

    seen_in = defaultdict(set)
    for i, r in enumerate(reports):
        for c in r["chunks"]:
            seen_in[c["text"]].add(i)
    boiler = {t for t, s in seen_in.items() if len(s) >= BOILERPLATE_REPORTS}
    dropped = 0
    for r in reports:
        before = len(r["chunks"])
        r["chunks"] = [c for c in r["chunks"] if c["text"] not in boiler]
        dropped += before - len(r["chunks"])

    # Text aliases: valid, unambiguous, and actually present in one of the stock's own reports.
    own_text: dict[str, list[str]] = defaultdict(list)
    for r in reports:
        if r["primary"]:
            own_text[r["primary"]].append(" ".join(c["text"] for c in r["chunks"]))
    owners: dict[str, set[str]] = defaultdict(set)
    for slug, s in stocks.items():
        for n in s["names"]:
            if _valid_text_alias(n, stoplist) and any(n.lower() in t.lower() for t in own_text.get(slug, ())):
                owners[n].add(slug)
    text_aliases = {n: next(iter(sl)) for n, sl in owners.items() if len(sl) == 1}
    finder = MentionFinder(text_aliases)

    # Mentions and relation kinds per report.
    for r in reports:
        occ: dict[str, list] = defaultdict(list)
        counts: Counter = Counter()
        for idx, c in enumerate(r["chunks"]):
            for start, end, slug in finder.find(c["text"]):
                if slug == r["primary"]:
                    continue
                counts[slug] += 1
                occ[slug].append((idx, _sentence_around(c["text"], start, end), c["text"]))
        r["mentions"] = []
        for slug in sorted(occ):
            kind, conf, ev = classify(occ[slug], r["sector"])
            r["mentions"].append([slug, kind, conf, counts[slug], ev])

    # Report-level BM25 postings.
    tfs, lengths = [], []
    df: Counter = Counter()
    for r in reports:
        tf = Counter(t for c in r["chunks"] for t in terms(c["text"]))
        tfs.append(tf)
        lengths.append(sum(tf.values()))
        df.update(tf.keys())
    n_docs = len(reports)
    basis = (prev or {}).get("params", {}).get("basis")
    if not basis or abs(n_docs - basis["docs"]) > BASIS_DRIFT * basis["docs"]:
        basis = None
    basis_docs = basis["docs"] if basis else n_docs
    avg_len = basis["avg_len"] if basis else round((sum(lengths) / n_docs) or 1.0, 4)
    # A posting stores only the document's saturated tf part, which depends on that document and the frozen
    # basis; the term's idf sits in front of its list ("idf:postings"). An arriving report therefore changes
    # a term's idf prefix and appends one posting — existing postings keep their bytes.
    raw: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for i, tf in enumerate(tfs):
        norm = K1 * (1 - B + B * lengths[i] / avg_len)
        for t, f in tf.items():
            tf_part = f / (f + norm)  # BM25 tf saturation / (k1 + 1), in (0, 1)
            # sqrt scale keeps resolution for low tf: tf_part ≈ (q / QMAX) ** 2
            raw[t].append((i, min(QMAX, max(1, round(QMAX * math.sqrt(tf_part))))))
    shards: list[dict[str, str]] = [{} for _ in range(SHARDS)]
    postings = 0
    for t in sorted(raw):
        idf = math.log(1 + (basis_docs - df[t] + 0.5) / (df[t] + 0.5))
        shards[shard_of(t)][t] = f"{idf:.4f}:{encode_postings(raw[t])}"
        postings += len(raw[t])

    stock_list = sorted(stocks.values(), key=lambda s: s["slug"])
    stock_idx = {s["slug"]: i for i, s in enumerate(stock_list)}
    corpus = hashlib.sha256("\n".join(f"{r['rel']}:{r['sha256']}" for r in reports).encode()).hexdigest()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "corpus_sha256": corpus,
        "method": ("Hangul bigrams + Latin words, report-level BM25 (k1=1.2, b=0.75): per-term idf, per-posting tf "
                   "part quantised (sqrt) to 1..31; weight = idf * (k1 + 1) * (q / qmax) ** 2; "
                   "paragraphs ranked client-side in the evidence pack; relation kinds from cue words in the "
                   "sentence around each mention (rule-based, not a model)."),
        "params": {"shards": SHARDS, "k1": K1, "b": B, "qmax": QMAX, "source_base": GITHUB_BLOB,
                   "basis": {"docs": basis_docs, "avg_len": avg_len}},
        # Per-shard content versions: an unchanged shard keeps its bytes; the client checks it got the matching one.
        "shard_versions": [_shard_version(sh) for sh in shards],
        "stats": {"reports": n_docs, "chunks": sum(len(r["chunks"]) for r in reports), "terms": len(raw),
                  "postings": postings, "boilerplate_chunks_dropped": dropped, "text_aliases": len(text_aliases)},
        "synonyms": {"groups": synonyms["groups"], "related": synonyms.get("related", {})},
        "stocks": [{"slug": s["slug"], "name": s["name"], "market": s["market"], "ticker": s["ticker"], "page": s["page"],
                    "aliases": sorted({s["ticker"], *s["names"]})} for s in stock_list],
        "reports": [{
            "id": r["id"], "club": r["club"], "date": r["date"], "first_seen": r["first_seen"],
            "primary": stock_idx.get(r["primary"]) if r["primary"] else None,
            "primary_conf": r["primary_conf"] if r["primary"] else None,
            "scope": r["scope"], "title": r["title"], "path": r["rel"],
            "chunks": len(r["chunks"]),
            "mentions": [[stock_idx[m[0]], KINDS.index(m[1]), CONFS.index(m[2]), m[3]] for m in r["mentions"]],
        } for r in reports],
    }
    out: dict[str, dict] = {"manifest.json": manifest}
    for i, shard in enumerate(shards):
        out[f"terms/{i:03d}.json"] = {"schema_version": SCHEMA_VERSION, "version": manifest["shard_versions"][i], "terms": shard}
    for r in reports:
        out[f"reports/{r['id']}.json.gz"] = {
            "schema_version": SCHEMA_VERSION, "id": r["id"], "path": r["rel"], "file_sha256": r["sha256"],
            "chunks": [[c["para"], c["lines"][0], c["lines"][1], c["hash"], c["text"]] for c in r["chunks"]],
            "mentions": r["mentions"],
        }
    return out


def _shard_version(shard: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(shard, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _render(doc: dict) -> str:
    # Term shards are written one term per line so a git diff touches only the terms that changed.
    indent = 0 if "terms" in doc else None
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), indent=indent, allow_nan=False) + "\n"


def _read_existing(path: Path) -> str | None:
    if not path.exists():
        return None
    data = path.read_bytes()
    return (gzip.decompress(data) if path.suffix == ".gz" else data).decode("utf-8")


def write(out: dict[str, dict], root: Path = OUT) -> int:
    """Write changed files only (a gz pack is compared decompressed, so zlib differences never churn)."""
    tmp = root.parent / f".{root.name}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    changed = 0
    for rel, doc in out.items():
        text = _render(doc)
        dest = root / rel
        if _read_existing(dest) == text:
            continue
        changed += 1
        stage = tmp / rel
        stage.parent.mkdir(parents=True, exist_ok=True)
        data = gzip.compress(text.encode(), compresslevel=9, mtime=0) if rel.endswith(".gz") else text.encode()
        stage.write_bytes(data)
    # Publish staged files, manifest last so a reader never sees a manifest ahead of its shards.
    staged = sorted((p for p in tmp.rglob("*") if p.is_file()), key=lambda p: p.name == "manifest.json") if tmp.exists() else []
    for p in staged:
        dest = root / p.relative_to(tmp)
        dest.parent.mkdir(parents=True, exist_ok=True)
        p.replace(dest)
    if tmp.exists():
        shutil.rmtree(tmp)
    for p in sorted(root.rglob("*")) if root.exists() else []:
        if p.is_file() and p.relative_to(root).as_posix() not in out:
            p.unlink()
            changed += 1
    return changed


def stale(out: dict[str, dict], root: Path = OUT) -> list[str]:
    bad = [rel for rel, doc in out.items() if _read_existing(root / rel) != _render(doc)]
    extra = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
             and p.relative_to(root).as_posix() not in out] if root.exists() else []
    return bad + extra


# ── reference query engine (the web client implements the same steps) ───────────

KIND_WEIGHT = {"primary": 1.0, "supplier_customer": 0.6, "peer": 0.5, "sector": 0.45, "passing": 0.15}
CONF_WEIGHT = {"high": 1.0, "medium": 0.8, "low": 0.6}
SYNONYM_WEIGHT, RELATED_WEIGHT, FUZZY_WEIGHT = 0.7, 0.4, 0.8
GROUP_FLOOR, DECAY = 0.2, 0.5
FUZZY_MAX_HITS = 3


def _strip_query_josa(word: str) -> str:
    if HANGUL_CHAR.match(word) and len(word) >= 3:
        for j in QUERY_JOSA:
            if word.endswith(j) and len(word) - len(j) >= 2:
                return word[:-len(j)]
    return word


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class Index:
    def __init__(self, root: Path = OUT):
        self.root = root
        self.manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self._shards: dict[int, dict[str, str]] = {}
        self.alias_to_stock: dict[str, int] = {}
        for i, s in enumerate(self.manifest["stocks"]):
            for a in [*s["aliases"], s["slug"]]:
                self.alias_to_stock.setdefault(normalize(a).replace(" ", ""), i)
        self.groups: dict[str, list[str]] = {}
        for g in self.manifest["synonyms"]["groups"]:
            for w in g:
                self.groups[normalize(w)] = [normalize(x) for x in g if normalize(x) != normalize(w)]
        self.related = {normalize(k): [normalize(x) for x in v] for k, v in self.manifest["synonyms"]["related"].items()}

    def postings(self, term: str) -> dict[int, float]:
        """report index → BM25 weight of the term in that report."""
        sid = shard_of(term)
        if sid not in self._shards:
            self._shards[sid] = json.loads((self.root / f"terms/{sid:03d}.json").read_text(encoding="utf-8"))["terms"]
        value = self._shards[sid].get(term)
        if not value:
            return {}
        idf_text, enc = value.split(":", 1)
        idf, k1, qmax = float(idf_text), self.manifest["params"]["k1"], self.manifest["params"]["qmax"]
        return {d: idf * (k1 + 1) * (q / qmax) ** 2 for d, q in decode_postings(enc)}

    def variant_scores(self, text: str) -> dict[int, float]:
        ts = list(dict.fromkeys(terms(text)))
        if not ts:
            return {}
        need = len(ts) if len(ts) <= 3 else math.ceil(len(ts) * 0.75)
        hits: dict[int, list[float]] = defaultdict(list)
        for t in ts:
            for doc, w in self.postings(t).items():
                hits[doc].append(w)
        return {d: sum(ws) / len(ts) for d, ws in hits.items() if len(ws) >= need}

    def plan(self, query: str) -> dict:
        q = normalize(query).strip()
        compact = q.replace(" ", "")
        stock = self.alias_to_stock.get(compact)
        words = [_strip_query_josa(w) for w in q.split()] if stock is None else [compact]
        fuzzy = None
        if stock is None and len(words) == 1 and len(compact) >= 3:
            best = sorted((_levenshtein(compact, a), a) for a in self.alias_to_stock if abs(len(a) - len(compact)) <= 1)
            if best and best[0][0] == 1:
                fuzzy = self.alias_to_stock[best[0][1]]
        plan = []
        for w in words:
            variants = [(w, 1.0)]
            if stock is not None:
                s = self.manifest["stocks"][stock]
                variants = [(normalize(a), 1.0) for a in s["aliases"] if not a.isdigit()] or variants
            variants += [(x, SYNONYM_WEIGHT) for x in self.groups.get(w, [])]
            variants += [(x, RELATED_WEIGHT) for x in self.related.get(w, [])]
            plan.append(variants)
        return {"stock": stock, "fuzzy": fuzzy, "words": plan}

    def search(self, query: str, clubs: set[str] | None = None, date_from: str | None = None,
               date_to: str | None = None, collected_by: str | None = None) -> dict:
        plan = self.plan(query)
        reports = self.manifest["reports"]

        def score_words(words):
            per_word = []
            for variants in words:
                best: dict[int, float] = {}
                for text, weight in variants:
                    for d, s in self.variant_scores(text).items():
                        best[d] = max(best.get(d, 0.0), s * weight)
                per_word.append(best)
            return per_word

        per_word = score_words(plan["words"])
        # A near-miss of a covered company's name (edit distance 1) that finds almost nothing is read as that
        # company: its names join the query at a lower weight.
        if plan["fuzzy"] is not None and len(set().union(*per_word)) < FUZZY_MAX_HITS:
            s = self.manifest["stocks"][plan["fuzzy"]]
            plan["stock"] = plan["fuzzy"]
            plan["words"] = [[*plan["words"][0], *((normalize(a), FUZZY_WEIGHT) for a in s["aliases"] if not a.isdigit())]]
            per_word = score_words(plan["words"])
        totals: dict[int, float] = defaultdict(float)
        matched: Counter = Counter()
        for best in per_word:
            for d, s in best.items():
                totals[d] += s
                matched[d] += 1
        n_words = len(per_word)
        need = n_words if n_words <= 2 else n_words - 1   # every word for short queries; one may miss in long ones
        scored = []
        for d, s in totals.items():
            r = reports[d]
            if matched[d] < need:
                continue
            if clubs and r["club"] not in clubs:
                continue
            if date_from and (not r["date"] or r["date"] < date_from):
                continue
            if date_to and (not r["date"] or r["date"] > date_to):
                continue
            if collected_by and (not r["first_seen"] or r["first_seen"] > collected_by):
                continue
            scored.append((d, s * (matched[d] / n_words) ** 2))
        stock = plan["stock"]
        scored.sort(key=lambda x: (stock is not None and reports[x[0]]["primary"] == stock, x[1], -x[0]), reverse=True)

        # Related stocks: the report's primary subject and the companies its text names, weighted by relation.
        # Mentions only count from reports that match the topic strongly (>= GROUP_FLOOR of the best report),
        # and a stock's score decays geometrically over its contributions, so one strong relation outranks
        # many passing mentions of a large company.
        top = max((s for _, s in scored), default=0.0)
        groups: dict[int, dict] = {}
        for d, s in scored:
            r = reports[d]
            links = [(r["primary"], "primary", r["primary_conf"] or "medium")] if r["primary"] is not None else []
            if s >= GROUP_FLOOR * top:
                links += [(m[0], KINDS[m[1]], CONFS[m[2]]) for m in r["mentions"]]
            for idx, kind, conf in links:
                g = groups.setdefault(idx, {"stock": idx, "score": 0.0, "reports": [], "clubs": set(), "kinds": Counter(),
                                            "first": None, "last": None})
                contrib = s * KIND_WEIGHT[kind] * CONF_WEIGHT[conf]
                g["reports"].append((d, kind, conf, contrib))
                g["clubs"].add(r["club"])
                g["kinds"][kind] += contrib
                if r["date"]:
                    g["first"] = min(filter(None, [g["first"], r["date"]]))
                    g["last"] = max(filter(None, [g["last"], r["date"]]))
        for g in groups.values():
            g["reports"].sort(key=lambda x: -x[3])
            g["score"] = sum(c * DECAY ** i for i, (_, _, _, c) in enumerate(g["reports"]))
        ordered = sorted(groups.values(), key=lambda g: (g["stock"] == stock, g["score"], -g["stock"]), reverse=True)
        return {"plan": plan, "reports": scored, "stocks": ordered}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--check", action="store_true")
    p.add_argument("--query")
    args = p.parse_args()
    if args.query:
        idx = Index()
        res = idx.search(args.query)
        ms, rs = idx.manifest["stocks"], idx.manifest["reports"]
        print(f"plan: stock={res['plan']['stock']} fuzzy={res['plan']['fuzzy']} words={res['plan']['words']}")
        for g in res["stocks"][:12]:
            print(f"  STOCK {ms[g['stock']]['name']:<16} {g['score']:7.2f} reports={len(g['reports'])} "
                  f"clubs={len(g['clubs'])} {g['first']}~{g['last']} kind={g['kinds'].most_common(1)[0][0]}")
        for d, s in res["reports"][:12]:
            print(f"  REPORT {s:7.2f} {rs[d]['club']:<5} {rs[d]['date']} {rs[d]['title']}")
        return 0
    out = build(previous_manifest())
    if args.check:
        bad = stale(out)
        if bad:
            print(f"full-text index stale ({len(bad)} files, e.g. {bad[:3]}) — run scripts/build_fulltext_index.py")
            return 1
        return 0
    changed = write(out)
    m = out["manifest.json"]["stats"]
    print(f"full-text index: {m['reports']} reports, {m['chunks']} chunks, {m['terms']} terms, "
          f"{m['postings']} postings, {m['text_aliases']} text aliases; {changed} files changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
