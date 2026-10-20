# HTTP evidence — REST API v1/v2, 2026-10-20

Scope: `docs/HANDOFF-CLAUDE-2026-10-20.md` §4. Every row below is a real HTTP request; nothing is inferred
from code. Three sources are kept apart:

| Source | What it is | Proves |
|---|---|---|
| **A. before** | the user's `next start` on `127.0.0.1:3100`, built 2026-10-20 20:30 from the tree before this change (GET/HEAD only, so it was not disturbed) | the bug |
| **B. after (local)** | `next build` + `next start` of the current working tree on `127.0.0.1:3125`, built in a private copy (so the `.next` of the 3100 server was never touched), stopped afterwards | the fix and the full matrix |
| **C. production** | `https://verdict-archive.vercel.app`: section 4 after the change (commit 7bd2b7b9f, full matrix), section 4a the older deployment (read-only GET/HEAD) | deployed CDN behaviour before and after |

Section 4 verifies this change on the production CDN; the one remaining delta (v1 alias `Cache-Control`) has a fix in the working tree that is unverified on Vercel until the next deploy.

## 1. Root cause of the cached 404s

Next.js applies `next.config.ts` `headers()` rules to the outgoing response before any route runs, matched on
the *request* path. When a route handler answers, `sendResponse` (`node_modules/next/dist/server/send-response.js`)
copies the handler's headers only if the header is not already present ("only append the header if it is either
not present in the outbound response or if the header supports multiple values"). So the fallback route's
`Cache-Control: no-store` was silently dropped and every 404 inherited the cache rule of the URI that was asked
for (source A):

| URI kind | 404 `Cache-Control` before | Consequence |
|---|---|---|
| unknown v2 URI / unknown id | `public, max-age=300, stale-while-revalidate=86400` | a resource created by the next export can stay 404 in caches for up to a day |
| future/today's snapshot `/api/v2/signals/{date}` | `public, max-age=3600, s-maxage=31536000` | browsers 1 h; a shared cache keeps the 404 for a year |
| unknown revision `/api/v2/strategies/{id}/revisions/{sha256}` | `public, max-age=31536000, immutable` (+ `Link: rel="up"`) | 404 cached for a year, never revalidated |
| v1 alias of an unknown revision | `public, max-age=31536000, immutable` | same |

