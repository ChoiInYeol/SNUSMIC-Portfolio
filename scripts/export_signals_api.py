"""Export the frozen, deprecated /api/v1 static JSON files from strategy-backtest.json.

Run this immediately after backtest_momentum.py has written
src/data/strategy-backtest.json.  It produces:

  public/api/v1/signals/latest.json        — today's trading signals
  public/api/v1/signals/{YYYY-MM-DD}.json  — immutable daily snapshot
  public/api/v1/strategies.json            — all strategy IS/OOS metrics
  public/api/v1/trades/{strategy_key}.json — closed-trade log per strategy
  public/api/v1/strategy-details/manifest.json
  public/api/v1/openapi.json               — OpenAPI 3.1 spec (every operation deprecated → /api/v2)
  public/api/v2/strategies/{key}/revisions/{sha256}.json
                                           — content-addressed strategy details, shared with v2;
                                             /api/v1/strategy-details/{key}/{sha256}.json is a
                                             next.config.ts rewrite to these exact bytes

Headline-strategy trade marks moved to /api/v2/instruments/{id}/trade-marks
(scripts/export_api_v2.py, built from build_strategy_marks below).

Usage:
    python scripts/export_signals_api.py
    python scripts/export_signals_api.py --source src/data/strategy-backtest.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import hashlib
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "src" / "data" / "strategy-backtest.json"
API_DIR = ROOT / "public" / "api" / "v1"
# v1 strategy-detail URLs are served from the v2 revision files (identical bytes, no duplication).
REVISIONS_DIR = ROOT / "public" / "api" / "v2" / "strategies"

SCHEMA_VERSION = "1.0"

DISCLAIMER_EN = (
    "These are backtested signals for research purposes only. "
    "They do NOT constitute investment advice. "
    "Past performance does not guarantee future results. "
    "All trading decisions and their consequences are solely your responsibility."
)
DISCLAIMER_KO = (
    "이 데이터는 백테스트 시뮬레이션 결과이며 연구 목적으로만 제공됩니다. "
    "투자 권유가 아닙니다. 과거 성과가 미래 수익을 보장하지 않습니다. "
    "실매매로 인한 모든 손익의 책임은 전적으로 사용자에게 있습니다."
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ── helpers ─────────────────────────────────────────────────────────────────


def _load_source(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    multi = data.get("multi_strategy") if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(multi, dict) or not isinstance(multi.get("strategies"), list):
        raise ValueError("source schema requires multi_strategy.strategies array")
    if not isinstance(data.get("params"), dict) or not _finite_tree(data):
        raise ValueError("source schema missing params or contains non-finite numeric value")
    return data

def _finite_tree(value: object) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_finite_tree(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _finite_tree(v) for k, v in value.items())
    return False


def _write_json(path: Path, obj: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            f.flush()
        Path(temp_name).replace(path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
    print(f"  wrote {path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path}", flush=True)


def _generated_at() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ── signals/latest.json ─────────────────────────────────────────────────────

def build_latest_signals(data: dict) -> dict:
    """Map the internal signals block to the public API shape."""
    sig = data.get("signals", {})
    params = data.get("params", {})
    regime_raw = sig.get("regime") or {}

    # open_positions: map internal fields → public fields
    def _map_position(p: dict) -> dict:
        return {
            "ticker": p.get("ticker", ""),
            "market": p.get("market", "KR"),
            "name": p.get("display_name", p.get("ticker", "")),
            "entry_date": p.get("entry_date", ""),
            "entry_price": p.get("entry_price"),
            "current_price": p.get("current_price"),
            "stop_level": p.get("stop_level"),
            "unrealized_pct": p.get("unrealized_pct"),
            "days_held": p.get("days_elapsed"),
            "entry_reason": p.get("entry_reason", ""),
            "trigger_reports": p.get("trigger_reports", []),
        }

    # buy_signals from imminent_buys
    def _map_buy(b: dict) -> dict:
        return {
            "ticker": b.get("ticker", ""),
            "market": b.get("market", "KR"),
            "name": b.get("display_name", b.get("ticker", "")),
            "signal_date": b.get("entry_basis_date") or sig.get("as_of", ""),
            "entry_basis_price": b.get("entry_basis_price"),
            "entry_reason": b.get("entry_reason", ""),
            "trigger_reports": b.get("trigger_reports", []),
        }

    # sell_signals from approaching_stop
    def _map_sell(s: dict) -> dict:
        return {
            "ticker": s.get("ticker", ""),
            "market": s.get("market", "KR"),
            "name": s.get("display_name", s.get("ticker", "")),
            "reason": "approaching_stop" if not s.get("stop_hit") else "stop_hit",
            "stop_level": s.get("stop_level"),
            "dist_to_stop_pct": s.get("dist_to_stop_pct"),
        }

    open_positions = [_map_position(p) for p in sig.get("open_positions", [])]
    approaching = sig.get("approaching_stop", [])
    sell_signals = [_map_sell(s) for s in approaching]
    buy_signals = [_map_buy(b) for b in sig.get("imminent_buys", [])]

    # parking note for regime
    regime_state = regime_raw.get("state", "ON")
    parking_label = "allweather" if params.get("headline_key", "").startswith("W_") else "kospi"

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": sig.get("as_of", ""),
        "generated_at": _generated_at(),
        "headline_strategy": sig.get("headline_strategy", params.get("headline_key", "")),
        "disclaimer": DISCLAIMER_EN,
        "disclaimer_ko": DISCLAIMER_KO,
        "regime": {
            "kospi_above_200ma": regime_state == "ON",
            "state": regime_state,
            "kospi_close": regime_raw.get("kospi_close"),
            "kospi_ma200": regime_raw.get("kospi_ma200"),
            "parking": parking_label,
            "applies": regime_raw.get("applies", False),
        },
        "slots": sig.get("slots", {}),
        "open_positions": open_positions,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
        "counts": {
            "open_positions": len(open_positions),
            "buy_signals": len(buy_signals),
            "sell_signals": len(sell_signals),
        },
    }


# ── strategies.json ──────────────────────────────────────────────────────────

def build_strategies(data: dict) -> dict:
    ms = data.get("multi_strategy", {})
    headline_key = ms.get("headline_key", data.get("params", {}).get("headline_key", ""))
    strategies = []
    for s in ms.get("strategies", []):
        metrics = s.get("metrics", {})
        is_ = s.get("in_sample", {})
        oos = s.get("out_of_sample", {})
        strategies.append({
            "key": s.get("key", ""),
            "label": s.get("label", ""),
            "is_headline": s.get("key", "") == headline_key,
            "trade_count": s.get("trade_count"),
            "metrics": {
                "total_return_pct": metrics.get("total_return_pct"),
                "cagr_pct": metrics.get("cagr_pct"),
                "sharpe": metrics.get("sharpe"),
                "mdd_pct": metrics.get("mdd_pct"),
                "win_rate_pct": metrics.get("win_rate_pct"),
                "avg_hold_days": metrics.get("avg_hold_days"),
            },
            "in_sample": {
                "period": f"{is_.get('start', '')} ~ {is_.get('end', '')}",
                "total_return_pct": is_.get("total_return_pct"),
                "cagr_pct": is_.get("cagr_pct"),
                "sharpe": is_.get("sharpe"),
                "mdd_pct": is_.get("mdd_pct"),
            },
            "out_of_sample": {
                "period": f"{oos.get('start', '')} ~ {oos.get('end', '')}",
                "total_return_pct": oos.get("total_return_pct"),
                "cagr_pct": oos.get("cagr_pct"),
                "sharpe": oos.get("sharpe"),
                "mdd_pct": oos.get("mdd_pct"),
            },
            "kospi_dca_ratio": s.get("kospi_dca_ratio"),
            "kospi_dca_beats": s.get("kospi_dca_beats"),
            "aw_dca_ratio": s.get("aw_dca_ratio"),
            "aw_dca_beats": s.get("aw_dca_beats"),
            # v24: 다중검정 보정 + 워크포워드 일관성
            "dsr": s.get("dsr"),
            "walkforward": s.get("walkforward"),
            "walkforward_oos": s.get("walkforward_oos"),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _generated_at(),
        "headline_key": headline_key,
        "disclaimer": DISCLAIMER_EN,
        "disclaimer_ko": DISCLAIMER_KO,
        "strategies": strategies,
    }


# ── trades/{strategy_key}.json ───────────────────────────────────────────────

def build_trades(data: dict) -> dict[str, list[dict]]:
    """Returns {strategy_key: [trade, ...]}."""
    ms = data.get("multi_strategy", {})
    tbs = ms.get("trades_by_strategy", {})
    result: dict[str, list[dict]] = {}
    for key, trades in tbs.items():
        mapped = []
        for t in trades:
            mapped.append({
                "ticker": t.get("ticker", ""),
                "market": t.get("market", "KR"),
                "name": t.get("display_name", t.get("ticker", "")),
                "entry_date": t.get("entry_date", ""),
                "exit_date": t.get("exit_date", ""),
                "entry_price": t.get("entry"),
                "exit_price": t.get("exit"),
                "return_pct": t.get("return_pct"),
                "days": t.get("days"),
                "exit_reason": t.get("exit_reason", ""),
                "entry_reason": t.get("entry_reason", ""),
                "trigger_reports": t.get("trigger_reports", []),
            })
        result[key] = mapped
    return result

def build_strategy_details(data: dict) -> dict[str, tuple[str, dict]]:
    """Hash the actual producer curves and raw UI trade contract, never placeholders."""
    ms = data["multi_strategy"]
    fields = {
        "nav": "equity_by_strategy",
        "wealth": "strategy_wealth_sims",
        "trades": "trades_by_strategy",
        "open_positions": "open_positions_by_strategy",
        "yearly": "yearly_by_strategy",
    }
    result: dict[str, tuple[str, dict]] = {}
    for strategy in ms["strategies"]:
        key = strategy["key"]
        if (not isinstance(key, str) or not key or not key[0].isalnum()
                or not key.isascii() or any(not (c.isalnum() or c in "_+.-") for c in key)):
            raise ValueError("invalid strategy asset key")
        payload = {"schema_version": SCHEMA_VERSION, "strategy_key": key}
        for field, source in fields.items():
            value = ms[source][key]
            expected_type = dict if field == "wealth" else list
            if not isinstance(value, expected_type):
                raise ValueError(f"{key}: invalid {source} contract")
            payload[field] = value
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        digest = hashlib.sha256(canonical).hexdigest()
        payload["content_hash"] = digest
        result[key] = (digest, payload)
    return result


# ── headline-strategy trade marks (served by v2 as /instruments/{id}/trade-marks) ──

def _exit_side(exit_reason: str) -> str:
    """Stop-style exits (chandelier/trailing/stop) → 'stop', everything else → 'sell'."""
    low = (exit_reason or "").lower()
    if "chandelier" in low or "stop" in low or "trail" in low:
        return "stop"
    return "sell"


def build_strategy_marks(data: dict) -> dict[str, dict]:
    """Per-ticker trade marks for the HEADLINE strategy.

    Returns {slug: payload} where slug is the v2 instrument id
    (\"{market}-{ticker}\".lower()).  Each payload carries the full buy/sell/stop
    mark list from the backtest trade log plus the current trailing-stop level
    for tickers still held.
    """
    ms = data.get("multi_strategy", {})
    sig = data.get("signals", {})
    tbs = ms.get("trades_by_strategy", {})
    # Prefer the key the public signals payload advertises (what users see on
    # latest.json); fall back to multi_strategy.headline_key.
    headline = sig.get("headline_strategy", "")
    if headline not in tbs:
        headline = ms.get("headline_key", data.get("params", {}).get("headline_key", ""))
    trades = tbs.get(headline, [])
    as_of = sig.get("as_of", "")

    per: dict[str, dict] = {}

    def _bucket(market: str, ticker: str, name: str) -> dict:
        slug = f"{market or 'KR'}-{ticker}".lower()
        return per.setdefault(slug, {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _generated_at(),
            "strategy_key": headline,
            "ticker": ticker,
            "market": market or "KR",
            "name": name or ticker,
            "disclaimer_ko": DISCLAIMER_KO,
            "marks": [],
            "open_stop": None,
        })

    # Closed trades → entry (buy) + exit (sell|stop) marks
    for t in trades:
        ticker = t.get("ticker", "")
        if not ticker:
            continue
        entry = _bucket(t.get("market", "KR"), ticker, t.get("display_name", ticker))
        if t.get("entry_date") and t.get("entry") is not None:
            entry["marks"].append({
                "date": t["entry_date"],
                "side": "buy",
                "price": t.get("entry"),
                "reason": t.get("entry_reason", ""),
            })
        if t.get("exit_date") and t.get("exit") is not None:
            ret = t.get("return_pct")
            ret_txt = f" ({ret:+.1f}%)" if isinstance(ret, (int, float)) else ""
            entry["marks"].append({
                "date": t["exit_date"],
                "side": _exit_side(t.get("exit_reason", "")),
                "price": t.get("exit"),
                "reason": f"{t.get('exit_reason', '')}{ret_txt}".strip(),
            })

    # Open positions → entry mark + current trailing-stop level
    for p in sig.get("open_positions", []):
        ticker = p.get("ticker", "")
        if not ticker:
            continue
        entry = _bucket(p.get("market", "KR"), ticker, p.get("display_name", ticker))
        if p.get("entry_date") and p.get("entry_price") is not None:
            entry["marks"].append({
                "date": p["entry_date"],
                "side": "buy",
                "price": p.get("entry_price"),
                "reason": p.get("entry_reason", ""),
            })
        if p.get("stop_level") is not None:
            entry["open_stop"] = {
                "stop_level": p.get("stop_level"),
                "entry_date": p.get("entry_date", ""),
                "as_of": as_of,
            }

    for payload in per.values():
        payload["marks"].sort(key=lambda m: (m["date"], 0 if m["side"] == "buy" else 1))

    return per


# ── openapi.json ─────────────────────────────────────────────────────────────

# v1 operation → its v2 successor, stated on each deprecated operation.
V2_SUCCESSORS = {
    "/signals/latest.json": "/api/v2/signals/latest",
    "/signals/{date}.json": "/api/v2/signals/{date}",
    "/strategies.json": "/api/v2/strategies",
    "/trades/{strategy_key}.json": "/api/v2/strategies/{strategy_id}/trades",
    "/signals/latest": "/api/v2/signals/latest",
    "/forward.json": "/api/v2/track-record",
    "/strategy-details/manifest.json": "/api/v2/strategies/{strategy_id} (links.current_revision)",
    "/strategy-details/{strategy_key}/{content_hash}.json": "/api/v2/strategies/{strategy_id}/revisions/{sha256}",
    "/openapi.json": "/api/v2/openapi.json",
}


def _deprecate(spec: dict) -> dict:
    for route, item in spec["paths"].items():
        op = item["get"]
        op["deprecated"] = True
        note = f"Deprecated: use `GET {V2_SUCCESSORS[route]}`."
        op["description"] = f"{note} {op['description']}" if op.get("description") else note
    return spec


def build_openapi(as_of: str) -> dict:
    # 라이브 도메인 — layout.tsx metadataBase와 반드시 일치 (과거 smic-easy 표기는 stale)
    base = "https://verdict-archive.vercel.app"
    return _deprecate({
        "openapi": "3.1.0",
        "info": {
            "title": "SMIC Strategy Signal API",
            "version": SCHEMA_VERSION,
            "description": (
                "DEPRECATED — frozen since 2026-10-19; use /api/v2 (spec: /api/v2/openapi.json). "
                "Responses carry `Deprecation` and `Link: </api/v2>; rel=\"successor-version\"` headers. "
                "Static JSON endpoints exposing backtested trading signals from the "
                "SMIC (Student Managed Investment Club) momentum strategy research pipeline. "
                f"Last updated: {as_of}. "
                + DISCLAIMER_EN
            ),
            "contact": {"url": base},
            "license": {"name": "MIT"},
        },
        "servers": [{"url": f"{base}/api/v1", "description": "Production (Vercel static)"}],
        "paths": {
            "/signals/latest.json": {
                "get": {
                    "operationId": "getLatestSignals",
                    "summary": "Latest trading signals",
                    "description": (
                        "Returns the most recent open positions, buy signals, and sell alerts "
                        "for the headline strategy. Regenerated daily by the CI pipeline."
                    ),
                    "responses": {
                        "200": {
                            "description": "Signal payload",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SignalsPayload"}}},
                        }
                    },
                }
            },
            "/signals/{date}.json": {
                "get": {
                    "operationId": "getSignalsByDate",
                    "summary": "Daily signal snapshot",
                    "description": "Immutable daily snapshot. Date format: YYYY-MM-DD.",
                    "parameters": [
                        {
                            "name": "date",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "date", "example": as_of},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Signal payload for that date",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SignalsPayload"}}},
                        },
                        "404": {"description": "Snapshot not yet generated for this date"},
                    },
                }
            },
            "/strategies.json": {
                "get": {
                    "operationId": "getStrategies",
                    "summary": "All strategies with IS/OOS metrics",
                    "responses": {
                        "200": {
                            "description": "Strategy list",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/StrategiesPayload"}}},
                        }
                    },
                }
            },
            "/trades/{strategy_key}.json": {
                "get": {
                    "operationId": "getTradesByStrategy",
                    "summary": "Closed trade log for a strategy",
                    "parameters": [
                        {
                            "name": "strategy_key",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "example": "W_allweather_chandelier"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Trade list",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TradesPayload"}}},
                        }
                    },
                }
            },
            "/signals/latest": {
                "get": {
                    "operationId": "getLatestSignalsAlias",
                    "summary": "Latest trading signals (extensionless alias of /signals/latest.json)",
                    "responses": {
                        "200": {
                            "description": "Signal payload",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SignalsPayload"}}},
                        }
                    },
                }
            },
            "/forward.json": {
                "get": {
                    "operationId": "getForwardRecord",
                    "summary": "Forward (out-of-sample) record of published buy signals",
                    "responses": {
                        "200": {
                            "description": "Forward record",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ForwardRecord"}}},
                        }
                    },
                }
            },
            "/strategy-details/manifest.json": {
                "get": {
                    "operationId": "getStrategyDetailsManifest",
                    "summary": "Current content hash and path of each strategy detail asset",
                    "responses": {
                        "200": {
                            "description": "Manifest",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/StrategyDetailsManifest"}}},
                        }
                    },
                }
            },
            "/strategy-details/{strategy_key}/{content_hash}.json": {
                "get": {
                    "operationId": "getStrategyDetail",
                    "summary": "Immutable, content-addressed NAV, wealth, trades and open positions of one strategy",
                    "parameters": [
                        {"name": "strategy_key", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "content_hash", "in": "path", "required": True,
                         "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Strategy detail",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/StrategyDetail"}}},
                        },
                        "404": {"description": "Unknown strategy or hash"},
                    },
                }
            },
            "/openapi.json": {
                "get": {
                    "operationId": "getOpenApiSpec",
                    "summary": "This OpenAPI spec",
                    "responses": {"200": {"description": "OpenAPI 3.1 document"}},
                }
            },
        },
        "components": {
            "schemas": {
                "Position": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "market": {"type": "string", "enum": ["KR", "US"]},
                        "name": {"type": "string"},
                        "entry_date": {"type": "string", "format": "date"},
                        "entry_price": {"type": ["number", "null"]},
                        "current_price": {"type": ["number", "null"]},
                        "stop_level": {"type": ["number", "null"]},
                        "unrealized_pct": {"type": ["number", "null"]},
                        "days_held": {"type": ["integer", "null"]},
                        "entry_reason": {"type": "string"},
                        "trigger_reports": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "BuySignal": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "market": {"type": "string", "enum": ["KR", "US"]},
                        "name": {"type": "string"},
                        "signal_date": {"type": ["string", "null"], "format": "date", "description": "null only in snapshots published before 2026-09-10"},
                        "entry_basis_price": {"type": ["number", "null"]},
                        "entry_reason": {"type": "string"},
                        "trigger_reports": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "SellSignal": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "market": {"type": "string", "enum": ["KR", "US"]},
                        "name": {"type": "string"},
                        "reason": {"type": "string", "enum": ["approaching_stop", "stop_hit"]},
                        "stop_level": {"type": ["number", "null"]},
                        "dist_to_stop_pct": {"type": ["number", "null"]},
                    },
                },
                "Regime": {
                    "type": "object",
                    "properties": {
                        "kospi_above_200ma": {"type": "boolean"},
                        "state": {"type": "string", "enum": ["ON", "OFF"]},
                        "kospi_close": {"type": ["number", "null"]},
                        "kospi_ma200": {"type": ["number", "null"]},
                        "parking": {"type": "string", "enum": ["allweather", "kospi", "cash"]},
                        "applies": {"type": "boolean"},
                    },
                },
                "SignalsPayload": {
                    "type": "object",
                    "required": ["schema_version", "as_of", "generated_at", "headline_strategy", "disclaimer"],
                    "properties": {
                        "schema_version": {"type": "string"},
                        "as_of": {"type": "string", "format": "date"},
                        "generated_at": {"type": "string", "format": "date-time"},
                        "headline_strategy": {"type": "string"},
                        "disclaimer": {"type": "string"},
                        "disclaimer_ko": {"type": "string"},
                        "regime": {"$ref": "#/components/schemas/Regime"},
                        "slots": {
                            "type": "object",
                            "properties": {
                                "max_positions": {"type": "integer"},
                                "open": {"type": "integer"},
                                "available": {"type": "integer"},
                            },
                        },
                        "open_positions": {"type": "array", "items": {"$ref": "#/components/schemas/Position"}},
                        "buy_signals": {"type": "array", "items": {"$ref": "#/components/schemas/BuySignal"}},
                        "sell_signals": {"type": "array", "items": {"$ref": "#/components/schemas/SellSignal"}},
                        "counts": {
                            "type": "object",
                            "properties": {
                                "open_positions": {"type": "integer"},
                                "buy_signals": {"type": "integer"},
                                "sell_signals": {"type": "integer"},
                            },
                        },
                    },
                },
                "StrategyEntry": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "label": {"type": "string"},
                        "is_headline": {"type": "boolean"},
                        "trade_count": {"type": "integer"},
                        "metrics": {"type": "object"},
                        "in_sample": {"type": "object"},
                        "out_of_sample": {"type": "object"},
                        "kospi_dca_ratio": {"type": ["number", "null"]},
                        "kospi_dca_beats": {"type": ["boolean", "null"]},
                        "aw_dca_ratio": {"type": ["number", "null"]},
                        "aw_dca_beats": {"type": ["boolean", "null"]},
                        "dsr": {"type": ["object", "null"], "description": "Probabilistic / deflated Sharpe ratio"},
                        "walkforward": {"type": ["object", "null"], "description": "Rolling-window walk-forward summary"},
                        "walkforward_oos": {"type": ["object", "null"], "description": "Walk-forward summary, OOS windows only"},
                    },
                },
                "StrategiesPayload": {
                    "type": "object",
                    "properties": {
                        "schema_version": {"type": "string"},
                        "generated_at": {"type": "string", "format": "date-time"},
                        "headline_key": {"type": "string"},
                        "disclaimer": {"type": "string"},
                        "disclaimer_ko": {"type": "string"},
                        "strategies": {"type": "array", "items": {"$ref": "#/components/schemas/StrategyEntry"}},
                    },
                },
                "Trade": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "market": {"type": "string", "enum": ["KR", "US"]},
                        "name": {"type": "string"},
                        "entry_date": {"type": "string", "format": "date"},
                        "exit_date": {"type": "string", "format": "date"},
                        "entry_price": {"type": ["number", "null"]},
                        "exit_price": {"type": ["number", "null"]},
                        "return_pct": {"type": ["number", "null"]},
                        "days": {"type": ["integer", "null"]},
                        "exit_reason": {"type": "string"},
                        "entry_reason": {"type": "string"},
                        "trigger_reports": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "TradesPayload": {
                    "type": "object",
                    "properties": {
                        "schema_version": {"type": "string"},
                        "generated_at": {"type": "string", "format": "date-time"},
                        "strategy_key": {"type": "string"},
                        "disclaimer": {"type": "string"},
                        "disclaimer_ko": {"type": "string"},
                        "trades": {"type": "array", "items": {"$ref": "#/components/schemas/Trade"}},
                    },
                },
                "StrategyDetailsManifest": {
                    "type": "object",
                    "required": ["schema_version", "as_of", "assets"],
                    "properties": {
                        "schema_version": {"type": "string"},
                        "as_of": {"type": "string", "format": "date"},
                        "assets": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "object",
                                "required": ["content_hash", "path"],
                                "properties": {"content_hash": {"type": "string"}, "path": {"type": "string"}},
                            },
                        },
                    },
                },
                "StrategyDetail": {
                    "type": "object",
                    "required": ["schema_version", "strategy_key", "content_hash"],
                    "description": "content_hash = sha256 of the canonical JSON of the payload without content_hash.",
                    "properties": {
                        "schema_version": {"type": "string"},
                        "strategy_key": {"type": "string"},
                        "content_hash": {"type": "string"},
                        "nav": {"type": "array", "items": {"type": "object"}},
                        "wealth": {"type": "object"},
                        "trades": {"type": "array", "items": {"type": "object"}},
                        "open_positions": {"type": "array", "items": {"type": "object"}},
                        "yearly": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "ForwardRecord": {
                    "type": "object",
                    "required": ["schema_version", "generated_at", "summary", "entries"],
                    "properties": {
                        "schema_version": {"type": "string"},
                        "generated_at": {"type": "string", "format": "date-time"},
                        "method": {"type": "string"},
                        "provenance": {"type": "object"},
                        "summary": {"type": "object"},
                        "entries": {"type": "array", "items": {"type": "object"}},
                        "headline_by_date": {"type": "object", "additionalProperties": {"type": "string"}},
                    },
                },
            }
        },
    })


# ── main ─────────────────────────────────────────────────────────────────────

def _manifest(as_of: str, details: dict[str, tuple[str, dict]]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "assets": {key: {"content_hash": digest, "path": f"strategy-details/{key}/{digest}.json"}
                   for key, (digest, _) in details.items()},
    }


def plan_outputs(data: dict, *, details_only: bool = False) -> tuple[dict[Path, dict | list], set[Path]]:
    """Every file this export owns → payload, plus the set of append-only paths.

    Append-only paths (dated snapshots, content-addressed details) are written
    only when missing and must never change once published.
    """
    as_of: str = data.get("signals", {}).get("as_of", dt.date.today().isoformat())
    outputs: dict[Path, dict | list] = {}
    append_only: set[Path] = set()
    # Immutable content-addressed detail assets; never prune old hashes.
    details = build_strategy_details(data)
    for key, (digest, payload) in details.items():
        path = REVISIONS_DIR / key / "revisions" / f"{digest}.json"
        outputs[path] = payload
        append_only.add(path)
    outputs[API_DIR / "strategy-details" / "manifest.json"] = _manifest(as_of, details)
    if details_only:
        return outputs, append_only

    latest = build_latest_signals(data)
    outputs[API_DIR / "signals" / "latest.json"] = latest
    daily_path = API_DIR / "signals" / f"{as_of}.json"
    outputs[daily_path] = latest
    append_only.add(daily_path)
    outputs[API_DIR / "strategies.json"] = build_strategies(data)
    for key, trades in build_trades(data).items():
        outputs[API_DIR / "trades" / f"{key}.json"] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _generated_at(),
            "strategy_key": key,
            "disclaimer": DISCLAIMER_EN,
            "disclaimer_ko": DISCLAIMER_KO,
            "trades": trades,
        }
    outputs[API_DIR / "openapi.json"] = build_openapi(as_of)
    return outputs, append_only


def _comparable(value: object) -> object:
    """Drop wall-clock stamps so a re-export of the same source compares equal."""
    if isinstance(value, dict):
        return {k: _comparable(v) for k, v in value.items() if k != "generated_at"}
    if isinstance(value, list):
        return [_comparable(v) for v in value]
    return value


def check_outputs(outputs: dict[Path, dict | list], append_only: set[Path]) -> list[str]:
    """Committed artifacts must equal what the committed source would export.

    Append-only paths only need to exist: a dated snapshot is frozen at first
    publication even if a same-day re-run produces different signals, and
    content-addressed details are verified by their hash.
    """
    problems: list[str] = []
    for path, expected in outputs.items():
        rel = path.relative_to(ROOT).as_posix()
        if not path.exists():
            problems.append(f"missing {rel}")
            continue
        if path in append_only:
            continue
        with open(path, encoding="utf-8") as f:
            actual = json.load(f)
        if _comparable(actual) != _comparable(expected):
            problems.append(f"stale {rel}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Path to strategy-backtest.json (default: src/data/strategy-backtest.json)",
    )
    parser.add_argument("--details-only", action="store_true", help="write only immutable strategy detail assets and manifest")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 if committed artifacts differ from a fresh export (CI gate)")
    args = parser.parse_args(argv)

    source: Path = args.source
    if not source.exists():
        print(f"ERROR: source file not found: {source}", file=sys.stderr)
        return 1

    print(f"Loading {source.relative_to(ROOT).as_posix()} ...", flush=True)
    data = _load_source(source)
    print(f"  as_of: {data.get('signals', {}).get('as_of')}", flush=True)
    outputs, append_only = plan_outputs(data, details_only=args.details_only)

    if args.check:
        problems = check_outputs(outputs, append_only)
        for problem in problems:
            print(f"  DRIFT {problem}", file=sys.stderr)
        if problems:
            print(f"\n{len(problems)} artifact(s) out of sync — run scripts/export_signals_api.py and commit.",
                  file=sys.stderr)
            return 1
        print(f"  OK  {len(outputs)} artifacts match the source", flush=True)
        return 0

    for path, payload in outputs.items():
        if path in append_only and path.exists():
            print(f"  skipped (exists) {path.relative_to(ROOT).as_posix()}", flush=True)
            continue
        _write_json(path, payload)
    print("\nDone. API files written to public/api/v1/ (strategy details: public/api/v2/strategies/)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
