# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))  # lake.py: medallion paths
from lake import GOLD_CACHE, GOLD_REPORT_PERFORMANCE, SILVER_PRICES  # noqa: E402

PRICE_DIR = SILVER_PRICES
REPORT_PERFORMANCE_CSV = GOLD_REPORT_PERFORMANCE
OUT_PATH = ROOT / "src" / "data" / "strategy-backtest.json"
CSV_PATH = ROOT / "public" / "strategy-trades.csv"
PUBLIC_DIR = ROOT / "public"

# Universe filter — reports from 2019-07 onwards feed the signal queue
UNIVERSE_START = dt.date(2019, 7, 1)
# Simulation starts 2020-01-01: report pool too thin before this date
SIM_START = dt.date(2020, 1, 1)

# Common params
ATR_PERIOD = 42
MAX_POSITIONS = 20
POSITION_WEIGHT = 0.05
COST_PER_SIDE = 0.003
KOSPI_PARK_COST = 0.0005  # shared synthetic parking conversion cost
REGIME_MA = 200

# Literature-grounded, fixed parameters (no grid search)
CHANDELIER_ATR_MULT = 5.0   # Chandelier Exit: ATR(42)×5 — wide, lets multibaggers breathe
MA200_MONTHLY_CHECK = True  # Faber (2007): check 200-day MA monthly

# v18: 현금 이자 / 차입 비용 — 모두 일복리, 252 거래일 기준
CASH_YIELD_ANNUAL = 0.03    # 유휴 현금 수익률 (한국 MMF/단기채 ETF 2020-26 평균 프록시, 가정)
CASH_YIELD_DAILY = (1 + CASH_YIELD_ANNUAL) ** (1 / 252) - 1
BORROW_RATE_ANNUAL = 0.06   # J 레버리지 차입 비용 (연)
BORROW_RATE_DAILY = (1 + BORROW_RATE_ANNUAL) ** (1 / 252) - 1

# DCA params
DCA_INITIAL = 10_000_000
DCA_BASE_MONTHLY = 1_000_000
DCA_STEP = 1_000_000
DCA_STEP_MONTHS = 24

# In-sample / out-of-sample split
IS_END = dt.date(2023, 12, 31)
OOS_START = dt.date(2024, 1, 1)

# v3 headline params (kept for breakout sensitivity)
HEADLINE_ATR_MULT = 4.0
HEADLINE_REGIME = False
MIN_DAYS_BEFORE_SIGNAL = 10
SIGNAL_WINDOW_DAYS = 180
RATCHET_THRESHOLD_1 = 0.30
RATCHET_THRESHOLD_2 = 1.00
