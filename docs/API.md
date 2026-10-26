# Verdict Archive API v2 — Reference

> **DISCLAIMER / 투자 경고**
>
> All data served by this API is derived from student investment-club reports and
> **backtested simulations** of strategies built on them.
> It is provided for **research and educational purposes only**.
> It does **NOT** constitute investment advice or a solicitation to buy or sell
> any security.  
> Past backtest performance does not guarantee future results.  
> **All trading decisions and their financial consequences are solely your
> responsibility (실매매로 인한 모든 손익의 책임은 전적으로 사용자에게 있습니다).**

---

- Base URL: `https://verdict-archive.vercel.app/api/v2`
- Immutable strategy revision trades may include the accounting producer's `pnl_krw`
  (realized P&L in KRW after costs). Older revisions omit it; the existing revision format remains valid.
- Machine-readable contract: [`/api/v2/openapi.json`](https://verdict-archive.vercel.app/api/v2/openapi.json) (OpenAPI 3.1)
- Status: **v2 is current.** `/api/v1` is frozen and deprecated — see [Migration from v1](#migration-from-v1).
- Design record: [`docs/adr/0001-api-v2-and-medallion.md`](adr/0001-api-v2-and-medallion.md)
- Schema and lineage explorer: `/developers/architecture`; optional FastAPI metadata service and local setup: [METADATA.md](METADATA.md).

The API is a set of static JSON documents written by the daily CI pipeline and served from Vercel's CDN.
Reads serve static files. A guard in front of them (`src/proxy.ts`) answers OPTIONS, rejects writes and
answers URIs that are not published resources with `404`, all before file resolution.

## 1. Design principles

### 1.1 REST는 프로토콜이 아니라 아키텍처 설계 원칙이다

REST does not ship with a wire format or a library; it is a set of constraints. v2 applies them as concrete,
checkable choices:

- **URI = resource identifier.** Plural nouns, hierarchical, no verbs, no file extensions:
  `/api/v2/strategies/{id}/trades`, not `/getTrades?key=…` or `/trades/{key}.json`.
- **HTTP method = action and meaning.** Reading is `GET`; there is nothing else to do with a published
  research record, so there is no other method (see 1.2).
- **Stable identifiers.** A strategy id is its pipeline key (`W_allweather_chandelier`, also `D+_chandelier_optuna`,
  `T-_kospi_core_regime`), an instrument id is `{market}-{ticker}` lower-cased (`kr-000080`, `us-fix`), a club id is
  its school code (`smic`). They are the same ids the website uses (`/stocks/kr-000080`, `/clubs/smic`).
- **Uniform interface.** Every document has the same envelope and navigates by links (1.4).
- **Stateless.** Every request is self-contained; no sessions, cookies or auth.

| URI | Resource | Mutability |
|---|---|---|
| `/api/v2` | entry point: links to every collection | daily |
| `/api/v2/openapi.json` | this contract (keeps `.json` by convention) | daily |
| `/api/v2/signals` | signal snapshot index, newest first | daily |
| `/api/v2/signals/latest` | latest snapshot | daily |
| `/api/v2/signals/{date}` | snapshot of one market date | **immutable** |
| `/api/v2/strategies` | all strategies with metrics | daily |
| `/api/v2/strategies/{id}` | one strategy | daily |
| `/api/v2/strategies/{id}/trades` | closed-trade log | daily |
| `/api/v2/strategies/{id}/revisions/{sha256}` | NAV, wealth, trades, positions, yearly — content-addressed | **immutable** |
| `/api/v2/track-record` | forward record of published buy signals | daily |
| `/api/v2/instruments` | instruments with a price series | daily |
| `/api/v2/instruments/{id}` | instrument summary + every club report on it | daily |
| `/api/v2/instruments/{id}/prices` | daily OHLCV candles | daily |
| `/api/v2/instruments/{id}/trade-marks` | headline-strategy buy/sell/stop marks | daily |
| `/api/v2/clubs` | clubs with scorecards | daily |
| `/api/v2/clubs/{id}` | one club | daily |
| `/api/v2/clubs/{id}/reports` | that club's scored reports | daily |

### 1.2 CRUD 매핑보다 HTTP 특성 이해가 중요하다

What makes an API predictable under duplicate requests, network failures and retries is using HTTP's own
properties correctly, not mapping tables onto POST/GET/PUT/DELETE.

| Method | Safe | Idempotent | Cacheable | v2 |
|---|---|---|---|---|
| `GET` | yes | yes | yes | every resource |
| `HEAD` | yes | yes | yes | every resource (headers only — cheap existence/freshness check) |
| `OPTIONS` | yes | yes | no | `204` with CORS headers on every v2 URI, handled before static files |
| `POST` `PUT` `PATCH` `DELETE` | no | `PUT`/`DELETE` only | no | **none** |

**Why no write methods.** Data changes only through the reviewed pipeline (collect → transcribe → score →
backtest → export → CI gate → commit). Writing over HTTP would bypass that review and the point-in-time
guarantees (no look-ahead). Because every operation is safe and idempotent, **any request may be retried
any number of times** — there is no side effect to duplicate.

**Status codes**

| Code | When | Body |
|---|---|---|
| `200 OK` | resource exists | the document |
| `304 Not Modified` | `If-None-Match` matches the current `ETag` | none |
| `308 Permanent Redirect` | legacy chart paths (`/prices/*`, `/strategy-marks/*`) and `/api/v2/` → `/api/v2` | none |
| `404 Not Found` | unknown URI — including a date with no snapshot (weekend, holiday, future) | `application/problem+json` ([RFC 9457](https://www.rfc-editor.org/rfc/rfc9457)), `Cache-Control: no-store` |
| `405 Method Not Allowed` | unsupported method on any v2 URI | `application/problem+json`, `Allow: GET, HEAD, OPTIONS`, `Cache-Control: no-store` |

```json
{"type": "about:blank", "title": "Not Found", "status": 404,
 "detail": "No /api/v2 resource at this URI. Start from /api/v2 and follow links.",
 "instance": "/api/v2/strategies/UNKNOWN"}
```

`src/proxy.ts` handles OPTIONS, unsupported methods and unknown URIs before static-file resolution, so
existing and missing resources use the same method contract. It knows what exists from
`src/data/api-v2-resources.json` (every published URI, written by `export_api_v2.py`). It has to: Next.js
applies the `next.config.ts` header rules before any route runs and only *adds* a route handler's headers
when the rule did not set them, so a 404 from a fallback route would inherit the cache rule of the URI it was
asked for — a year for an unpublished revision hash or snapshot date under `next start` (see
[HTTP-EVIDENCE-2026-10-20.md](HTTP-EVIDENCE-2026-10-20.md)).

**Caching follows mutability**

| Mutability | Resources | `Cache-Control` |
|---|---|---|
| daily | everything else | `public, max-age=300, stale-while-revalidate=86400` |
| immutable (dated) | `/signals/{date}` | `public, max-age=3600, s-maxage=31536000` |
| immutable (content-addressed) | `/strategies/{id}/revisions/{sha256}` | `public, max-age=31536000, immutable` |
| missing resource | `404` from the guard, every kind of URI | `no-store` |
| unsupported method / preflight | `405` / `204` from the guard | `no-store` |

- Daily resources: fresh for 5 minutes, then served stale while revalidating for up to a day. Data changes once
  per trading day, so a day-old copy during an outage is better than an error.
- Dated snapshots never change; browsers still revalidate hourly as a safety margin while the CDN keeps its copy
  until the next deploy.
- Errors are never cached, per resource kind: a future or today's snapshot date is published by the evening
  refresh, a new revision hash and a new instrument, strategy or club appear with the next export, and an
  unknown URI may become a resource in an additive 2.x release. Every one of them can turn from `404` into `200`
  at the next deploy, and the guard (middleware) runs on every request before cached files are served, so
  caching its answer saves nothing.
- Revisions are content-addressed: new bytes get a new URI, so caching forever is safe. They also send
  `Link: </api/v2/strategies/{id}>; rel="up"`.

**ETag / 304.** Every response has an `ETag`. Send it back as `If-None-Match`; an unchanged resource answers `304`
with no body. Polling more often than `max-age` returns `304` (or a CDN hit) at negligible cost.

**Retry guidance.** Retry network errors and `5xx` with exponential backoff (1 s, 2 s, 4 s … plus jitter) and
honour `Retry-After` when present. Do not retry `404`/`405` — they are answers, not failures. A `404` on today's
dated snapshot means it is not published yet; use `/signals/latest` or retry after the evening refresh (KST).

### 1.3 REST가 정답은 아니다 — 도메인에 맞게, 일관성 있게

Stateless and Uniform Interface are kept everywhere; where the domain is not CRUD, the domain wins:

- **A signal snapshot** is a domain event (what the strategy said on a market date). It is its own immutable
  resource `/signals/{date}`, not a filter on a mutable list. `/signals/latest` is a separate resource whose
  `links.snapshot` names the dated copy.
- **A strategy revision** is a backtest result identified by the sha256 of its canonical JSON. It is the one
  document without the envelope: its bytes *are* the hashed payload (verify integrity by recomputing the hash),
  and the same bytes answer the deprecated v1 URL, so nothing is stored twice.
- **Track record** is a computed view over the append-only snapshots, exposed as one read-only resource rather
  than as an "evaluate" action.
- **No query parameters.** Filtering, sorting and pagination would need a server; collections are small enough
  (the largest, `/clubs/smic/reports`, is ~0.9 MB) to fetch whole, and every item links to its detail.

### 1.4 Envelope and links

Every v2 document (except revisions) is:

```json
{
  "schema_version": "2.0",
  "links": {"self": "/api/v2/strategies/D+_chandelier_optuna", "collection": "/api/v2/strategies",
            "trades": "/api/v2/strategies/D+_chandelier_optuna/trades",
            "current_revision": "/api/v2/strategies/D+_chandelier_optuna/revisions/81d0…6c1e"},
  "meta": {"generated_at": "2026-10-19T09:58:46+00:00", "as_of": "2026-10-16",
           "disclaimer": "…", "disclaimer_ko": "…"},
  "data": { … }
}
```

- `links` are root-relative hrefs (`/api/v2/…`); resolve them against the API origin. Collection items carry
  `links.self`; payload items (positions, buy/sell signals, trades, reports, track-record entries) carry
  `links.instrument` when that instrument resource exists. **Navigate by link instead of building URIs.**
- `meta.generated_at` is when the pipeline last produced *different* content for the document (an unchanged
  re-export keeps the previous value); `meta.as_of` is the market date the data refers to; `meta.count` is the
  number of items in `data` for collections.
- `+` in strategy ids is a literal path character; `%2B` also works.

## 2. Resources

Field-level schemas, with an example for every response, are in `openapi.json`
(`components/schemas`). Objects there are closed: the contract test fails on any undocumented field.

### Signals

- `GET /api/v2/signals` — `data[]`: `{date, headline_strategy, counts, links.self}`, newest first. History starts
  2026-06-11 and includes every snapshot first published under v1.
- `GET /api/v2/signals/latest`, `GET /api/v2/signals/{date}` — `data`: `as_of`, `headline_strategy`,
  `regime {kospi_above_200ma, state, kospi_close, kospi_ma200, parking, applies}`,
  `slots {max_positions, open, available}`, `counts`, `open_positions[]`, `buy_signals[]`, `sell_signals[]`.
  Item fields are those of v1 (`ticker, market, name, entry_date, entry_price, current_price, stop_level,
  unrealized_pct, days_held, entry_reason, trigger_reports[]`; buy: `signal_date, entry_basis_price`; sell:
  `reason: approaching_stop|stop_hit, stop_level, dist_to_stop_pct`) plus `links.instrument`.
  Dated snapshots are frozen at first publication; `meta.generated_at` is that publication time.

### Strategies

- `GET /api/v2/strategies` / `GET /api/v2/strategies/{id}` — `id, label, is_headline, is_curated, verdict
  (SOTA·채택·연구용·기각), verdict_reason, trade_count, metrics {start, end, total_return_pct, cagr_pct, sharpe,
  mdd_pct, win_rate_pct, avg_hold_days}, in_sample, out_of_sample, best_trade {ticker, name, return_pct},
  top_decile_pnl_share_pct, benchmarks {kospi_dca_ratio, kospi_dca_beats, aw_dca_ratio, aw_dca_beats},
  dsr {psr, dsr, sr0_annualized, n_trials, significant_after_deflation}, walkforward, walkforward_oos`.
- `GET /api/v2/strategies/{id}/trades` — closed trades, oldest first:
  `ticker, market, name, entry_date, exit_date, entry_price, exit_price, return_pct, days, exit_reason,
  entry_reason, trigger_reports[], links.instrument`.
- `GET /api/v2/strategies/{id}/revisions/{sha256}` — `{schema_version: "1.0", strategy_key, nav[], wealth,
  trades[], open_positions[], yearly[], content_hash}`; `content_hash` = sha256 of the canonical JSON
  (sorted keys, `,`/`:` separators, UTF-8) of the object without `content_hash`. Old revisions stay published.

### Track record

- `GET /api/v2/track-record` — `method, summary {n_signals, n_tracking, n_unavailable, n_pending, coverage_pct,
  avg_return_pct, win_rate_pct, best_pct, worst_pct, first_snapshot, last_snapshot, n_snapshots}, entries[]
  {signal_date, ticker, market, name, basis_price, entry_date, entry_price, current_price, return_pct, peak_pct,
  days, status: tracking|pending|unavailable, trigger_schools, headline_strategy, links.instrument},
  headline_by_date`. Rendered at [/track](https://verdict-archive.vercel.app/track).
  Snapshot scoring is not automatically independent out-of-sample evidence; first-publication provenance is
  unknown unless independently verified.

### Instruments

- `GET /api/v2/instruments` — every instrument with a published price series:
  `id, market, ticker, name, exchange, report_count, club_report_counts {club id: n}, first_report_date,
  latest_report_date, links {self, prices, trade_marks?}`.
- `GET /api/v2/instruments/{id}` — the same summary plus `reports[]` (the report subset below, each with
  `links.club`), newest first.
- `GET /api/v2/instruments/{id}/prices` — `data.candles[] {time, open, high, low, close, volume}` (local currency,
  missing fields `null`) and `data.report_dates[]`; `meta.as_of` = last candle.
- `GET /api/v2/instruments/{id}/trade-marks` — only for instruments the headline strategy traded:
  `strategy_id, ticker, market, name, marks[] {date, side: buy|sell|stop, price, reason}, open_stop
  {stop_level, entry_date, as_of} | null`.

### Clubs and reports

- `GET /api/v2/clubs`, `GET /api/v2/clubs/{id}` — `id, name, full_name, report_count, first_report_date,
  latest_report_date, scorecard` (scored subset: buy-side reports since 2019-07 — `reports, priced_reports,
  with_target, up_latest, down_latest, target_hit, avg_return_latest_pct, median_return_latest_pct`).
- `GET /api/v2/clubs/{id}/reports` — newest first, undated last. **Report subset:** identity (`school, report_type,
  era, market, ticker, exchange, company, display_name`), dates (`report_date, authored_date` — as transcribed and
  unvalidated, `first_trade_date, latest_trade_date`), rating (`rating, rating_class`), prices (`target_price,
  report_current_price, stated_upside_pct, start_close, latest_close`), returns (`return_latest_pct,
  return_30d/90d/180d/365d_pct, benchmark_return_pct, alpha_latest_pct, max_high_return_pct, peak_return_24m_pct,
  peak_date_24m`), target hit (`target_hit_until_latest, first_target_hit_date, days_to_target`), status (`age_days,
  maturity, performance_bucket, bucket_peak, data_issue`), sources (`source_name, source_md_url, source_pdf_url`).
  Internal pipeline fields (repo paths, raw parser text, QA flags) are not published.

## 3. Examples

### curl

```bash
# Entry point, then follow links
curl -s https://verdict-archive.vercel.app/api/v2 | python -m json.tool

# Conditional request: 304 when nothing changed
etag=$(curl -sI https://verdict-archive.vercel.app/api/v2/signals/latest | awk -F': ' 'tolower($1)=="etag"{print $2}' | tr -d '\r')
curl -s -o /dev/null -w '%{http_code}\n' -H "If-None-Match: $etag" https://verdict-archive.vercel.app/api/v2/signals/latest

# Unknown resource → RFC 9457 problem
curl -s https://verdict-archive.vercel.app/api/v2/strategies/UNKNOWN
```

### Python — today's signals by link navigation, with retries

```python
import json, time, urllib.error, urllib.request

ORIGIN = "https://verdict-archive.vercel.app"

def get(href, attempts=4):
    for i in range(attempts):
        try:
            with urllib.request.urlopen(ORIGIN + href, timeout=10) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500:          # 404/405 are answers, not failures
                raise
        except urllib.error.URLError:
            pass
        time.sleep(2 ** i)            # safe to retry: every request is GET
    raise RuntimeError(f"gave up on {href}")

index = get("/api/v2")
latest = get(index["links"]["latest_signals"])
snap = latest["data"]
print(f"as of {snap['as_of']} · {snap['headline_strategy']} · regime {snap['regime']['state']}")
for b in snap["buy_signals"]:
    print("BUY", b["ticker"], b["name"], b["entry_reason"])
    if "links" in b:                  # instrument resource exists
        inst = get(b["links"]["instrument"])["data"]
        print("   ", inst["report_count"], "club reports, latest", inst["latest_report_date"])
for s in snap["sell_signals"]:
    print("SELL", s["ticker"], s["reason"], s["dist_to_stop_pct"])
```

### Python — what changed between two snapshots

```python
index = get("/api/v2/signals")["data"]          # newest first
today, prev = (get(e["links"]["self"])["data"] for e in index[:2])
held = lambda s: {p["ticker"] for p in s["open_positions"]}
print("entered", held(today) - held(prev), "exited", held(prev) - held(today))
```

### JavaScript — strategy curve via `current_revision`

```js
const origin = "https://verdict-archive.vercel.app";
const get = async (href) => {
  const res = await fetch(origin + href);
  if (!res.ok) throw new Error(`${res.status} ${href}`);
  return res.json();
};

const strategies = await get("/api/v2/strategies");
const headline = strategies.data.find((s) => s.is_headline);
const strategy = await get(headline.links.self);
const revision = await get(strategy.links.current_revision); // immutable: cache forever
console.log(headline.id, revision.nav.at(-1), `${revision.trades.length} trades`);
```

## 4. Migration from v1

| v1 (deprecated) | v2 |
|---|---|
| `GET /api/v1/signals/latest.json`, `/api/v1/signals/latest` | `GET /api/v2/signals/latest` |
| `GET /api/v1/signals/{date}.json` | `GET /api/v2/signals/{date}` |
| `GET /api/v1/strategies.json` (`strategies[].key`) | `GET /api/v2/strategies` (`data[].id`, `links.self`) |
| `GET /api/v1/trades/{key}.json` | `GET /api/v2/strategies/{id}/trades` |
| `GET /api/v1/strategy-details/manifest.json` | `links.current_revision` of `GET /api/v2/strategies/{id}` |
| `GET /api/v1/strategy-details/{key}/{hash}.json` | `GET /api/v2/strategies/{id}/revisions/{sha256}` (identical bytes) |
| `GET /api/v1/forward.json` | `GET /api/v2/track-record` |
| `GET /prices/{slug}.json` | `GET /api/v2/instruments/{id}/prices` (old path answers `308`) |
| `GET /strategy-marks/{slug}.json` | `GET /api/v2/instruments/{id}/trade-marks` (old path answers `308`) |
| — | `/api/v2/instruments`, `/instruments/{id}`, `/clubs`, `/clubs/{id}`, `/clubs/{id}/reports`, `/signals` |

Shape changes: top-level fields move into `{links, meta, data}` (`generated_at`, `as_of`, disclaimers → `meta`);
v1's top-level arrays become `data`; strategy `key` → `id`; `in_sample.period` ("start ~ end") → `start`/`end`;
`kospi_dca_*`/`aw_dca_*` → `benchmarks`; price files lose `slug` (it is in `links`); trade-mark `strategy_key` →
`strategy_id`.

## 5. Versioning and deprecation policy

- `/api/v1` is **frozen**: same URIs, same payloads, still refreshed daily, every operation marked
  `deprecated: true` in [`/api/v1/openapi.json`](https://verdict-archive.vercel.app/api/v1/openapi.json).
  Every v1 response carries `Deprecation: @1792368000` (RFC 9745; 2026-10-19T00:00:00Z) and
  `Link: </api/v2>; rel="successor-version"`. No sunset date is promised yet; when one is set it will be announced
  with a `Sunset` header (RFC 8594).
- Within v2, additive fields keep `schema_version` `2.x`; clients must ignore unknown fields. Removing or renaming
  a field, changing its type, or changing a URI is breaking and ships as `/api/v3`.

## 6. Contract and regeneration

Writers: `scripts/export_stock_charts.py` (instrument prices — its output defines the instrument set),
`scripts/export_signals_api.py` (v1 + revision files), `scripts/export_api_v2.py` (every other v2 document and
`openapi.json`). Run after the backtest and the forward record:

```bash
uv run python scripts/export_stock_charts.py
uv run python scripts/backtest_momentum.py
uv run python scripts/export_signals_api.py
uv run python scripts/build_forward_record.py
uv run python scripts/export_api_v2.py

# CI gates: write nothing, exit 1 on drift (generated_at ignored; append-only files only need to exist)
uv run python scripts/export_signals_api.py --check
uv run python scripts/export_api_v2.py --check
```

- Exports are write-then-prune and deterministic: an unchanged document is not rewritten, so a no-op re-run
  leaves no diff. Dated snapshots and revisions are append-only and never pruned.
- Invalid input fails the export (non-finite numbers, missing fields, unknown club ids, invalid ids).
- `tests/test_api_v2_contract.py` validates every published file against the spec (closed objects), checks
  that every `links.*` resolves, every spec path is published, examples are valid, revisions hash correctly,
  committed append-only files never change, and `--check` passes. `tests/api-v2-schemas.test.cjs` validates
  every document against the web app's zod schemas (`src/lib/api-v2.ts`). v1 keeps `tests/test_api_contract.py`
  and `tests/api-schemas.test.cjs`.
- Serving: `next.config.ts` rewrites extensionless URIs to `public/api/v2/<uri>.json`, sets the cache/CORS/v1
  deprecation headers, and redirects the legacy chart paths. `src/proxy.ts` answers OPTIONS, writes and unknown
  URIs (`src/data/api-v2-resources.json`); `src/app/api/v2/[...path]/route.ts` remains the problem+json fallback
  for a listed file that is not deployed. The v1 extensionless alias `/api/v1/signals/latest` is a rewrite to
  `latest.json` (identical bytes and headers); `npm run build` first runs `scripts/check-build-inputs.cjs`, which
  fails the build when that file does not satisfy the web app's zod `SignalsPayloadSchema`.
- `tests/api-http.test.cjs` checks the guard against the real resource index; `tests/test_http_contract.py`
  starts `next start` from the production build and checks status, headers, `304`, problem bodies and the
  Swagger default examples over real HTTP (skipped, with the reason, without a fresh build).

## 7. API inventory and boundaries

Everything below is read-only. Only the first two rows are a public, versioned API contract.

| Surface | Served by | Audience | Contract |
|---|---|---|---|
| `/api/v2/**` | static files + `src/proxy.ts` | public | this document, `openapi.json`, Swagger at `/developers/swagger` |
| `/api/v1/**` | static files (+ rewrites for the `signals/latest` alias and strategy-details), `src/proxy.ts` (methods, missing snapshot/revision) | public, **deprecated** | frozen payloads; `/api/v1/openapi.json` |
| `/research/*.json`, `/research/reviews/{sha256}.json` | static files | the site's own pages (public URLs, no API guarantees) | per-file `schema_version`, Python writer + zod reader; not in Swagger |
| `/api/metadata/schema` | Next route → FastAPI | the `/developers/architecture` page | `src/lib/schema-metadata.ts`; `503` problem+json when the sidecar is down |
| FastAPI `:8000` `/metadata/schema`, `/docs`, `/openapi.json` | `npm run dev:metadata` | **local / ops only**, never deployed | [METADATA.md](METADATA.md) |

- **v2 and v1** get CORS (`*`), the cache rules above, and the guard's method contract (v1 static files used to
  answer `OPTIONS`/writes with `500`). The guard's matcher is `/api/v2/:path*` and `/api/v1/:path*`, so it runs on
  every v1 request too (a set lookup; non-matching v1 GETs pass straight through).
- URI spelling: a percent-encoded `/` (`%2F`) inside a segment is never a separator (`/api/v2/clubs%2Fewha` is
  `404`), and the entry point has one URI, `/api/v2` (`/api/v2/index` and `/api/v2/index.json` are `404`).
- **`/research/*.json`** are build-time/static inputs of research pages: `portfolio.json` (paper portfolio,
  `paper_portfolio.py`), `strategy-robustness.json` (`build_strategy_robustness.py`), `review.json` and
  `reviews/*.json` (`build_research_review.py`), `search.json` and full-text shards (`build_search_index.py`,
  `build_fulltext_index.py`), `funded-account.json` (`funded_account.py`). They have no CORS or cache rule
  (Next's default static `Cache-Control: public, max-age=0` + `ETag`), no `/api/` path, and may change shape
  with a `schema_version` bump without a deprecation period. Integrations should use `/api/v2`. See
  [연구 보조 서빙 계약](#연구-보조-서빙-계약) for the two files with published field lists.
- **Metadata** is schema/lineage only — no data rows, no SQL, no writes. The FastAPI sidecar keeps database
  credentials server-side, answers `ETag` + `Cache-Control: private, no-cache` (`304` on `If-None-Match`), and
  allows CORS only from `METADATA_CORS_ORIGINS`. The Next route `/api/metadata/schema` is `no-store` and returns
  `503` problem+json when `METADATA_API_URL` (default `http://127.0.0.1:8000/metadata/schema`) is unreachable —
  which is the expected state on a deployment without the sidecar.

---

## 텔레그램 일일 신호 봇

포트폴리오의 일별 평가·비중 알림은 [모의 포트폴리오](PORTFOLIO.md)를 따른다.
아래 기존 신호 변화 알림은 Actions Variable `TELEGRAM_SIGNALS_ENABLED=true`일 때만 별도로 실행한다.

`scripts/send_telegram_signals.py`가 매 거래일 CI(`refresh-daily.yml`)에서
오늘의 신호를 한국어 다이제스트로 텔레그램에 보냅니다. 전일 스냅샷과
비교해 **신규 진입 / 청산 / 매수 신호 / 매도 임박**의 변화가 있을 때만
전송합니다 (변화 없으면 침묵 — 스팸 없음).

### 1. 봇 만들기 (BotFather)

1. 텔레그램에서 [@BotFather](https://t.me/BotFather) 검색 → 대화 시작
2. `/newbot` 입력 → 봇 이름과 username(예: `verdict_signal_bot`) 지정
3. BotFather가 주는 **HTTP API 토큰**(`123456:ABC-DEF...` 형태)을 복사
   — 이것이 `TELEGRAM_BOT_TOKEN`

### 2. chat_id 얻기

1. 방금 만든 봇과 대화를 시작하고 아무 메시지나 보냅니다
   (그룹에 봇을 초대해 그룹으로 받아도 됩니다)
2. 브라우저에서 다음 URL 열기:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. 응답 JSON의 `result[].message.chat.id` 값이 `TELEGRAM_CHAT_ID`
   (그룹이면 음수일 수 있습니다 — 그대로 사용)

### 3. GitHub 저장소 시크릿 설정

저장소 → Settings → Secrets and variables → Actions → New repository secret:

| Secret 이름 | 값 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather가 준 API 토큰 |
| `TELEGRAM_CHAT_ID` | 위에서 얻은 chat id |

시크릿을 설정하기 전에도 CI는 실패하지 않습니다 — 환경 변수가 없으면
스크립트가 조용히 종료(exit 0)합니다.

### 4. 로컬 테스트

```bash
# 다이제스트 내용만 출력 (전송 없음, 토큰 불필요)
uv run python scripts/send_telegram_signals.py --dry-run

# 실제 전송 (변화 없어도 강제 전송)
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
  uv run python scripts/send_telegram_signals.py --force
```


## 연구 보조 서빙 계약

- `POST /api/telegram/webhook`: Telegram 내부 명령 수신 엔드포인트. 웹훅 secret 헤더·방 허용 목록·관리자 ID로
  인증한다. 참여자는 조회만 가능하고 `/refresh`는 관리자만 GitHub Actions로 실행한다. v1/v2 읽기 전용 REST
  계약과 별도이며 응답은 `no-store`다. [명령·권한·연결 절차](TELEGRAM-COMMANDS.md).
- `/research/portfolio.json`: `schema_version=2.0`, `engine_version=parking-open-v2`.
  `policy`(고정 W 정책), `source_as_of`(원천 기준일), `as_of`(전체 시장 완료 평가일),
  `fills`(시장·방향·시가·환율·수량·원화 현금흐름·비용), `data_issues`를 제공한다.
  `status`는 `awaiting_next_open` 또는 `tracking`. 관측 이후 모의 계정이며 상세는 [PORTFOLIO.md](PORTFOLIO.md).
- `/research/strategy-robustness.json`: `schema_version=1.0`, 동일 엔진의 고정 정책 7조건 진단.
  `policy`, `input_fingerprint`, `reoptimized=false`, `ranking`(원화 순손익), `scenarios`,
  `top5_positive_pnl_share_pct`, `historical_reference`를 제공한다.
  최고 수익 종목 제외는 사후 민감도 진단이며 별도 투자 신호가 아니다.

위 파일은 연구 화면의 정적 보조 계약이며 v2 REST 리소스나 Swagger 작업을 추가하지 않는다.
Python 작성기와 zod 경계 모델이 검증하고, 잘못된 입력은 빌드/파이프라인을 실패시킨다.