On Vercel (source C) a function's own headers do win, so the v2 404s there are already `no-store`; but the
deployed v1 snapshot 404 (`/api/v1/signals/2099-01-01.json`, Next's HTML 404 page) carries
`public, max-age=3600, s-maxage=31536000` and was a CDN **HIT** — a cached 404 that will hide that date until
the next deploy. Self-hosting (`next start`, the repo's `Dockerfile`) hits every row of the table above.

**Fix.** `src/proxy.ts` (the method guard, which runs before file resolution and whose responses do override
the config rules) now also answers GET/HEAD for URIs that are not published, from
`src/data/api-v2-resources.json` — every URI under `/api/v2` that answers 200, written by
`scripts/export_api_v2.py` and checked by its `--check` gate and by `tests/api-http.test.cjs`. The proxy also
covers `/api/v1`: the strategy-details alias and dated snapshots are checked against their v2 twins, and
OPTIONS/writes get the v2 method contract (they used to return `500 text/plain`). Error policy per resource:

| 404 for | `Cache-Control` | Why |
|---|---|---|
| future/today's snapshot date (v1 and v2) | `no-store` | published by the evening refresh; pollers must see it on the next request |
| unknown revision hash (v1 alias and v2) | `no-store` | a new backtest publishes a new hash; the long `immutable` rule is only true for bytes that exist |
| unknown instrument / strategy / club / sub-resource | `no-store` | appear with the next export (new coverage, new strategy, first trade) |
| any other unknown v2 URI | `no-store` | may become a resource in an additive 2.x release |

A short `max-age` was considered and rejected: the guard (middleware) runs on every request before cached files are served, so
caching its 404 saves no origin work, and on Vercel a deploy purges the CDN anyway — `no-store` costs nothing.

## 2. Before (source A, 127.0.0.1:3100)

| request (source A, :3100) | status | Content-Type | Cache-Control | Link |
|---|---|---|---|---|
| `GET /api/v2/no-such-resource` | 404 | application/problem+json | public, max-age=300, stale-while-revalidate=86400 |  |
| `HEAD /api/v2/no-such-resource` | 404 | application/problem+json | public, max-age=300, stale-while-revalidate=86400 |  |
| `GET /api/v2/strategies/UNKNOWN` | 404 | application/problem+json | public, max-age=300, stale-while-revalidate=86400 |  |
| `HEAD /api/v2/strategies/UNKNOWN` | 404 | application/problem+json | public, max-age=300, stale-while-revalidate=86400 |  |
| `GET /api/v2/signals/2099-01-01` | 404 | application/problem+json | public, max-age=3600, s-maxage=31536000 |  |
| `HEAD /api/v2/signals/2099-01-01` | 404 | application/problem+json | public, max-age=3600, s-maxage=31536000 |  |
| `GET /api/v2/strategies/W_allweather_chandelier/revisions/0000000000000000000000000000000000000000000000000000000000000000` | 404 | application/problem+json | public, max-age=31536000, immutable | </api/v2/strategies/W_allweather_chandelier>; rel="up" |
| `HEAD /api/v2/strategies/W_allweather_chandelier/revisions/0000000000000000000000000000000000000000000000000000000000000000` | 404 | application/problem+json | public, max-age=31536000, immutable | </api/v2/strategies/W_allweather_chandelier>; rel="up" |
| `GET /api/v1/signals/2099-01-01.json` | 404 | text/html; charset=utf-8 | private, no-cache, no-store, max-age=0, must-revalidate | </api/v2>; rel="successor-version" |
| `HEAD /api/v1/signals/2099-01-01.json` | 404 | text/html; charset=utf-8 | private, no-cache, no-store, max-age=0, must-revalidate | </api/v2>; rel="successor-version" |
| `GET /api/v1/strategy-details/W_allweather_chandelier/0000000000000000000000000000000000000000000000000000000000000000.json` | 404 | application/problem+json | public, max-age=31536000, immutable | </api/v2>; rel="successor-version" |
| `HEAD /api/v1/strategy-details/W_allweather_chandelier/0000000000000000000000000000000000000000000000000000000000000000.json` | 404 | application/problem+json | public, max-age=31536000, immutable | </api/v2>; rel="successor-version" |
| `GET /api/v2/instruments/kr-000020` | 404 | application/problem+json | public, max-age=300, stale-while-revalidate=86400 |  |
| `HEAD /api/v2/instruments/kr-000020` | 404 | application/problem+json | public, max-age=300, stale-while-revalidate=86400 |  |

`kr-000020` was the spec's default `InstrumentId` example (section 5). One `OPTIONS /api/v1/signals/latest.json`
on :3100 returned `500 text/plain` (`Allow: GET`), as did OPTIONS/POST/PUT/PATCH/DELETE on that file in a first
:3125 build before the v1 guard was added.

## 3. After (source B, 127.0.0.1:3125, current tree)

ETag/Last-Modified columns: `y` = present. Revision used: the first committed revision of
`W_allweather_chandelier`. `kr-000120` is the spec's default instrument example (it has prices and trade marks).

| resource | request | status | body bytes | Content-Type | Cache-Control | ETag | Last-Modified | ACAO | Allow / Location |
|---|---|---|---|---|---|---|---|---|---|
| entry point | `GET /api/v2` | 200 | 1189 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| entry point | `HEAD /api/v2` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| openapi | `GET /api/v2/openapi.json` | 200 | 90381 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| openapi | `HEAD /api/v2/openapi.json` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| collection | `GET /api/v2/strategies` | 200 | 35087 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| collection | `HEAD /api/v2/strategies` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item (+ literal) | `GET /api/v2/strategies/D+_chandelier_optuna` | 200 | 2248 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item (+ literal) | `HEAD /api/v2/strategies/D+_chandelier_optuna` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item (%2B) | `GET /api/v2/strategies/D%2B_chandelier_optuna` | 200 | 2248 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item (%2B) | `HEAD /api/v2/strategies/D%2B_chandelier_optuna` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item (.json) | `GET /api/v2/strategies/D+_chandelier_optuna.json` | 200 | 2248 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item (.json) | `HEAD /api/v2/strategies/D+_chandelier_optuna.json` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item + query | `GET /api/v2/strategies/D+_chandelier_optuna?foo=1&bar` | 200 | 2248 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item + query | `HEAD /api/v2/strategies/D+_chandelier_optuna?foo=1&bar` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| latest snapshot | `GET /api/v2/signals/latest` | 200 | 10501 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| latest snapshot | `HEAD /api/v2/signals/latest` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| dated snapshot | `GET /api/v2/signals/2026-10-19` | 200 | 11669 | application/json; charset=UTF-8 | public, max-age=3600, s-maxage=31536000 | y | y | * |  |
| dated snapshot | `HEAD /api/v2/signals/2026-10-19` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=3600, s-maxage=31536000 | y | y | * |  |
| revision | `GET /api/v2/strategies/W_allweather_chandelier/revisions/54a2941bb4273c0a713cb7211921cded621d20cf07c4dd2771a9411111b1562c` | 200 | 275866 | application/json; charset=UTF-8 | public, max-age=31536000, immutable | y | y | * |  |
| revision | `HEAD /api/v2/strategies/W_allweather_chandelier/revisions/54a2941bb4273c0a713cb7211921cded621d20cf07c4dd2771a9411111b1562c` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=31536000, immutable | y | y | * |  |
| prices | `GET /api/v2/instruments/kr-000120/prices` | 200 | 301142 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| prices | `HEAD /api/v2/instruments/kr-000120/prices` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| trade marks | `GET /api/v2/instruments/kr-000120/trade-marks` | 200 | 1239 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| trade marks | `HEAD /api/v2/instruments/kr-000120/trade-marks` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| 404 unpublished instrument prices | `GET /api/v2/instruments/kr-005930/prices` | 404 | 186 | application/problem+json | no-store | - | - | * |  |
| 404 unpublished instrument prices | `HEAD /api/v2/instruments/kr-005930/prices` | 404 | 0 | application/problem+json | no-store | - | - | * |  |
| 404 unknown | `GET /api/v2/no-such-resource` | 404 | 174 | application/problem+json | no-store | - | - | * |  |
| 404 unknown | `HEAD /api/v2/no-such-resource` | 404 | 0 | application/problem+json | no-store | - | - | * |  |
| 404 unknown id | `GET /api/v2/strategies/UNKNOWN` | 404 | 176 | application/problem+json | no-store | - | - | * |  |
| 404 unknown id | `HEAD /api/v2/strategies/UNKNOWN` | 404 | 0 | application/problem+json | no-store | - | - | * |  |
| 404 future date | `GET /api/v2/signals/2099-01-01` | 404 | 176 | application/problem+json | no-store | - | - | * |  |
| 404 future date | `HEAD /api/v2/signals/2099-01-01` | 404 | 0 | application/problem+json | no-store | - | - | * |  |
| 404 unknown hash | `GET /api/v2/strategies/W_allweather_chandelier/revisions/0000000000000000000000000000000000000000000000000000000000000000` | 404 | 267 | application/problem+json | no-store | - | - | * |  |
| 404 unknown hash | `HEAD /api/v2/strategies/W_allweather_chandelier/revisions/0000000000000000000000000000000000000000000000000000000000000000` | 404 | 0 | application/problem+json | no-store | - | - | * |  |
| v1 latest (deprecated) | `GET /api/v1/signals/latest.json` | 200 | 9484 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=60 | y | y | * |  |
| v1 latest (deprecated) | `HEAD /api/v1/signals/latest.json` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=60 | y | y | * |  |
| v1 latest alias | `GET /api/v1/signals/latest` | 200 | 9354 | application/json | public, max-age=300, stale-while-revalidate=60 | - | - | * |  |
| v1 latest alias | `HEAD /api/v1/signals/latest` | 200 | 0 | application/json | public, max-age=300, stale-while-revalidate=60 | - | - | * |  |
| v1 dated | `GET /api/v1/signals/2026-10-19.json` | 200 | 10633 | application/json; charset=UTF-8 | public, max-age=3600, s-maxage=31536000 | y | y | * |  |
| v1 dated | `HEAD /api/v1/signals/2026-10-19.json` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=3600, s-maxage=31536000 | y | y | * |  |
| v1 revision alias | `GET /api/v1/strategy-details/W_allweather_chandelier/54a2941bb4273c0a713cb7211921cded621d20cf07c4dd2771a9411111b1562c.json` | 200 | 275866 | application/json; charset=UTF-8 | public, max-age=31536000, immutable | y | y | * |  |
| v1 revision alias | `HEAD /api/v1/strategy-details/W_allweather_chandelier/54a2941bb4273c0a713cb7211921cded621d20cf07c4dd2771a9411111b1562c.json` | 200 | 0 | application/json; charset=UTF-8 | public, max-age=31536000, immutable | y | y | * |  |
| v1 404 future date | `GET /api/v1/signals/2099-01-01.json` | 404 | 181 | application/problem+json | no-store | - | - | * |  |
| v1 404 future date | `HEAD /api/v1/signals/2099-01-01.json` | 404 | 0 | application/problem+json | no-store | - | - | * |  |
| v1 404 unknown hash | `GET /api/v1/strategy-details/W_allweather_chandelier/0000000000000000000000000000000000000000000000000000000000000000.json` | 404 | 268 | application/problem+json | no-store | - | - | * |  |
| v1 404 unknown hash | `HEAD /api/v1/strategy-details/W_allweather_chandelier/0000000000000000000000000000000000000000000000000000000000000000.json` | 404 | 0 | application/problem+json | no-store | - | - | * |  |

### Other methods

| resource | request | status | body bytes | Content-Type | Cache-Control | ETag | Last-Modified | ACAO | Allow / Location |
|---|---|---|---|---|---|---|---|---|---|
| methods | `OPTIONS /api/v2/strategies` | 204 | 0 |  | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `POST /api/v2/strategies` | 405 | 155 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PUT /api/v2/strategies` | 405 | 155 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PATCH /api/v2/strategies` | 405 | 155 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `DELETE /api/v2/strategies` | 405 | 155 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `OPTIONS /api/v2/strategies/W_allweather_chandelier/revisions/54a2941bb4273c0a713cb7211921cded621d20cf07c4dd2771a9411111b1562c` | 204 | 0 |  | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `POST /api/v2/strategies/W_allweather_chandelier/revisions/54a2941bb4273c0a713cb7211921cded621d20cf07c4dd2771a9411111b1562c` | 405 | 254 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PUT /api/v2/strategies/W_allweather_chandelier/revisions/54a2941bb4273c0a713cb7211921cded621d20cf07c4dd2771a9411111b1562c` | 405 | 254 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PATCH /api/v2/strategies/W_allweather_chandelier/revisions/54a2941bb4273c0a713cb7211921cded621d20cf07c4dd2771a9411111b1562c` | 405 | 254 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `DELETE /api/v2/strategies/W_allweather_chandelier/revisions/54a2941bb4273c0a713cb7211921cded621d20cf07c4dd2771a9411111b1562c` | 405 | 254 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `OPTIONS /api/v2/no-such-resource` | 204 | 0 |  | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `POST /api/v2/no-such-resource` | 405 | 161 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PUT /api/v2/no-such-resource` | 405 | 161 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PATCH /api/v2/no-such-resource` | 405 | 161 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `DELETE /api/v2/no-such-resource` | 405 | 161 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `OPTIONS /api/v1/signals/latest.json` | 204 | 0 |  | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `POST /api/v1/signals/latest.json` | 405 | 164 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PUT /api/v1/signals/latest.json` | 405 | 164 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PATCH /api/v1/signals/latest.json` | 405 | 164 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `DELETE /api/v1/signals/latest.json` | 405 | 164 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `OPTIONS /api/v1/signals/latest` | 204 | 0 |  | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `POST /api/v1/signals/latest` | 405 | 159 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PUT /api/v1/signals/latest` | 405 | 159 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PATCH /api/v1/signals/latest` | 405 | 159 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `DELETE /api/v1/signals/latest` | 405 | 159 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |

### Conditional requests, redirects, CORS preflight

| resource | request | status | body bytes | Content-Type | Cache-Control | ETag | Last-Modified | ACAO | Allow / Location |
|---|---|---|---|---|---|---|---|---|---|
| If-None-Match: W/"890f-1a1591259be" | `GET /api/v2/strategies` | 304 | 0 |  | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| If-None-Match: "stale" | `GET /api/v2/strategies` | 200 | 35087 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| If-Modified-Since: Tue, 20 Oct 2026 11:28:57 GMT | `GET /api/v2/strategies` | 304 | 0 |  | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| If-None-Match: W/"2d95-1a15560e82b" | `GET /api/v2/signals/2026-10-19` | 304 | 0 |  | public, max-age=3600, s-maxage=31536000 | y | y | * |  |
| If-None-Match: "stale" | `GET /api/v2/signals/2026-10-19` | 200 | 11669 | application/json; charset=UTF-8 | public, max-age=3600, s-maxage=31536000 | y | y | * |  |
| If-Modified-Since: Mon, 19 Oct 2026 18:16:16 GMT | `GET /api/v2/signals/2026-10-19` | 304 | 0 |  | public, max-age=3600, s-maxage=31536000 | y | y | * |  |
| If-None-Match: W/"4359a-1a15560e8b5" | `GET /api/v2/strategies/W_allweather_chandelier/revisions/54a2941bb4273c0a713cb7211921cded621d20cf07c4dd2771a9411111b1562c` | 304 | 0 |  | public, max-age=31536000, immutable | y | y | * |  |
| If-None-Match: "stale" | `GET /api/v2/strategies/W_allweather_chandelier/revisions/54a2941bb4273c0a713cb7211921cded621d20cf07c4dd2771a9411111b1562c` | 200 | 275866 | application/json; charset=UTF-8 | public, max-age=31536000, immutable | y | y | * |  |
| If-Modified-Since: Mon, 19 Oct 2026 18:16:16 GMT | `GET /api/v2/strategies/W_allweather_chandelier/revisions/54a2941bb4273c0a713cb7211921cded621d20cf07c4dd2771a9411111b1562c` | 304 | 0 |  | public, max-age=31536000, immutable | y | y | * |  |
| If-None-Match: W/"250c-1a15912539b" | `GET /api/v1/signals/latest.json` | 304 | 0 |  | public, max-age=300, stale-while-revalidate=60 | y | y | * |  |
| If-None-Match: "stale" | `GET /api/v1/signals/latest.json` | 200 | 9484 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=60 | y | y | * |  |
| If-Modified-Since: Tue, 20 Oct 2026 11:28:55 GMT | `GET /api/v1/signals/latest.json` | 304 | 0 |  | public, max-age=300, stale-while-revalidate=60 | y | y | * |  |
| trailing slash | `GET /api/v2/` | 308 | 7 |  |  | - | - |  |  Location: /api/v2 |
| legacy prices | `GET /prices/kr-000120.json` | 308 | 36 |  |  | - | - |  |  Location: /api/v2/instruments/kr-000120/prices |
| legacy trade marks | `GET /strategy-marks/kr-000120.json` | 308 | 41 |  |  | - | - |  |  Location: /api/v2/instruments/kr-000120/trade-marks |
| preflight from other origin | `OPTIONS /api/v2/strategies` | 204 | 0 |  | no-store | - | - | * | GET, HEAD, OPTIONS |
| simple CORS GET | `GET /api/v2/strategies` | 200 | 35087 | application/json; charset=UTF-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| swagger page | `GET /developers/swagger` | 200 | 2868 | text/html; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | - | - |  |  |

### Reading the matrix

- **GET/HEAD**: HEAD returns the same status and headers with an empty body everywhere, including 404s.
- **Query strings** are ignored (no query parameters exist): `?foo=1&bar` returns the same 200 bytes.
- **`+` / `%2B` / `.json`** spellings name the same resource and return identical bytes.
- **Conditional requests**: static files carry a weak `ETag` and `Last-Modified`; `If-None-Match` and
  `If-Modified-Since` both give `304` with no body; a non-matching tag gives `200`. The v1 extensionless alias
  `/api/v1/signals/latest` is a prerendered route handler and sends neither validator (unchanged, v1 is frozen).
- **CORS**: `Access-Control-Allow-Origin: *` on every v1/v2 response including errors; the preflight is `204`
  with `Access-Control-Allow-Methods: GET, HEAD, OPTIONS`. Plain GETs need no preflight.
- **Redirects**: `/api/v2/` → `308 /api/v2`; `/prices/{id}.json` and `/strategy-marks/{id}.json` → `308` to
  their v2 resources (pattern redirects: the target is not checked, so an unpublished id redirects to a 404).
- **Errors**: every error is `no-store`. The 404 of an unknown revision still carries
  `Link: </api/v2/strategies/{id}>; rel="up"` from the config rule (a header the guard does not set) — it points
  at the existing parent and is harmless.
- **Unpublished instrument**: `kr-005930` (used as an id example in older docs) has no price series, so it is
  not an API resource; `docs/API.md` now uses `kr-000080`, which is both a stock page and an instrument.

## 4. Production after the change (source C), captured 2026-10-20T19:12:45Z

`https://verdict-archive.vercel.app` serving commit `7bd2b7b9f` (`/api/v2/openapi.json` reports `info.version`
2.0.1 and two servers). Same `matrix.sh` as section 3, including OPTIONS/POST/PUT/PATCH/DELETE on API URIs
(static and read-only, so no state changes; approved by the lead).

| resource | request | status | body bytes | Content-Type | Cache-Control | ETag | Last-Modified | ACAO | Allow / Location |
|---|---|---|---|---|---|---|---|---|---|
| entry point | `GET /api/v2` | 200 | 1189 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| entry point | `HEAD /api/v2` | 200 | 0 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| openapi | `GET /api/v2/openapi.json` | 200 | 90822 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| openapi | `HEAD /api/v2/openapi.json` | 200 | 0 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| collection | `GET /api/v2/strategies` | 200 | 35083 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| collection | `HEAD /api/v2/strategies` | 200 | 0 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item (+ literal) | `GET /api/v2/strategies/D+_chandelier_optuna` | 200 | 2247 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item (+ literal) | `HEAD /api/v2/strategies/D+_chandelier_optuna` | 200 | 0 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item (%2B) | `GET /api/v2/strategies/D%2B_chandelier_optuna` | 200 | 2247 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item (%2B) | `HEAD /api/v2/strategies/D%2B_chandelier_optuna` | 200 | 0 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item (.json) | `GET /api/v2/strategies/D+_chandelier_optuna.json` | 200 | 2247 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item (.json) | `HEAD /api/v2/strategies/D+_chandelier_optuna.json` | 200 | 0 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item + query | `GET /api/v2/strategies/D+_chandelier_optuna?foo=1&bar` | 200 | 2247 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| item + query | `HEAD /api/v2/strategies/D+_chandelier_optuna?foo=1&bar` | 200 | 0 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| latest snapshot | `GET /api/v2/signals/latest` | 200 | 10632 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| latest snapshot | `HEAD /api/v2/signals/latest` | 200 | 0 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| dated snapshot | `GET /api/v2/signals/2026-10-19` | 200 | 11669 | application/json; charset=utf-8 | public, max-age=3600, s-maxage=31536000 | y | y | * |  |
| dated snapshot | `HEAD /api/v2/signals/2026-10-19` | 200 | 0 | application/json; charset=utf-8 | public, max-age=3600, s-maxage=31536000 | y | y | * |  |
| revision | `GET /api/v2/strategies/W_allweather_chandelier/revisions/3b49bb047658055efaaf7f5273f934187b50088f5082d846d67cc896a07ad61c` | 200 | 268332 | application/json; charset=utf-8 | public, max-age=31536000, immutable | y | y | * |  |
| revision | `HEAD /api/v2/strategies/W_allweather_chandelier/revisions/3b49bb047658055efaaf7f5273f934187b50088f5082d846d67cc896a07ad61c` | 200 | 0 | application/json; charset=utf-8 | public, max-age=31536000, immutable | y | y | * |  |
| prices | `GET /api/v2/instruments/kr-000120/prices` | 200 | 301138 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| prices | `HEAD /api/v2/instruments/kr-000120/prices` | 200 | 0 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| trade marks | `GET /api/v2/instruments/kr-000120/trade-marks` | 200 | 1239 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| trade marks | `HEAD /api/v2/instruments/kr-000120/trade-marks` | 200 | 0 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| 404 unpublished instrument prices | `GET /api/v2/instruments/kr-005930/prices` | 404 | 186 | application/problem+json | no-store | - | - | * |  |
| 404 unpublished instrument prices | `HEAD /api/v2/instruments/kr-005930/prices` | 404 | 0 | application/problem+json | no-store | - | - | * |  |
| 404 unknown | `GET /api/v2/no-such-resource` | 404 | 174 | application/problem+json | no-store | - | - | * |  |
| 404 unknown | `HEAD /api/v2/no-such-resource` | 404 | 0 | application/problem+json | no-store | - | - | * |  |
| 404 unknown id | `GET /api/v2/strategies/UNKNOWN` | 404 | 176 | application/problem+json | no-store | - | - | * |  |
| 404 unknown id | `HEAD /api/v2/strategies/UNKNOWN` | 404 | 0 | application/problem+json | no-store | - | - | * |  |
| 404 future date | `GET /api/v2/signals/2099-01-01` | 404 | 176 | application/problem+json | no-store | - | - | * |  |
| 404 future date | `HEAD /api/v2/signals/2099-01-01` | 404 | 0 | application/problem+json | no-store | - | - | * |  |
| 404 unknown hash | `GET /api/v2/strategies/W_allweather_chandelier/revisions/0000000000000000000000000000000000000000000000000000000000000000` | 404 | 267 | application/problem+json | no-store | - | - | * |  |
| 404 unknown hash | `HEAD /api/v2/strategies/W_allweather_chandelier/revisions/0000000000000000000000000000000000000000000000000000000000000000` | 404 | 0 | application/problem+json | no-store | - | - | * |  |
| v1 latest (deprecated) | `GET /api/v1/signals/latest.json` | 200 | 9558 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=60 | y | y | * |  |
| v1 latest (deprecated) | `HEAD /api/v1/signals/latest.json` | 200 | 0 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=60 | y | y | * |  |
| v1 latest alias | `GET /api/v1/signals/latest` | 200 | 9428 | application/json | public, max-age=300 | - | - | * |  |
| v1 latest alias | `HEAD /api/v1/signals/latest` | 200 | 0 | application/json | public, max-age=300 | - | - | * |  |
| v1 dated | `GET /api/v1/signals/2026-10-19.json` | 200 | 10633 | application/json; charset=utf-8 | public, max-age=3600, s-maxage=31536000 | y | y | * |  |
| v1 dated | `HEAD /api/v1/signals/2026-10-19.json` | 200 | 0 | application/json; charset=utf-8 | public, max-age=3600, s-maxage=31536000 | y | y | * |  |
| v1 revision alias | `GET /api/v1/strategy-details/W_allweather_chandelier/3b49bb047658055efaaf7f5273f934187b50088f5082d846d67cc896a07ad61c.json` | 200 | 268332 | application/json; charset=utf-8 | public, max-age=31536000, immutable | y | y | * |  |
| v1 revision alias | `HEAD /api/v1/strategy-details/W_allweather_chandelier/3b49bb047658055efaaf7f5273f934187b50088f5082d846d67cc896a07ad61c.json` | 200 | 0 | application/json; charset=utf-8 | public, max-age=31536000, immutable | y | y | * |  |
| v1 404 future date | `GET /api/v1/signals/2099-01-01.json` | 404 | 181 | application/problem+json | no-store | - | - | * |  |
| v1 404 future date | `HEAD /api/v1/signals/2099-01-01.json` | 404 | 0 | application/problem+json | no-store | - | - | * |  |
| v1 404 unknown hash | `GET /api/v1/strategy-details/W_allweather_chandelier/0000000000000000000000000000000000000000000000000000000000000000.json` | 404 | 268 | application/problem+json | no-store | - | - | * |  |
| v1 404 unknown hash | `HEAD /api/v1/strategy-details/W_allweather_chandelier/0000000000000000000000000000000000000000000000000000000000000000.json` | 404 | 0 | application/problem+json | no-store | - | - | * |  |

### Other methods

| resource | request | status | body bytes | Content-Type | Cache-Control | ETag | Last-Modified | ACAO | Allow / Location |
|---|---|---|---|---|---|---|---|---|---|
| methods | `OPTIONS /api/v2/strategies` | 204 | 0 |  | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `POST /api/v2/strategies` | 405 | 155 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PUT /api/v2/strategies` | 405 | 155 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PATCH /api/v2/strategies` | 405 | 155 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `DELETE /api/v2/strategies` | 405 | 155 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `OPTIONS /api/v2/strategies/W_allweather_chandelier/revisions/3b49bb047658055efaaf7f5273f934187b50088f5082d846d67cc896a07ad61c` | 204 | 0 |  | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `POST /api/v2/strategies/W_allweather_chandelier/revisions/3b49bb047658055efaaf7f5273f934187b50088f5082d846d67cc896a07ad61c` | 405 | 254 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PUT /api/v2/strategies/W_allweather_chandelier/revisions/3b49bb047658055efaaf7f5273f934187b50088f5082d846d67cc896a07ad61c` | 405 | 254 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PATCH /api/v2/strategies/W_allweather_chandelier/revisions/3b49bb047658055efaaf7f5273f934187b50088f5082d846d67cc896a07ad61c` | 405 | 254 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `DELETE /api/v2/strategies/W_allweather_chandelier/revisions/3b49bb047658055efaaf7f5273f934187b50088f5082d846d67cc896a07ad61c` | 405 | 254 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `OPTIONS /api/v2/no-such-resource` | 204 | 0 |  | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `POST /api/v2/no-such-resource` | 405 | 161 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PUT /api/v2/no-such-resource` | 405 | 161 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PATCH /api/v2/no-such-resource` | 405 | 161 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `DELETE /api/v2/no-such-resource` | 405 | 161 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `OPTIONS /api/v1/signals/latest.json` | 204 | 0 |  | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `POST /api/v1/signals/latest.json` | 405 | 164 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PUT /api/v1/signals/latest.json` | 405 | 164 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PATCH /api/v1/signals/latest.json` | 405 | 164 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `DELETE /api/v1/signals/latest.json` | 405 | 164 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `OPTIONS /api/v1/signals/latest` | 204 | 0 |  | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `POST /api/v1/signals/latest` | 405 | 159 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PUT /api/v1/signals/latest` | 405 | 159 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `PATCH /api/v1/signals/latest` | 405 | 159 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |
| methods | `DELETE /api/v1/signals/latest` | 405 | 159 | application/problem+json | no-store | - | - | * | GET, HEAD, OPTIONS |

### Conditional requests, redirects, CORS preflight

| resource | request | status | body bytes | Content-Type | Cache-Control | ETag | Last-Modified | ACAO | Allow / Location |
|---|---|---|---|---|---|---|---|---|---|
| If-None-Match: "70bd82593adb95ae13bfbb3249268eac" | `GET /api/v2/strategies` | 304 | 0 |  | public, max-age=300, stale-while-revalidate=86400 | y | - |  |  |
| If-None-Match: "stale" | `GET /api/v2/strategies` | 200 | 35083 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| If-Modified-Since: Tue, 20 Oct 2026 19:11:50 GMT | `GET /api/v2/strategies` | 304 | 0 |  | public, max-age=300, stale-while-revalidate=86400 | y | - |  |  |
| If-None-Match: "e6b543776f214ed3ffe0e6d9b7621e71" | `GET /api/v2/signals/2026-10-19` | 304 | 0 |  | public, max-age=3600, s-maxage=31536000 | y | - |  |  |
| If-None-Match: "stale" | `GET /api/v2/signals/2026-10-19` | 200 | 11669 | application/json; charset=utf-8 | public, max-age=3600, s-maxage=31536000 | y | y | * |  |
| If-Modified-Since: Tue, 20 Oct 2026 19:11:58 GMT | `GET /api/v2/signals/2026-10-19` | 304 | 0 |  | public, max-age=3600, s-maxage=31536000 | y | - |  |  |
| If-None-Match: "38eda36cfeaedac66ac73302193a82cf" | `GET /api/v2/strategies/W_allweather_chandelier/revisions/3b49bb047658055efaaf7f5273f934187b50088f5082d846d67cc896a07ad61c` | 304 | 0 |  | public, max-age=31536000, immutable | y | - |  |  |
| If-None-Match: "stale" | `GET /api/v2/strategies/W_allweather_chandelier/revisions/3b49bb047658055efaaf7f5273f934187b50088f5082d846d67cc896a07ad61c` | 200 | 268332 | application/json; charset=utf-8 | public, max-age=31536000, immutable | y | y | * |  |
| If-Modified-Since: Tue, 20 Oct 2026 19:12:00 GMT | `GET /api/v2/strategies/W_allweather_chandelier/revisions/3b49bb047658055efaaf7f5273f934187b50088f5082d846d67cc896a07ad61c` | 304 | 0 |  | public, max-age=31536000, immutable | y | - |  |  |
| If-None-Match: "1d5f06f6c319740e4f1fe519918a9949" | `GET /api/v1/signals/latest.json` | 304 | 0 |  | public, max-age=300, stale-while-revalidate=60 | y | - |  |  |
| If-None-Match: "stale" | `GET /api/v1/signals/latest.json` | 200 | 9558 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=60 | y | y | * |  |
| If-Modified-Since: Tue, 20 Oct 2026 19:12:11 GMT | `GET /api/v1/signals/latest.json` | 304 | 0 |  | public, max-age=300, stale-while-revalidate=60 | y | - |  |  |
| trailing slash | `GET /api/v2/` | 308 | 15 | text/plain | public, max-age=0, must-revalidate | - | - |  |  Location: /api/v2 |
| legacy prices | `GET /prices/kr-000120.json` | 308 | 15 | text/plain | public, max-age=0, must-revalidate | - | - |  |  Location: /api/v2/instruments/kr-000120/prices |
| legacy trade marks | `GET /strategy-marks/kr-000120.json` | 308 | 15 | text/plain | public, max-age=0, must-revalidate | - | - |  |  Location: /api/v2/instruments/kr-000120/trade-marks |
| preflight from other origin | `OPTIONS /api/v2/strategies` | 204 | 0 |  | no-store | - | - | * | GET, HEAD, OPTIONS |
| simple CORS GET | `GET /api/v2/strategies` | 200 | 35083 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | y | * |  |
| swagger page | `GET /developers/swagger` | 200 | 2868 | text/html; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | y | - | * |  |

### Repeat requests: does the CDN cache any 404?

Three consecutive GETs per URI. 404s carry no `X-Vercel-Cache` at all: the guard (middleware) answers them
before the CDN cache and they are `no-store`. 200s are CDN `HIT`s with a growing `Age`.

| request | try | status | Content-Type | Cache-Control | X-Vercel-Cache | Age |
|---|---|---|---|---|---|---|
| `GET /api/v2/clubs%2Fewha` | 1 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/clubs%2Fewha` | 2 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/clubs%2Fewha` | 3 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/clubs%2fewha/reports` | 1 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/clubs%2fewha/reports` | 2 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/clubs%2fewha/reports` | 3 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/index` | 1 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/index` | 2 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/index` | 3 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/index.json` | 1 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/index.json` | 2 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/index.json` | 3 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/signals/2099-01-01` | 1 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/signals/2099-01-01` | 2 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/signals/2099-01-01` | 3 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v1/signals/2099-01-01.json` | 1 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v1/signals/2099-01-01.json` | 2 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v1/signals/2099-01-01.json` | 3 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/strategies/W_allweather_chandelier/revisions/0000000000000000000000000000000000000000000000000000000000000000` | 1 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/strategies/W_allweather_chandelier/revisions/0000000000000000000000000000000000000000000000000000000000000000` | 2 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/strategies/W_allweather_chandelier/revisions/0000000000000000000000000000000000000000000000000000000000000000` | 3 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v1/strategy-details/W_allweather_chandelier/0000000000000000000000000000000000000000000000000000000000000000.json` | 1 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v1/strategy-details/W_allweather_chandelier/0000000000000000000000000000000000000000000000000000000000000000.json` | 2 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v1/strategy-details/W_allweather_chandelier/0000000000000000000000000000000000000000000000000000000000000000.json` | 3 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/strategies/UNKNOWN` | 1 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/strategies/UNKNOWN` | 2 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/strategies/UNKNOWN` | 3 | 404 | application/problem+json | no-store |  |  |
| `GET /api/v2/strategies` | 1 | 200 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | HIT | 82 |
| `GET /api/v2/strategies` | 2 | 200 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | HIT | 83 |
| `GET /api/v2/strategies` | 3 | 200 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | HIT | 83 |
| `GET /api/v2/signals/2026-10-19` | 1 | 200 | application/json; charset=utf-8 | public, max-age=3600, s-maxage=31536000 | HIT | 75 |
| `GET /api/v2/signals/2026-10-19` | 2 | 200 | application/json; charset=utf-8 | public, max-age=3600, s-maxage=31536000 | HIT | 75 |
| `GET /api/v2/signals/2026-10-19` | 3 | 200 | application/json; charset=utf-8 | public, max-age=3600, s-maxage=31536000 | HIT | 76 |
| `GET /api/v1/signals/latest` | 1 | 200 | application/json | public, max-age=300 | HIT | 61 |
| `GET /api/v1/signals/latest` | 2 | 200 | application/json | public, max-age=300 | HIT | 61 |
| `GET /api/v1/signals/latest` | 3 | 200 | application/json | public, max-age=300 | HIT | 62 |

`GET /api/v2/clubs%2Fewha` → `{"title":"Not Found","status":404,…,"instance":"/api/v2/clubs%2Fewha"}`;
`GET /api/v2/index` → the same problem with `instance` `/api/v2/index`.
`/developers/swagger` → `SwaggerUIBundle({url: "/api/v2/openapi.json"})` on the production origin; the spec
offers `/` as a second server, and every parameter example is a published id
(`StrategyId` W_allweather_chandelier / D+_chandelier_optuna / T-_kospi_core_regime, `RevisionHash` the
headline's current revision, `InstrumentId` kr-000120 / us-alab, `ClubId` smic).

The automated contract test was also run against production:
`API_HTTP_BASE=https://verdict-archive.vercel.app uv run pytest tests/test_http_contract.py` → **51 passed**
before the v1 alias assertion below was added (that assertion now fails on production until the next deploy).

### Previously CDN-cached v1 404: fixed

Before (old deployment, section 4a): `GET /api/v1/signals/2099-01-01.json` → `404 text/html`,
`Cache-Control: public, max-age=3600, s-maxage=31536000`, `X-Vercel-Cache: HIT`. Now it is
`404 application/problem+json`, `no-store`, no `X-Vercel-Cache`, on all three repeats.

### Local (`next start`) vs CDN deltas

| Aspect | Local :3125 | Production CDN | Assessment |
|---|---|---|---|
| ETag | weak `W/"…"` | strong `"…"` (Vercel content hash) | both validate; `If-None-Match` and `If-Modified-Since` → `304` on both |
| `304` headers | `Cache-Control`, `ETag`, `Last-Modified`, CORS | `Cache-Control`, `ETag`; no `Last-Modified`, no CORS header | allowed (RFC 9110 §15.4.5); browsers reuse the stored response's CORS headers |
| `Content-Type` charset | `UTF-8` | `utf-8` | equivalent |
| 308 body / headers | 7–41 bytes, no `Cache-Control` | 15 bytes `text/plain`, `public, max-age=0, must-revalidate` | equivalent |
| Swagger page | no `ETag`, no CORS | `ETag`, `Access-Control-Allow-Origin: *` | harmless |
| v1 alias `/api/v1/signals/latest` `Cache-Control` | `public, max-age=300, stale-while-revalidate=60` | `public, max-age=300` | **defect (minor)**: the alias must match `/api/v1/signals/latest.json` (route comment). Vercel drops `stale-while-revalidate` from the prerendered route's own header. Fix in the working tree: a `next.config.ts` header rule for `/api/v1/signals/latest` (the config rules are applied by the platform router, as for the `.json` file); the live test now asserts the two are equal. Locally 51/51 pass; **unverified on Vercel until deployed**. |
| 404 for every kind, OPTIONS, 405 | `no-store` problem+json from the guard | identical | no delta |

## 4a. Production before the change (old deployment), captured 2026-10-20T12:59:52Z


The deployment predates this change (its `openapi.json` is `info.version` `2.0.0` with one server).

| request | status | Content-Type | Cache-Control | X-Vercel-Cache | Location / Link |
|---|---|---|---|---|---|
| `HEAD /api/v2` | 200 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | HIT |  |
| `HEAD /api/v2/strategies` | 200 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | HIT |  |
| `HEAD /api/v2/strategies/D+_chandelier_optuna` | 200 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | HIT |  |
| `HEAD /api/v2/strategies/D%2B_chandelier_optuna` | 200 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | HIT |  |
| `HEAD /api/v2/strategies?x=1` | 200 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | HIT |  |
| `HEAD /api/v2/signals/2026-10-19` | 200 | application/json; charset=utf-8 | public, max-age=3600, s-maxage=31536000 | MISS |  |
| `HEAD /api/v2/no-such-resource` | 404 | application/problem+json | no-store | MISS |  |
| `HEAD /api/v2/strategies/UNKNOWN` | 404 | application/problem+json | no-store | MISS |  |
| `HEAD /api/v2/signals/2099-01-01` | 404 | application/problem+json | no-store | MISS |  |
| `HEAD /api/v2/strategies/W_allweather_chandelier/revisions/0000000000000000000000000000000000000000000000000000000000000000` | 404 | application/problem+json | no-store | MISS | </api/v2/strategies/W_allweather_chandelier>; rel="up" |
| `HEAD /api/v1/signals/latest.json` | 200 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=60 | MISS | </api/v2>; rel="successor-version" |
| `HEAD /api/v1/signals/2099-01-01.json` | 404 | text/html; charset=utf-8 | public, max-age=3600, s-maxage=31536000 | HIT | </api/v2>; rel="successor-version" |
| `HEAD /api/v1/strategy-details/W_allweather_chandelier/0000000000000000000000000000000000000000000000000000000000000000.json` | 404 | application/problem+json | no-store | MISS | </api/v2>; rel="successor-version" |
| `HEAD /api/v2/` | 308 | text/plain | public, max-age=0, must-revalidate |  | /api/v2 |
| `HEAD /prices/kr-000120.json` | 308 | text/plain | public, max-age=0, must-revalidate |  | /api/v2/instruments/kr-000120/prices |
| `HEAD /api/v2/openapi.json` | 200 | application/json; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | HIT |  |
| `HEAD /developers/swagger` | 200 | text/html; charset=utf-8 | public, max-age=300, stale-while-revalidate=86400 | HIT |  |
| `HEAD /research/portfolio.json` | 404 | text/html; charset=utf-8 | public, max-age=0, must-revalidate | HIT |  |
| `HEAD /api/metadata/schema` | 503 | application/problem+json | no-store | MISS |  |
| `GET /api/v2/strategies` + `If-None-Match: "ec5580cc54a75c27f6f6fa642981188e"` | 304 | | | | |

- Vercel already sends `no-store` on v2 404s (function headers win there), but the v1 future-date 404 is a
  **CDN-cached HTML 404 with `s-maxage=31536000`** — fixed by this change once deployed (the guard answers it
  with a `no-store` problem+json).
- `/research/portfolio.json` is not deployed yet (404); `/api/metadata/schema` is `503` problem+json because the
  FastAPI sidecar is local/ops only — both as documented in `docs/API.md` §7.
- Not tested on production: OPTIONS and write methods (read-only rule), and anything about this change.

## 5. Contract cross-check (spec vs responses)

| Check | Result |
|---|---|
| Every spec path, called with the examples Swagger pre-fills, returns 200, the documented `Cache-Control`, `ETag`, CORS, and a body valid against the documented schema; HEAD and `If-None-Match` agree | pass (`tests/test_http_contract.py`, 17 paths) |
| Every 404 kind validates against `ProblemDetails` and the `NotFound` response's documented headers | pass (6 URI kinds) |
| Every parameter example names a published resource | **failed before**: `InstrumentId` example `kr-000020` was not published, so Swagger "Try it out" defaulted to a 404 on all four instrument operations; `StrategyId`'s first example `D+_chandelier_optuna` did not own the `RevisionHash` example (a `W_allweather_chandelier` hash), so the revision operation defaulted to a 404. Now every example is drawn from published ids at export time (headline strategy first, the traded sample instrument first). |
| `NotFound` response documented its headers | **missing before**: now documents `Cache-Control: no-store` and CORS; the example `detail` now equals the real one |
| Swagger page loads the spec | `/developers/swagger` → `SwaggerUIBundle({url: "/api/v2/openapi.json"})`, same origin, 200; the three pinned cdnjs files match their SRI hashes (recomputed 2026-10-20) |
| Swagger "Try it out" target | before: only the production server, so a local or preview Swagger silently queried production. Now a second server `/` ("this deployment") is offered; production stays first because `/developers` uses `servers[0]` as its base URL. Browser rendering itself was not automated. |

Spec changes: `info.version` 2.0.0 → 2.0.1 (also `data.api_version` in `/api/v2`), `servers` +1, parameter
examples, `NotFound` headers, error-caching text. No document field changed, so `schema_version` stays `2.0`.

## 6. Commands

```bash
# Private build (never touches the .next of the server on :3100)
bash sync-build-tree.sh        # copies src/, configs; hardlinks public/ and node_modules/
cd scratchpad/build-tree && npx next build   # BelowNormal priority, 8-core affinity, experimental.cpus=4
npx next start -H 127.0.0.1 -p 3125

# Matrix (script below), production probes, automated checks
bash matrix.sh http://127.0.0.1:3125
API_HTTP_PROJECT=scratchpad/build-tree uv run pytest tests/test_http_contract.py   # 48 passed
node --test tests/api-http.test.cjs                                                  # 28 passed
uv run python scripts/export_api_v2.py --check
```

Production probe (HEAD only, one per URI): `curl -s -m 20 -I "https://verdict-archive.vercel.app$URI"`, plus
`curl -s -o /dev/null -w '%{http_code}' -H "If-None-Match: $ETAG" https://verdict-archive.vercel.app/api/v2/strategies`.

`matrix.sh`:

```bash
#!/usr/bin/env bash
# HTTP evidence matrix: one row per (resource, method). Usage: matrix.sh http://127.0.0.1:3125
B=${1:-http://127.0.0.1:3125}
H=$(printf '%064d' 0)
REV=$(ls public/api/v2/strategies/W_allweather_chandelier/revisions | head -1 | sed 's/.json$//')
row() { # label method uri [extra curl args...]
  local label=$1 m=$2 u=$3; shift 3
  local hdr; hdr=$(mktemp)
  local out
  if [ "$m" = HEAD ]; then
    out=$(curl -s -I -o /dev/null -D "$hdr" -w '%{http_code} %{size_download}' "$@" "$B$u")
  else
    out=$(curl -s -X "$m" -o /dev/null -D "$hdr" -w '%{http_code} %{size_download}' "$@" "$B$u")
  fi
  g() { grep -i "^$1:" "$hdr" | head -1 | cut -d' ' -f2- | tr -d '\r'; }
  printf '| %s | `%s %s` | %s | %s | %s | %s | %s | %s | %s | %s |\n' "$label" "$m" "$u" "${out% *}" "${out#* }" \
    "$(g content-type)" "$(g cache-control)" "$( [ -n "$(g etag)" ] && echo y || echo -)" \
    "$( [ -n "$(g last-modified)" ] && echo y || echo -)" "$(g access-control-allow-origin)" "$(g allow)$( [ -n "$(g location)" ] && echo " Location: $(g location)")"
  rm -f "$hdr"
}
echo '| resource | request | status | body bytes | Content-Type | Cache-Control | ETag | Last-Modified | ACAO | Allow / Location |'
echo '|---|---|---|---|---|---|---|---|---|---|'
for spec in \
  "entry point|/api/v2" \
  "openapi|/api/v2/openapi.json" \
  "collection|/api/v2/strategies" \
  "item (+ literal)|/api/v2/strategies/D+_chandelier_optuna" \
  "item (%2B)|/api/v2/strategies/D%2B_chandelier_optuna" \
  "item (.json)|/api/v2/strategies/D+_chandelier_optuna.json" \
  "item + query|/api/v2/strategies/D+_chandelier_optuna?foo=1&bar" \
  "latest snapshot|/api/v2/signals/latest" \
  "dated snapshot|/api/v2/signals/2026-10-19" \
  "revision|/api/v2/strategies/W_allweather_chandelier/revisions/$REV" \
  "prices|/api/v2/instruments/kr-000120/prices" "trade marks|/api/v2/instruments/kr-000120/trade-marks" "404 unpublished instrument prices|/api/v2/instruments/kr-005930/prices" \
  "404 unknown|/api/v2/no-such-resource" \
  "404 unknown id|/api/v2/strategies/UNKNOWN" \
  "404 future date|/api/v2/signals/2099-01-01" \
  "404 unknown hash|/api/v2/strategies/W_allweather_chandelier/revisions/$H" \
  "v1 latest (deprecated)|/api/v1/signals/latest.json" \
  "v1 latest alias|/api/v1/signals/latest" \
  "v1 dated|/api/v1/signals/2026-10-19.json" \
  "v1 revision alias|/api/v1/strategy-details/W_allweather_chandelier/$REV.json" \
  "v1 404 future date|/api/v1/signals/2099-01-01.json" \
  "v1 404 unknown hash|/api/v1/strategy-details/W_allweather_chandelier/$H.json" \
  ; do
  label=${spec%%|*}; uri=${spec#*|}
  for m in GET HEAD; do row "$label" "$m" "$uri"; done
done
echo
echo '### Other methods'
echo
echo '| resource | request | status | body bytes | Content-Type | Cache-Control | ETag | Last-Modified | ACAO | Allow / Location |'
echo '|---|---|---|---|---|---|---|---|---|---|'
for uri in /api/v2/strategies "/api/v2/strategies/W_allweather_chandelier/revisions/$REV" /api/v2/no-such-resource /api/v1/signals/latest.json /api/v1/signals/latest; do
  for m in OPTIONS POST PUT PATCH DELETE; do row "methods" "$m" "$uri"; done
done
echo
echo '### Conditional requests, redirects, CORS preflight'
echo
echo '| resource | request | status | body bytes | Content-Type | Cache-Control | ETag | Last-Modified | ACAO | Allow / Location |'
echo '|---|---|---|---|---|---|---|---|---|---|'
for uri in /api/v2/strategies /api/v2/signals/2026-10-19 "/api/v2/strategies/W_allweather_chandelier/revisions/$REV" /api/v1/signals/latest.json; do
  et=$(curl -s -I "$B$uri" | grep -i '^etag:' | cut -d' ' -f2- | tr -d '\r')
  lm=$(curl -s -I "$B$uri" | grep -i '^last-modified:' | cut -d' ' -f2- | tr -d '\r')
  row "If-None-Match: $et" GET "$uri" -H "If-None-Match: $et"
  row "If-None-Match: \"stale\"" GET "$uri" -H 'If-None-Match: "stale"'
  [ -n "$lm" ] && row "If-Modified-Since: $lm" GET "$uri" -H "If-Modified-Since: $lm"
done
row "trailing slash" GET /api/v2/
row "legacy prices" GET /prices/kr-000120.json
row "legacy trade marks" GET /strategy-marks/kr-000120.json
row "preflight from other origin" OPTIONS /api/v2/strategies -H 'Origin: https://example.org' -H 'Access-Control-Request-Method: GET'
row "simple CORS GET" GET /api/v2/strategies -H 'Origin: https://example.org'
row "swagger page" GET /developers/swagger
```
