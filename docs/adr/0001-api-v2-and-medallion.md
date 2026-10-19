# ADR 0001 — REST API v2 and the Bronze/Silver/Gold data layout

- Status: accepted (2026-10-19)
- Branch: `feat/api-v2-medallion` (stacked on `feat/kordoc-parsing`)

## Context

- `/api/v1` grew file-shaped: URIs name files (`/strategies.json`, `/trades/{key}.json`,
  `/strategy-details/{key}/{hash}.json`), and chart data sits outside the API (`/prices/{slug}.json`,
  `/strategy-marks/{slug}.json`). Strategies, instruments and clubs are not addressable as resources.
- `data/` mixes raw downloads, cleaned derivations and serving aggregates, and raw price responses are
  overwritten in place, so prices cannot be reprocessed from what was actually fetched.

## Decision 1 — REST API v2

`/api/v1` is frozen (AGENTS.md rule 4). The new resource model ships as `/api/v2`; v1 keeps its URIs and
payloads and is marked deprecated (`deprecated: true` in its spec; `Deprecation` and
`Link: </api/v2>; rel="successor-version"` response headers). No sunset date is promised yet.

### Principles applied

1. **URIs identify resources.** Plural nouns, no file extensions, no verbs. Identifiers are stable across
   versions: a strategy id is its existing key (`W_allweather_chandelier`), an instrument id is the
   existing slug (`kr-005930`, `us-fix`), a club id is the school code (`smic`).
2. **Methods carry the semantics.** The API is a read-only publication of pipeline output, so every
   resource supports `GET`/`HEAD` (safe, idempotent, cacheable) and `OPTIONS` (CORS). There is no
   write method because nothing is written over HTTP: data changes only through the reviewed pipeline
   (CI). Clients can retry any request freely.
3. **Caching follows mutability.** Immutable resources (dated snapshots, content-addressed strategy
   revisions) are `immutable`; mutable resources use a short `max-age` + `stale-while-revalidate`.
   Static hosting supplies `ETag`, so conditional `If-None-Match` requests get `304`.
4. **Status codes.** `200` found, `304` not modified, `404` unknown resource as
   `application/problem+json` (RFC 9457) where the platform allows, `405` for other methods.
5. **Uniform envelope.** Every v2 document is
   `{ "schema_version": "2.0", "links": { "self": ..., ... }, "meta": { ... }, "data": ... }`.
   Collection items carry `links.self`, so clients navigate by link instead of building URIs.
6. **Pragmatism.** Domain events that are not CRUD (a signal snapshot, a strategy revision) are modelled
   as their own read-only resources rather than forced into verbs.

### Resources

| URI | Mutability | Source (gold) |
|---|---|---|
| `GET /api/v2` | daily | index of top-level links |
| `GET /api/v2/openapi.json` | daily | OpenAPI 3.1 spec (keeps `.json` by convention) |
| `GET /api/v2/signals` | daily | snapshot index: `[{date, headline_strategy, counts, links.self}]`, newest first |
| `GET /api/v2/signals/latest` | daily | latest snapshot |
| `GET /api/v2/signals/{date}` | immutable | snapshot for `YYYY-MM-DD` |
| `GET /api/v2/strategies` | daily | all strategies with IS/OOS metrics |
| `GET /api/v2/strategies/{id}` | daily | one strategy: metrics + `links.current_revision`, `links.trades` |
| `GET /api/v2/strategies/{id}/trades` | daily | closed-trade log |
| `GET /api/v2/strategies/{id}/revisions/{sha256}` | immutable | NAV, wealth, trades, open positions, yearly (content-addressed) |
| `GET /api/v2/track-record` | daily | forward (out-of-sample) record of published buy signals |
| `GET /api/v2/instruments` | daily | instruments with price data: `[{id, market, ticker, name, links}]` |
| `GET /api/v2/instruments/{id}` | daily | instrument summary + club reports on it |
| `GET /api/v2/instruments/{id}/prices` | daily | daily OHLCV candles (was `/prices/{slug}.json`) |
| `GET /api/v2/instruments/{id}/trade-marks` | daily | headline-strategy buy/sell marks (was `/strategy-marks/{slug}.json`) |
| `GET /api/v2/clubs` | daily | clubs with report summary |
| `GET /api/v2/clubs/{id}` | daily | one club summary + links |
| `GET /api/v2/clubs/{id}/reports` | daily | that club's scored reports |

Physical files: `public/api/v2/<uri-path>.json` (`/api/v2` → `public/api/v2/index.json`). `next.config.ts`
rewrites the extensionless URI to the file, so Python stays the only writer and the CDN serves bytes.
`/prices/*` and `/strategy-marks/*` answer `308` to their v2 URIs.

Writers: `scripts/export_api_v2.py` (all v2 resources; `--check` drift gate), `scripts/export_stock_charts.py`
(instrument prices). Contract: `public/api/v2/openapi.json` ← validated by `tests/test_api_v2_contract.py`
and the zod schemas in `src/lib/api-v2.ts`.

## Decision 2 — Bronze / Silver / Gold

Paths live in `scripts/lake.py`; scripts import them instead of spelling `data/...`.

```
data/
  bronze/                         raw, append-only, provenance kept
    reports/pdfs/{school}/        downloaded PDFs (git-ignored)
    reports/manifest.json         one row per download: url, sha256, size, downloaded_at, error
    reports/kuvic_items.json      raw KUVIC listing
    prices/{vendor}/ingest_date=YYYY-MM-DD/{run_id}.csv.gz   raw vendor rows as fetched (git-ignored)
    prices/_ingest.jsonl          one line per fetch: vendor, symbol, request, fetched_at, rows, sha256, lib version
    reference/krx_names.json      pykrx names as fetched
  silver/                         validated, de-duplicated, standardised
    reports/markdown/{school}/    kordoc transcripts
    reports/parsed.csv            one row per report, typed fields
    reports/parse_issues.csv
    reference/                    kr_name_ticker.json, corrections.json (curated)
    prices/{MARKET}_{ticker}.csv  clean OHLCV (git-ignored; rebuildable from bronze)
  gold/                           purpose-built aggregates
    reports/performance.csv, performance_summary.csv
    ops/health.json
    _cache/                       deterministic backtest stage caches (git-ignored)
  catalog.json                    dataset → layer, path, producer, inputs, schema, rows, updated_at
src/data/*.json, public/api/**    gold serving artifacts read by Next.js
```

Rules:
- Bronze is never rewritten in place; corrections happen in silver (`corrections.json` is a silver input).
- Silver writers validate before writing. Structural violations (duplicate or non-increasing dates, missing
  close column) fail the run and nothing is written. Row-level violations (NaN close, close ≤ 0, a bar
  outside the OHLC envelope by more than 0.5%) are quarantined to `data/silver/_rejects/{symbol}.jsonl` with
  a reason and a warning, never silently dropped, and the refresh continues. Halt days (open/high/low = 0)
  are kept. A scan of 771 existing files found 0 structural failures and 4 quarantinable rows.
- Bronze is never behind silver: the recorder is flushed before every silver price write.
- Gold reads silver only. Every gold artifact the web reads carries `schema_version`.
- `data/catalog.json` records lineage so any gold file can be traced to its silver and bronze inputs.

## Consequences

- One large rename diff (`data/markdown` → `data/silver/reports/markdown`). It lands on the kordoc branch,
  which re-transcribes every report anyway, so the daily bot's `data/markdown` commits on `main` conflict
  once, at merge time; resolve by taking the branch layout and re-running transcription.
- v1 and v2 coexist; v1 strategy-detail URLs are served from the v2 revision files to avoid duplicating them.
