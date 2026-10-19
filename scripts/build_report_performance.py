from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")  # pykrx가 import 시점에 KRX_ID/KRX_PW를 읽으므로 먼저 로드

from pykrx import stock  # noqa: E402

import lake  # noqa: E402
from lake_prices import flush_bronze, record_raw_prices, write_silver_prices  # noqa: E402
from report_extract import extract  # noqa: E402
from market_clock import closed_bar_end  # noqa: E402
from report_text import strip_missing_images  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCHOOLS = ("smic", "yig", "star", "kuvic", "ewha", "voera")
PRICE_CACHE_DIR = lake.SILVER_PRICES

KOREAN_DATE_RE = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
FILENAME_DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
DOTTED_DATE_RE = re.compile(r"(?<![\d,.])(20\d{2})\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{1,2})(?![\d.])")
SHORT_DOTTED_DATE_RE = re.compile(r"(?<![\d,.])(2\d)\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})(?![\d.])")
MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
ENGLISH_DATE_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2}),\s*(20\d{2})\b",
    re.I,
)
TITLE_WITH_CODE_RE = re.compile(r"^\s*#*\s*\|?\s*(?P<name>[^|#\n]*?)\s*\((?P<inside>[^)]{2,80})\)", re.I)
CODE_ONLY_HEADING_RE = re.compile(r"^\s*#*\s*\(?\s*(?P<code>\d{6})\s*\)?\s*$")
US_EXCHANGE_RE = re.compile(r"\b(NASDAQ|NYSE|AMEX|NSDQ)\b", re.I)
US_TICKER_AFTER_EXCHANGE_RE = re.compile(r"\b(?:NASDAQ|NYSE|AMEX|NSDQ)\s*:?\s*([A-Z][A-Z0-9.\-]{0,7})\b", re.I)
US_TICKER_BEFORE_EXCHANGE_RE = re.compile(r"\b([A-Z][A-Z0-9.\-]{0,7})\s*:?\s*(?:NASDAQ|NYSE|AMEX|NSDQ)\b", re.I)
KR_CODE_RE = re.compile(r"\b\d{6}\b")
# 상장폐지로 시세 복구가 불가능해 데이터셋에서 제외하는 US 티커 (2026-06 기준).
# 재상장/시세 복구 시 여기서 제거할 것. 건별 교정은 corrections.json이 정위치.
EXCLUDED_DELISTED_TICKERS = {"VTNR", "NETI"}

# Non-KR/US exchange suffixes in ticker strings — e.g. 4751.T (Tokyo), 00700.HK, 600519.SS
FOREIGN_EXCHANGE_SUFFIX_RE = re.compile(r"\.(T|HK|SS|SZ|TYO|L|PA|F|AX|TO|V)$", re.I)

TARGET_LABEL_RE = re.compile(r"(?:\d{2,4}E?\s*)?(목표\s*주가|목표주가|적정\s*주가|적정주가|Target\s+Price|목표주7[Hh]|목표주7h)", re.I)
CURRENT_LABEL_RE = re.compile(r"(현재\s*주가|현재주가|현재가(?!치)|Current\s+Price)", re.I)
UPSIDE_RE = re.compile(r"(상승\s*여력|하락\s*여력|Upside)\s*[:：]?\s*([+\-]?\d+(?:\.\d+)?)\s*%", re.I)
# SMIC prose layout: "Base Case: 53,000원" / "Bull Case: 54,500원" / "현재주가: X 목표주가: Y 상승여력: Z%"
BASE_BULL_CASE_RE = re.compile(
    r"(?:Base|Bull)\s+Case\s*[:：]\s*(?P<prefix>[$₩])?\s*(?P<val>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>원|엔|달러|USD|KRW|[$₩])?",
    re.I,
)
# KUVIC OCR header: "목표주가 현재주가 상승여력 ... {N,NNN} {N,NNN} {P}%"
# Matches the floating value block that appears after the header labels on the same merged OCR line.
# The two values immediately precede an optional percent (upside) — we capture them.
KUVIC_PRICE_BLOCK_RE = re.compile(
    r"목표주가\s+현재주가.*?(?P<tp>(?:[$₩])?\s*\d{1,3}(?:[,]\d{3})+(?:\.\d+)?(?:\s*원)?)"
    r"\s+(?P<cp>(?:[$₩])?\s*\d{1,3}(?:[,]\d{3})+(?:\.\d+)?(?:\s*원)?)"
    r"(?:\s+\d+(?:\.\d+)?\s*%)?",
    re.I | re.S,
)
RATING_RE = re.compile(r"\b(Strong\s+Buy|Buy|Sell|Hold|Neutral|Outperform|Underperform|Overweight|Underweight)\b|(강력\s*매수|적극\s*매수|매수|매도|중립|보유)", re.I)
UNRATED_OPINION_RE = re.compile(r"^\s*#*\s*(?:N\s*/?\s*R|Not\s+Rated|투자의견\s*없음)(?:\s*[\[(].*)?\s*$", re.I)
KOREAN_RATINGS = {"강력매수": "Buy", "적극매수": "Buy", "매수": "Buy", "매도": "Sell", "중립": "Neutral", "보유": "Hold"}
PRICE_TOKEN_RE = re.compile(
    r"(?P<prefix>[$₩])?\s*(?P<a>\d[\d,]*(?:\.\d+)?)\s*(?:[~\-–]\s*(?P<prefix2>[$₩])?\s*(?P<b>\d[\d,]*(?:\.\d+)?))?\s*(?P<suffix>원|엔|달러|USD|KRW|[$₩])?",
    re.I,
)
# 라벨 두 개가 먼저 나오고 값 두 개가 뒤따르는 분리형 레이아웃
#   "현재주가: 목표주가:  14,260원 22,240원 56.0%"        (샘씨엔에스)
#   "목표주가(원) 현재주가(원) |46,000 30,200"             (KUVIC 표)
_LABEL_TAIL = r"\s*(?:\([^)]{0,12}\))?\s*[:：]?\s*"
_PAIR_VALUE = r"[$₩]?\s*\d[\d,]*(?:\.\d+)?\s*(?:원|엔|달러|USD|KRW|[$₩])?"
CURRENT_THEN_TARGET_PAIR_RE = re.compile(
    rf"현재\s*주가{_LABEL_TAIL}목표\s*주가{_LABEL_TAIL}\|?\s*(?P<v1>{_PAIR_VALUE})\s+(?P<v2>{_PAIR_VALUE})",
    re.I,
)
TARGET_THEN_CURRENT_PAIR_RE = re.compile(
    rf"목표\s*주가{_LABEL_TAIL}현재\s*주가{_LABEL_TAIL}\|?\s*(?P<v1>{_PAIR_VALUE})\s+(?P<v2>{_PAIR_VALUE})",
    re.I,
)
# VOERA 라벨-블록→값-블록 레이아웃:
#   "현재주가 목표주가 상승여력 19,400 원 30,000 원 54.6 %"
#   표 셀에서 라벨(현재주가 / 목표주가 상승여력)이 먼저 나오고 값(cp / tp upside%)이 뒤따름
VOERA_LABEL_BLOCK_RE = re.compile(
    rf"현재\s*주가\s+목표\s*주가\s+상승\s*여력\s+"
    rf"(?P<cp>{_PAIR_VALUE})\s+"
    rf"(?P<tp>{_PAIR_VALUE})\s+"
    rf"(?P<upside>[+\-]?\d+(?:\.\d+)?)\s*%",
    re.I,
)
# 본문 고신뢰 목표주가 힌트: "목표주가 30,000원, 투자의견 BUY 제시" (모든 학교 공통)
IN_TEXT_TARGET_PRICE_RE = re.compile(
    r"목표\s*주가\s*(?:는|은|을|로|이)?\s*([\d,]+)\s*원.{0,20}?(투자의견|BUY|매수)\s*(제시)?",
    re.I,
)
# 국내 코드 + 거래소 표기: (089970,KQ) (007810, KOSPI) (047050.KS) (187790) (KQ.237690)
KR_CODE_EXCHANGE_RE = re.compile(r"^(\d{6})\s*[,.]?\s*(KS|KQ|KOSPI|KOSDAQ|코스피|코스닥|KRX)?$", re.I)
KR_EXCHANGE_CODE_RE = re.compile(r"^(KS|KQ|KOSPI|KOSDAQ|코스피|코스닥|KRX)\s*[.,]?\s*(\d{6})$", re.I)
# 괄호 안 단독 미국 티커: (GLW) (TSLA)
BARE_US_TICKER_RE = re.compile(r"^([A-Z]{1,5})$")
YIG_PUBLISHED_RE = re.compile(r"발간일\s*(\d{6})")
# transcribe_pdfs.py 헤더: kordoc이 OCR을 적용한 페이지가 있으면 ocr_pages가 비어 있지 않다
KORDOC_OCR_HEADER_RE = re.compile(r"^<!-- kordoc \S+ ocr_pages=\d")
# Voera 파일명 브라켓 날짜: "[2025.10]" → 2025-10 / "[2026]" → 2026-01
VOERA_BRACKET_DATE_RE = re.compile(r"^\[(\d{4})[. ]?(\d{2})?\]")
# SMIC bulk-import 날짜 복구: pdf_url 내 YYMMDD 접두사 (e.g. "070525_")
SMIC_PDF_DATE_PREFIX_RE = re.compile(r"/(\d{6})_")

# ── 회사명 정제 패턴 ──────────────────────────────────────────────────────────
# 목록 마커 / 숫자 TOC 접두사: "- 2.1.3 OCI홀딩스"  "1. 삼성전자"
COMPANY_LIST_MARKER_RE = re.compile(r"^[-*•]\s*\d[\d.]*\s+|^\d[\d.]*\s+")
# 등급 접두사: "BUY [매수] 루트로닉"  "STRONG BUY 삼성"
COMPANY_RATING_PREFIX_RE = re.compile(
    r"^(?:strong\s+buy|buy|sell|hold|neutral|overweight|underweight|매수|매도|중립|보유|강력\s*매수|적극\s*매수)"
    r"(?:\s*[\[\(][^\]\)]*[\]\)])?\s+",
    re.I,
)
# 대회/기수 접두사: "[2025-1 SOKHA 우승]" "[Top-pick]"
COMPANY_BRACKET_PREFIX_RE = re.compile(r"^\[[^\]]{0,60}\]\s*")
# 회사명이 TOC artifact인지 판단: "- 2.1.3" 로 시작하거나 전체가 숫자/점/공백
COMPANY_TOC_ARTIFACT_RE = re.compile(r"^[-*•\s]*\d+[\d.\s]*$|[-*•]\s*\d+\.")
# Line-level TOC pattern: a line that starts with a list marker followed by numbering
# e.g. "- 2.1.3 [STRONG BUY] OCI홀딩스(010060, KS) 55p"
LINE_TOC_RE = re.compile(r"^\s*[-*•]\s+\d+[\d.]*\s")
# Figure/table captions: "그림1.지구저궤도(LEO) ..." or "Figure 3. ..."
LINE_FIGURE_CAPTION_RE = re.compile(r"^(?:그림|표|Fig(?:ure)?|Table)\s*\d+[\d.]*", re.I)


def _strip_company_prefixes(name: str) -> str:
    """BUY/[award] 등 회사명에 붙은 접두사를 제거한다."""
    original = name
    # 대괄호 접두사 반복 제거
    for _ in range(3):
        cleaned = COMPANY_BRACKET_PREFIX_RE.sub("", name).strip()
        if cleaned == name:
            break
        name = cleaned
    # 등급 접두사
    name = COMPANY_RATING_PREFIX_RE.sub("", name).strip()
    # 대괄호 접두사 다시 (등급 제거 후 남은 경우)
    for _ in range(3):
        cleaned = COMPANY_BRACKET_PREFIX_RE.sub("", name).strip()
        if cleaned == name:
            break
        name = cleaned
    return name if name else original


def _is_toc_artifact(name: str) -> bool:
    """TOC 목록 마커/번호 패턴이면 True."""
    return bool(COMPANY_TOC_ARTIFACT_RE.search(name))


# ── 등급 분류 ─────────────────────────────────────────────────────────────────
BUY_RATINGS = {"buy", "strong buy", "strongbuy", "매수", "강력매수", "적극매수", "overweight"}
SELL_RATINGS = {"sell", "reduce", "underperform", "underweight", "매도"}


def classify_rating(rating: str | None) -> str:
    """등급 문자열 → "buy" | "soft_buy" | "sell"."""
    if not rating:
        return "soft_buy"
    norm = rating.strip().lower().replace(" ", "")
    if norm in BUY_RATINGS:
        return "buy"
    if norm in SELL_RATINGS:
        return "sell"
    return "soft_buy"


@dataclass
class ParsedReport:
    source_file: str
    school: str
    report_type: str  # company | sector
    ocr_recovered: bool
    report_date: str | None  # public date = max(cover_date, upload_date)
    authored_date: str | None  # cover/작성일 from document — may differ from report_date
    filename_date: str | None
    market: str | None
    company: str | None
    ticker: str | None
    exchange: str | None
    rating: str | None
    target_price: float | None
    target_price_raw: str | None
    report_current_price: float | None
    report_current_price_raw: str | None
    stated_upside_pct: float | None
    parse_issue: str | None = None
    qa_flags: str | None = None
    data_issue: str | None = None


ERA_CUTOFF = dt.date(2019, 7, 1)  # YIG/STAR/KUVIC archives start here; earlier SMIC reports are archive-only


def compute_era(report_date: str | None, filename_date: str | None) -> str:
    """Determine era for cross-club fairness.

    "modern"  — effective date ≥ 2019-07-01 (counted in stats)
    "archive" — earlier, or no date at all (kept in dataset but excluded from stats)
    """
    effective = date_from_string(report_date) or date_from_string(filename_date)
    if effective is None:
        return "archive"
    return "modern" if effective >= ERA_CUTOFF else "archive"


@dataclass
class PerformanceRow:
    source_file: str
    school: str
    report_type: str
    era: str  # "modern" | "archive"
    report_date: str | None  # public date = max(cover_date, upload_date)
    authored_date: str | None  # cover/작성일 from document
    filename_date: str | None
    market: str | None
    company: str | None
    ticker: str | None
    exchange: str | None
    rating: str | None
    rating_class: str  # "buy" | "soft_buy" | "sell"
    display_name: str | None
    target_price: float | None
    target_price_raw: str | None
    report_current_price: float | None
    report_current_price_raw: str | None
    stated_upside_pct: float | None
    first_trade_date: str | None
    start_close: float | None
    latest_trade_date: str | None
    latest_close: float | None
    return_latest_pct: float | None
    direction_latest: str | None
    return_30d_pct: float | None
    return_90d_pct: float | None
    return_180d_pct: float | None
    return_365d_pct: float | None
    return_ytd_pct: float | None
    benchmark_return_pct: float | None
    alpha_latest_pct: float | None
    max_high_until_latest: float | None
    max_high_return_pct: float | None
    target_hit_until_latest: bool | None
    first_target_hit_date: str | None
    days_to_target: int | None
    peak_return_24m_pct: float | None
    peak_date_24m: str | None
    age_days: int | None
    maturity: str | None
    data_issue: str | None
    parse_issue: str | None
    qa_flags: str | None
    target_seq: int | None = None
    target_seq_total: int | None = None
    source_md_url: str | None = None
    source_pdf_url: str | None = None


def compact_line(line: str) -> str:
    line = line.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ")
    line = re.sub(r"\s+", " ", line)
    # OCR artifact: "161 ,500" → "161,500" (space inserted before thousands comma)
    line = re.sub(r"(\d)\s+,(\d)", r"\1,\2", line)
    return line.strip()


def clean_name(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"^#+", "", value).strip()
    value = value.strip("| ")
    # Strip leading time-of-day artifacts from OCR/PDF headers: "08:56 슈피겐코리아" → "슈피겐코리아"
    value = re.sub(r"^\d{1,2}:\d{2}\s+", "", value)
    value = re.sub(r"\s+", " ", value)
    # Strip trailing dangling open-bracket / punctuation: "티씨케이(" → "티씨케이"
    value = re.sub(r"[\(\[\{\|,;:\s]+$", "", value)
    if not value or set(value) <= {"-", "|"}:
        return None
    return value


def parse_date(path: Path, lines: list[str]) -> tuple[str | None, str | None]:
    """(본문에서 찾은 발간일 | None, 파일명 날짜 | None)을 반환한다."""
    filename_date = None
    fm = FILENAME_DATE_RE.search(path.name)
    if fm:
        filename_date = f"{fm.group(1)}-{fm.group(2)}-{fm.group(3)}"

    head = "\n".join(lines[:120])
    km = KOREAN_DATE_RE.search(head)
    if km:
        return f"{int(km.group(1)):04d}-{int(km.group(2)):02d}-{int(km.group(3)):02d}", filename_date
    em = ENGLISH_DATE_RE.search(head)
    if em:
        month = MONTHS[em.group(1)[:3].lower()]
        return f"{int(em.group(3)):04d}-{month:02d}-{int(em.group(2)):02d}", filename_date

    def valid(y: int, m: int, d: int) -> str | None:
        try:
            return dt.date(y, m, d).isoformat()
        except ValueError:
            return None

    dm = DOTTED_DATE_RE.search(head)
    if dm:
        iso = valid(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))
        if iso:
            return iso, filename_date
    sm = SHORT_DOTTED_DATE_RE.search(head)
    if sm:
        iso = valid(2000 + int(sm.group(1)), int(sm.group(2)), int(sm.group(3)))
        if iso:
            return iso, filename_date
    return None, filename_date


def yig_published_from_stem(stem: str) -> str | None:
    """YIG 파일명/제목의 '발간일 230916' 표기 → 2023-09-16."""
    m = YIG_PUBLISHED_RE.search(stem)
    if not m:
        return None
    raw = m.group(1)
    yy, mm, dd = int(raw[:2]), int(raw[2:4]), int(raw[4:6])
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return None
    return f"20{yy:02d}-{mm:02d}-{dd:02d}"


def _voera_bracket_date(stem: str) -> str | None:
    """Voera 파일명 브라켓 '[2025.10]' → 2025-10-01 (추정일, qa_flag 필요).

    undated_ 접두사를 제거한 뒤 적용한다.
    """
    clean = stem.removeprefix("undated_").strip()
    m = VOERA_BRACKET_DATE_RE.match(clean)
    if not m:
        return None
    yr = int(m.group(1))
    mo = int(m.group(2)) if m.group(2) else 1
    if not (2010 <= yr <= 2050 and 1 <= mo <= 12):
        return None
    try:
        return dt.date(yr, mo, 1).isoformat()
    except ValueError:
        return None


def _smic_pdf_prefix_date(hint: dict | None) -> str | None:
    """SMIC bulk-import 레코드: pdf_url 내 YYMMDD 접두사로 실제 발간일 복구.

    예: ".../070525_..." → 2007-05-25
    반환값이 None이면 복구 불가.
    """
    if not hint:
        return None
    pdf_url = hint.get("pdf_url") or ""
    m = SMIC_PDF_DATE_PREFIX_RE.search(pdf_url)
    if not m:
        return None
    raw = m.group(1)  # YYMMDD
    yy, mm, dd = int(raw[:2]), int(raw[2:4]), int(raw[4:6])
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return None
    # 두 자리 연도: 00-29 → 2000s, 30-99 → 1900s (but we expect 07-11 = 2007-2011)
    year = 2000 + yy if yy < 30 else 1900 + yy
    try:
        return dt.date(year, mm, dd).isoformat()
    except ValueError:
        return None


def resolve_report_date(path: Path, lines: list[str], school: str, hint: dict | None) -> tuple[str | None, str | None, str | None]:
    """공개일(public_date), 파일명 날짜, 작성일(authored_date)을 반환한다.

    반환: (report_date, filename_date, authored_date)
      report_date   = public_date = max(cover_date, upload_date)  ← PIT 기준
      authored_date = 문서 표지의 작성일(cover date) — 투명성 목적으로 별도 보존
      filename_date = 파일명에서 추출한 날짜

    PIT 원칙: 정보는 업로드일에 공개된다. 표지 날짜(cover_date)가 업로드일보다
    늦을 수 있으므로 max(cover_date, upload_date)를 공개일로 사용한다.
    업로드일을 알 수 없을 때는 표지 날짜만 사용하고 qa_flag 'date_cover_only'를 붙인다.

    추가:
    - voera: 수집 힌트 없으면 파일명 브라켓 날짜 시도 (추정일)
    - smic: filename_date가 bulk-import 아티팩트(2013-09-18)면 pdf_url 접두사로 복구
    """
    content_date, filename_date = parse_date(path, lines)

    # authored_date = 문서 표지에서 직접 파싱한 날짜
    authored_date: str | None = content_date

    # YIG 파일명 내 '발간일 YYMMDD' 표기는 작성일 소스로도 사용
    if not authored_date:
        published = yig_published_from_stem(path.stem)
        if published:
            authored_date = published

    # SMIC bulk-import 아티팩트: filename_date == "2013-09-18"이면 pdf_url 접두사로 복구
    if school == "smic" and filename_date == "2013-09-18":
        recovered = _smic_pdf_prefix_date(hint)
        if recovered:
            # pdf_url 접두사가 실제 발간일에 가장 가까운 날짜. authored_date(표지)가 없으면 채운다.
            authored_date = authored_date or recovered
            # public_date = max(cover, pdf_prefix) — cover가 하루 늦을 수 있음
            cover_d2 = date_from_string(authored_date)
            recovered_d = date_from_string(recovered)
            if cover_d2 and recovered_d:
                public_date2 = max(cover_d2, recovered_d).isoformat()
            else:
                public_date2 = authored_date or recovered
            return public_date2, filename_date, authored_date

    # 업로드/공개 날짜 후보: hint의 published_hint 또는 파일명 날짜
    hint_date = (hint or {}).get("published_hint")
    # YIG: hint_date / filename_date 는 일괄 재업로드일이라 PIT 기준으로 무의미.
    #       발간일(authored_date)을 그대로 public_date로 사용한다.
    # voera: 수집 힌트가 없으면 파일명 브라켓 날짜를 업로드일로 쓰지 않음.
    # 나머지 학교: hint_date(수집 메타) 또는 filename_date가 업로드일.
    if school == "yig":
        upload_date: str | None = None  # 일괄 재업로드일은 무시
    elif school == "voera":
        upload_date = hint_date  # voera는 hint_date만 업로드일로 인정
    else:
        upload_date = hint_date or filename_date

    # 업로드일을 알 수 없고 본문 날짜도 없는 특수 케이스 처리
    if upload_date is None and authored_date is None:
        if school == "voera":
            bracket = _voera_bracket_date(path.stem)
            if bracket:
                return bracket, filename_date, None
        return None, filename_date, None

    # public_date = max(authored_date, upload_date) — 둘 다 있을 때
    cover_d = date_from_string(authored_date)
    upload_d = date_from_string(upload_date)

    if cover_d is not None and upload_d is not None:
        public_date = max(cover_d, upload_d).isoformat()
    elif cover_d is not None:
        # 업로드일 미확인 → 표지 날짜를 public_date로 사용, 플래그는 parse_report에서 추가
        public_date = cover_d.isoformat()
    else:
        # 표지 날짜 없음 → upload_date를 public_date로 사용
        public_date = upload_date  # upload_d could be None only if upload_date is invalid iso

    return public_date, filename_date, authored_date


KR_EXCHANGE_NAMES = {"KQ": "KOSDAQ", "KOSDAQ": "KOSDAQ", "코스닥": "KOSDAQ", "KS": "KOSPI", "KOSPI": "KOSPI", "코스피": "KOSPI"}


def parse_market_from_inside(inside: str) -> tuple[str | None, str | None, str | None]:
    inside = inside.strip()
    km = KR_CODE_EXCHANGE_RE.match(inside)
    if km:
        exchange = KR_EXCHANGE_NAMES.get((km.group(2) or "").upper(), "KRX")
        return "KR", km.group(1), exchange
    km = KR_EXCHANGE_CODE_RE.match(inside)
    if km:
        exchange = KR_EXCHANGE_NAMES.get(km.group(1).upper(), "KRX")
        return "KR", km.group(2), exchange

    if US_EXCHANGE_RE.search(inside):
        exchange_match = US_EXCHANGE_RE.search(inside)
        exchange = exchange_match.group(1).upper().replace("NSDQ", "NASDAQ") if exchange_match else "US"
        m = US_TICKER_AFTER_EXCHANGE_RE.search(inside) or US_TICKER_BEFORE_EXCHANGE_RE.search(inside)
        if m:
            return "US", m.group(1).upper().replace(".", "-"), exchange

    # Non-KR/US foreign exchange suffix: 4751.T, 00700.HK, 600519.SS etc.
    # Return sentinel "FOREIGN" so callers can skip further fallback matching.
    fm = FOREIGN_EXCHANGE_SUFFIX_RE.search(inside)
    if fm:
        return "FOREIGN", None, None

    return None, None, None


# 흔한 약어가 미국 티커로 오인되는 것 방지 (bare ticker 2차 패스에서만 사용)
NON_TICKER_ACRONYMS = {
    "AI", "IT", "IR", "PR", "PER", "PBR", "ROE", "ROA", "EPS", "DPS", "BPS", "EV", "EBIT", "CAGR",
    "CEO", "CFO", "CTO", "OLED", "LCD", "CPU", "GPU", "HBM", "DRAM", "NAND", "ETF", "LNG", "LPG",
    "US", "USA", "KR", "QoQ", "YOY", "YoY", "MOM", "DC", "RND", "ESG", "IPO", "MOU", "B2B", "B2C",
    "FDA", "EU", "UN", "GDP", "CPI", "PMI", "M&A", "REIT", "SCR", "STF", "EDS", "LTA", "AM", "TP",
    # 재무 약어 — 리포트 표/섹션 헤더에서 티커로 오인식되는 케이스
    "TTM", "MRQ", "DCF", "LCOE", "MRO", "ROIC", "EBITDA", "OPM", "ATH", "NPM", "GPM",
}


def parse_bare_us_ticker(lines: list[str], full_text: str) -> tuple[str | None, str | None, str | None, str | None]:
    """명시적 식별이 실패했을 때만: '회사명 (GLW)' 형태의 단독 영문 티커를 시도.

    달러/미국 거래소 표기가 본문에 있어야 하고, 흔한 약어는 제외한다.
    """
    if not re.search(r"[$]|USD|NASDAQ|NYSE|AMEX", full_text[:4000], re.I):
        return None, None, None, None
    for idx, line in enumerate(lines[:140]):
        cl = compact_line(line)
        m = TITLE_WITH_CODE_RE.search(cl)
        if not m:
            continue
        inside = m.group("inside").strip()
        um = BARE_US_TICKER_RE.match(inside)
        if not um or um.group(1).upper() in NON_TICKER_ACRONYMS:
            continue
        name = clean_name(m.group("name"))
        return "US", um.group(1).upper(), "US", name
    return None, None, None, None


def infer_company_before(lines: list[str], idx: int, path: Path) -> str | None:
    for j in range(idx - 1, max(-1, idx - 8), -1):
        candidate = clean_name(lines[j])
        if not candidate:
            continue
        if "Equity Investment Research" in candidate:
            continue
        if re.search(r"\d{4}\s*년|\d{4}-\d{2}-\d{2}", candidate):
            continue
        if candidate.lower() in {"rating", "buy", "sell", "hold"}:
            continue
        return candidate
    stem = path.stem
    name = re.sub(r"^\d{4}-\d{2}-\d{2}_equity-research-", "", stem)
    name = re.sub(r"-\d+$", "", name).replace("-", " ").strip()
    return name or None


def parse_identity(path: Path, lines: list[str]) -> tuple[str | None, str | None, str | None, str | None]:
    head_text = "\n".join(lines[:200])[:4000]
    has_usd_marks = bool(re.search(r"[$]|USD|NASDAQ|NYSE|AMEX", head_text, re.I))

    # Prefer explicit header/table identity near the front page.
    for idx, line in enumerate(lines[:140]):
        cl = compact_line(line)
        # Skip TOC list entries: "- 2.1.3 [STRONG BUY] OCI홀딩스(010060, KS) 55p"
        if LINE_TOC_RE.match(cl):
            continue
        # Skip figure/table captions: "그림1.지구저궤도(LEO) 우주발사단가..."
        if LINE_FIGURE_CAPTION_RE.match(cl):
            continue
        m = TITLE_WITH_CODE_RE.search(cl)
        if not m:
            continue
        inside = m.group("inside").strip()
        market, ticker, exchange = parse_market_from_inside(inside)
        if market == "FOREIGN":
            # Foreign listing (e.g. 4751.T Tokyo) — not KR/US coverage; return with null ticker
            name = clean_name(m.group("name"))
            if name:
                name = _strip_company_prefixes(name)
                name = clean_name(name)
            return "FOREIGN", None, None, name
        if not market and has_usd_marks:
            # "|Corning (GLW) ...|" 같은 단독 영문 티커 — 달러 표기가 있는 문서에서만 인정
            um = BARE_US_TICKER_RE.match(inside)
            if um and um.group(1).upper() not in NON_TICKER_ACRONYMS and clean_name(m.group("name")):
                market, ticker, exchange = "US", um.group(1).upper(), "US"
        if not market:
            continue
        name = clean_name(m.group("name"))
        # Strip rating/bracket prefixes from company name extracted from heading
        if name:
            name = _strip_company_prefixes(name)
            name = clean_name(name)
        # Reject TOC artifacts as company names
        if name and _is_toc_artifact(name):
            name = None
        if not name:
            name = infer_company_before(lines, idx, path)
        return market, ticker, exchange, name

    # Common old layout: company appears in a table row, then a code-only heading follows.
    for idx, line in enumerate(lines[:140]):
        cl = compact_line(line)
        code_match = CODE_ONLY_HEADING_RE.search(cl)
        if code_match:
            code = code_match.group("code")
            return "KR", code, "KRX", infer_company_before(lines, idx, path)

    # Fallback for US table rows or non-heading lines.
    head = "\n".join(compact_line(x) for x in lines[:80])
    m = re.search(r"([A-Za-z][A-Za-z0-9 &.,'\-]+?)\s*\((NASDAQ|NYSE|AMEX|NSDQ)\s*:?\s*([A-Z][A-Z0-9.\-]{0,7})\)", head, re.I)
    if m:
        exchange = m.group(2).upper().replace("NSDQ", "NASDAQ")
        return "US", m.group(3).upper().replace(".", "-"), exchange, clean_name(m.group(1))

    # 파일명(수집된 게시물 제목) fallback — YIG처럼 제목에 "회사명 (089970,KQ)"가 있는 경우
    stem = compact_line(path.stem)
    sm = TITLE_WITH_CODE_RE.search(stem)
    if sm:
        market, ticker, exchange = parse_market_from_inside(sm.group("inside").strip())
        if market:
            raw_name = re.sub(r"^(?:undated|\d{4}-\d{2}-\d{2})_", "", sm.group("name") or "")
            name = clean_name(raw_name)
            if name:
                name = _strip_company_prefixes(name)
                name = clean_name(name)
            return market, ticker, exchange, name

    return None, None, None, None


def parse_table_identity(path: Path, lines: list[str]) -> tuple[str | None, str | None, str | None, str | None]:
    """Read GFM cells as header candidates without treating bare price cells as tickers."""
    if re.search(r"\b(?:Industry\s+In[ -]?depth|Sector\s+(?:Report|Analysis))\b", "\n".join(lines[:40]), re.I):
        return None, None, None, None
    expanded = []
    for line in lines[:200]:
        cells = [re.sub(r"<[^>]+>", "", part).strip() for cell in re.split(r"(?<!\\)\|", line)
                 for part in re.split(r"<br\s*/?>", cell, flags=re.I)] if "|" in line else []
        candidates = [(cell, TITLE_WITH_CODE_RE.search(compact_line(cell))) for cell in cells]
        candidates = [(cell, match) for cell, match in candidates if match]
        # A named company wins over an unlabelled stray code in the same table row.
        candidates.sort(key=lambda item: bool(clean_name(item[1].group("name"))), reverse=True)
        expanded.extend(cell for cell, _ in candidates)
        for cell in cells:
            # Older covers have a clipped opening parenthesis before a code and industry.
            clipped = re.match(r"^(\d{6})\)\s+[가-힣 ]+업\b", cell)
            if clipped:
                expanded.append(f"({clipped[1]})")
        expanded.append(line)
    identity = parse_identity(path, expanded)
    if identity[0]:
        if identity[3] in {"Initial Coverage", "Company Analysis Report", "Research Report"}:
            company = re.sub(r"^\d{4}-\d{2}-\d{2}_(?:Equity Research,\s*|equity-research-)", "", path.stem, flags=re.I)
            if company != path.stem:
                return *identity[:3], clean_name(company)
        return identity
    labelled = re.search(r"\b(?:Ticker|Stock\s+Code|종목코드|VOERA)\s*[:：]?\s*(\d{6})\b",
                         "\n".join(expanded[:80]), re.I)
    if labelled:
        return "KR", labelled[1], "KRX", None
    # Collection titles can contain a competition/year bracket before the company code.
    for match in re.finditer(r"(?P<name>[^()]+)\((?P<inside>[^)]{2,80})\)", path.stem):
        market, ticker, exchange = parse_market_from_inside(match["inside"])
        if market:
            name = re.sub(r"^(?:undated|\d{4}-\d{2}-\d{2})_", "", match["name"])
            return market, ticker, exchange, clean_name(_strip_company_prefixes(name))
    return identity


def normalize_rating(m: re.Match[str]) -> str:
    if m.group(1):
        return m.group(1).title()
    korean = re.sub(r"\s+", "", m.group(2))
    return KOREAN_RATINGS.get(korean, korean)


def parse_rating(lines: list[str]) -> str | None:
    for idx, line in enumerate(lines[:160]):
        if "Rating" in line or "투자의견" in line.replace(" ", ""):
            window = " ".join(compact_line(x) for x in lines[idx : idx + 8])
            m = RATING_RE.search(window)
            if m:
                return normalize_rating(m)
    head = " ".join(compact_line(x) for x in lines[:120])
    m = RATING_RE.search(head)
    return normalize_rating(m) if m else None


def number_to_float(value: str, market: str | None = None) -> float:
    normalized = value.replace(",", "")
    if market == "KR" and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
        normalized = value.replace(".", "")
    return float(normalized)


def token_has_expected_currency(window: str, token: re.Match[str], market: str | None) -> bool:
    raw = token.group(0)
    suffix = (token.group("suffix") or "").upper()
    prefix = token.group("prefix") or token.group("prefix2") or ""
    post = window[token.end() : token.end() + 10].upper()
    if market == "KR":
        return suffix in {"원", "₩", "KRW"} or prefix == "₩"
    if market == "US":
        return prefix == "$" or suffix in {"$", "USD", "달러"} or "USD" in post
    return True


def value_from_price_token(token: re.Match[str], market: str | None = None) -> tuple[float, str]:
    raw = token.group(0).strip()
    a = number_to_float(token.group("a"), market)
    b = number_to_float(token.group("b"), market) if token.group("b") else None
    return ((a + b) / 2 if b is not None else a), raw


BAD_UNIT_AFTER_RE = re.compile(r"^\s*(%|배|x\b|X\b|억|조|만|천\b|bn|mn|조원|억원)")


def token_followed_by_pct(window: str, token: re.Match[str]) -> bool:
    return bool(BAD_UNIT_AFTER_RE.match(window[token.end() : token.end() + 4]))


def token_is_plausible_bare_price(token: re.Match[str], market: str | None) -> bool:
    """통화 표기가 아예 없는 KR 보고서(예: '목표주가 46,000 현재주가 30,200')용 완화 판정.

    미국 리포트는 거의 항상 $ 표기가 있으므로 무통화 완화를 적용하지 않는다
    (1.0x, 4.0배 같은 멀티플 오인 방지).
    """
    if market != "KR":
        return False
    raw_number = token.group("a")
    try:
        value = number_to_float(raw_number, market)
    except ValueError:
        return False
    if "," not in raw_number and value == int(value) and 1900 <= value <= 2100:
        return False  # 연도(2026E 등) 오인 방지
    return "," in raw_number or value >= 500


def choose_price_after_label(text: str, label_re: re.Pattern[str], market: str | None, lenient: bool = False) -> tuple[float | None, str | None]:
    """라벨 등장 순서대로 창을 보되, 같은 창 안에서 통화 표기 토큰 → 무통화(완화) 토큰 순으로 고른다.

    뒤쪽 라벨 창의 통화 토큰이 앞쪽 라벨 창의 무통화 정답을 가로채는 것 방지 (STAR 사례).
    """
    matches = list(label_re.finditer(text))
    for lm in matches:
        window = text[lm.end() : lm.end() + 120]
        tokens = [tm for tm in PRICE_TOKEN_RE.finditer(window) if not token_followed_by_pct(window, tm)][:5]
        for tm in tokens:
            # 라벨에서 가까운 순서대로: 통화 표기 토큰 또는 (완화 시) 그럴듯한 무통화 숫자
            if token_has_expected_currency(window, tm, market) or (lenient and token_is_plausible_bare_price(tm, market)):
                return value_from_price_token(tm, market)
    return None, None


def choose_price_before_label(text: str, label_re: re.Pattern[str], market: str | None) -> tuple[float | None, str | None]:
    matches = list(label_re.finditer(text))
    for lm in matches:
        window = text[max(0, lm.start() - 100) : lm.start()]
        tokens = [tm for tm in PRICE_TOKEN_RE.finditer(window) if token_has_expected_currency(window, tm, market)]
        if not tokens:
            continue
        value, raw = value_from_price_token(tokens[-1], market)
        return value, raw
    return None, None


def parse_price_value(raw: str, market: str | None) -> float | None:
    tm = PRICE_TOKEN_RE.search(raw)
    if not tm:
        return None
    value, _ = value_from_price_token(tm, market)
    return value


def parse_prices(lines: list[str], market: str | None) -> tuple[float | None, str | None, float | None, str | None, float | None]:
    # Rating box is usually the cleanest source; prepend first-page prose for reports that state target before Rating.
    first_page = " ".join(compact_line(x) for x in lines[:180])
    rating_pos = first_page.lower().find("rating")
    if rating_pos >= 0:
        text = first_page[rating_pos : rating_pos + 900] + " " + first_page[: rating_pos]
    else:
        text = first_page

    target = target_raw = current = current_raw = None

    # 0) 라벨 두 개 + 값 두 개 분리형 레이아웃을 최우선 처리 (라벨-창 방식이 값을 엇갈려 잡는 사례 방지)
    pm = CURRENT_THEN_TARGET_PAIR_RE.search(text)
    if pm:
        current, current_raw = parse_price_value(pm.group("v1"), market), pm.group("v1").strip()
        target, target_raw = parse_price_value(pm.group("v2"), market), pm.group("v2").strip()
    else:
        pm = TARGET_THEN_CURRENT_PAIR_RE.search(text)
        if pm:
            target, target_raw = parse_price_value(pm.group("v1"), market), pm.group("v1").strip()
            current, current_raw = parse_price_value(pm.group("v2"), market), pm.group("v2").strip()

    # 0b) KUVIC OCR header layout fallback: "목표주가 현재주가 상승여력 ... {TP} {CP} {upside}%"
    #     Values appear much later on the merged line — the pair regex can't bridge the gap.
    #     Only fires when pair matching above found nothing AND values are magnitude-plausible.
    if target is None:
        kb = KUVIC_PRICE_BLOCK_RE.search(first_page)
        if kb:
            tp_raw = kb.group("tp").strip()
            cp_raw = kb.group("cp").strip()
            tp_val = parse_price_value(tp_raw, market)
            cp_val = parse_price_value(cp_raw, market)
            # Sanity: tp > cp, ratio in [1.01, 10], and both values >= 1000 for KR (≥ 1¢ for US)
            min_plausible = 1000.0 if market == "KR" else 1.0
            if (
                tp_val is not None and cp_val is not None
                and tp_val > cp_val
                and 1.01 <= tp_val / cp_val <= 10.0
                and tp_val >= min_plausible and cp_val >= min_plausible
            ):
                target, target_raw = tp_val, tp_raw
                current, current_raw = cp_val, cp_raw

    # 0c) VOERA 라벨-블록→값-블록 레이아웃:
    #     "현재주가 목표주가 상승여력 19,400 원 30,000 원 54.6 %"
    #     라벨이 먼저 나오고(현재주가 / 목표주가 상승여력) 값이 나중에 나오므로
    #     라벨-창 방식(step 1)은 현재주가 레이블 근처에서 첫 값(cp)을 목표가로 오인식함.
    #     상승여력도 이 블록에서 직접 추출하여 UPSIDE_RE 미매칭 케이스를 보완한다.
    _voera_upside = None
    if target is None:
        vb = VOERA_LABEL_BLOCK_RE.search(first_page)
        if vb:
            cp_raw_v = vb.group("cp").strip()
            tp_raw_v = vb.group("tp").strip()
            cp_val_v = parse_price_value(cp_raw_v, market)
            tp_val_v = parse_price_value(tp_raw_v, market)
            min_plausible = 1000.0 if market == "KR" else 1.0
            if (
                tp_val_v is not None and cp_val_v is not None
                and tp_val_v > cp_val_v
                and 1.01 <= tp_val_v / cp_val_v <= 10.0
                and tp_val_v >= min_plausible and cp_val_v >= min_plausible
            ):
                target, target_raw = tp_val_v, tp_raw_v
                current, current_raw = cp_val_v, cp_raw_v
                _voera_upside = float(vb.group("upside"))

    # 1) 라벨 뒤 토큰 — 창 안에서 통화 표기 우선, 없으면 그럴듯한 무통화 숫자 (KUVIC/STAR 레이아웃)
    if target is None:
        target, target_raw = choose_price_after_label(text, TARGET_LABEL_RE, market, lenient=True)
    if current is None:
        current, current_raw = choose_price_after_label(text, CURRENT_LABEL_RE, market, lenient=True)

    # 2) Valuation prose often puts the price before "목표 주가/Target Price".
    if target is None:
        target, target_raw = choose_price_before_label(text, TARGET_LABEL_RE, market)

    # 3) SMIC-style prose layout: "Base Case: 53,000원" or "Bull Case: 54,500원"
    #    Used as last-resort when no standard label found — take the first (Base) case as the target.
    if target is None:
        bm = BASE_BULL_CASE_RE.search(first_page)
        if bm:
            raw_str = bm.group(0).strip()
            val = parse_price_value(raw_str, market)
            if val is not None:
                target, target_raw = val, raw_str

    # 3b) 본문 고신뢰 힌트: "목표주가 30,000원, 투자의견 BUY 제시" 패턴 (모든 학교 공통)
    #     VOERA 표지 소제목, 본문 맺음말 등에서 목표주가를 명시적으로 재확인하는 문장.
    #     step 0c가 이미 처리했을 수 있으나, 레이아웃 불일치 시 보조 소스로 작동.
    if target is None:
        itm = IN_TEXT_TARGET_PRICE_RE.search(first_page)
        if itm is None:
            full_prose = " ".join(compact_line(x) for x in lines[:360])
            itm = IN_TEXT_TARGET_PRICE_RE.search(full_prose)
        if itm is not None:
            raw_str = itm.group(1).strip() + "원"
            val = parse_price_value(raw_str, market)
            if val is not None:
                target, target_raw = val, raw_str

    # 4) Extended prose search in the full document (beyond first 180 lines) for documents where
    #    the target price is stated clearly in a later section (e.g. "목표 주가 X원으로 매수").
    if target is None:
        full_text_rest = " ".join(compact_line(x) for x in lines[180:360])
        t2, t2r = choose_price_after_label(full_text_rest, TARGET_LABEL_RE, market, lenient=True)
        if t2 is None:
            t2, t2r = choose_price_before_label(full_text_rest, TARGET_LABEL_RE, market)
        if t2 is not None:
            target, target_raw = t2, t2r

    upside = None
    um = UPSIDE_RE.search(text)
    if um:
        upside = float(um.group(2))
        if um.group(1).replace(" ", "").startswith("하락") and upside > 0:
            upside = -upside
    # 0c 에서 추출한 VOERA 블록 상승여력: UPSIDE_RE가 못 잡은 경우에만 사용
    if upside is None and _voera_upside is not None:
        upside = _voera_upside

    # 정합성 가드: 목표가==현재가인데 상승여력이 0이 아니면 현재가 쪽 오파싱 의심 → 현재가 폐기
    # (분리형 레이아웃은 위의 pair 처리가 이미 해결하므로, 남는 충돌은 현재가 라벨 오매칭이 대부분)
    if target is not None and current is not None and upside is not None and target == current and abs(upside) > 1:
        current = current_raw = None

    # OCR 오인식('28기000원'→'000원'=0.0) 등 비현실 가격 차단
    minimum = 100 if market == "KR" else 0.2
    if target is not None and target < minimum:
        target = target_raw = None
    if current is not None and current < minimum:
        current = current_raw = None
    return target, target_raw, current, current_raw, upside


def parse_report(path: Path, school: str = "smic", hint: dict | None = None, *, extractor: str = "candidate") -> ParsedReport:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if extractor == "candidate":
        text = strip_missing_images(text)
    lines = [line.rstrip("\n") for line in text.splitlines()]
    ocr_recovered = bool(KORDOC_OCR_HEADER_RE.match(text)) or "<!-- ocr_fallback -->" in text[:200]
    report_date, filename_date, authored_date = resolve_report_date(path, lines, school, hint)
    market, ticker, exchange, company = (parse_table_identity(path, lines) if extractor == "candidate"
                                        else parse_identity(path, lines))
    if extractor not in {"candidate", "legacy"}:
        raise ValueError(f"unknown extractor: {extractor}")
    # Identity/date resolution and downstream QA stay shared. Keep the old extractor only
    # as an explicit evaluation baseline; a missing candidate rating may use its regex.
    result = extract(text, market) if extractor == "candidate" else None
    explicitly_unrated = extractor == "candidate" and any(UNRATED_OPINION_RE.fullmatch(line) for line in lines[:80])
    rating = None if explicitly_unrated else (result.rating if result else None) or parse_rating(lines)
    # For foreign-exchange listings prices are in non-KRW/USD units — don't parse them
    if market == "FOREIGN":
        target = target_raw = current = current_raw = upside = None
    elif result is not None:
        target, target_raw, current, current_raw, upside = (
            result.target, result.target_raw, result.current, result.current_raw, result.upside)
    else:
        target, target_raw, current, current_raw, upside = parse_prices(lines, market)

    qa_flags: list[str] = []
    if ocr_recovered:
        qa_flags.append("ocr_fallback")
        # OCR 숫자 오인식 가드: 명시된 상승여력과 (목표/현재-1)이 크게 어긋나면 가격을 신뢰하지 않는다
        if target is not None and current is not None and upside is not None:
            implied = (target / current - 1) * 100
            if abs(implied - upside) > 15:
                qa_flags.append("ocr_inconsistent_prices")
                target = target_raw = current = current_raw = None
    # voera 파일명 브라켓 날짜 추정 → qa_flag
    if school == "voera" and report_date and not (hint or {}).get("published_hint"):
        _, content_only_date = parse_date(path, lines)
        if not content_only_date:  # content_date가 없었을 때만 bracket으로 들어온 것
            bracket = _voera_bracket_date(path.stem)
            if bracket and bracket == report_date:
                qa_flags.append("report_date_estimated")
    # smic bulk-date 복구 → qa_flag
    if school == "smic" and filename_date == "2013-09-18" and report_date and report_date != "2013-09-18":
        qa_flags.append("report_date_from_filename")
    # date_cover_only: 업로드일 불명 → 표지 날짜만 사용 (PIT 주의)
    hint_date = (hint or {}).get("published_hint")
    has_upload_date = bool(hint_date or (filename_date and school not in ("voera",) and filename_date != "2013-09-18"))
    if authored_date and not has_upload_date:
        qa_flags.append("date_cover_only")

    if company and len(company) > 40:
        company = None  # OCR 덩어리 등 비정상 회사명 → 파일명/힌트 폴백으로
    if not company:
        # STAR 등: 표지가 전사에서 소실돼도 파일명에 회사명이 남는다
        sm = re.search(r"_(?:STAR|SMIC|YIG|KUVIC|EWHA|VOERA)_(.+)$", path.stem, re.I)
        if sm:
            company = clean_name(sm.group(1))
    if not company and hint:
        company = clean_name(hint.get("company_hint") or None)

    # ── Ewha: ticker hint from title "(010950)" ───────────────────────────────
    if school == "ewha" and hint:
        if not ticker:
            ticker_hint = hint.get("ticker_hint")
            if ticker_hint and re.fullmatch(r"\d{6}", ticker_hint):
                ticker = ticker_hint
                market = market or "KR"
                exchange = exchange or "KRX"

    # ── Voera: rating + TP hints from title bracket ───────────────────────────
    if school == "voera" and hint:
        if not rating and not explicitly_unrated:
            rating_hint = hint.get("rating_hint")
            if rating_hint:
                rating = rating_hint.capitalize()
        if target is None:
            tp_raw = hint.get("tp_hint")
            if tp_raw:
                try:
                    target = float(tp_raw.replace(",", ""))
                    target_raw = f"{tp_raw}원"
                except ValueError:
                    pass

    parsed = ParsedReport(
        source_file=str(path),
        school=school,
        report_type="company",  # 티커 복구 이후 main()에서 확정
        ocr_recovered=ocr_recovered,
        report_date=report_date,
        authored_date=authored_date,
        filename_date=filename_date,
        market=market,
        company=company,
        ticker=ticker,
        exchange=exchange,
        rating=rating,
        target_price=target,
        target_price_raw=target_raw,
        report_current_price=current,
        report_current_price_raw=current_raw,
        stated_upside_pct=upside,
        parse_issue=None,
        qa_flags=";".join(qa_flags) if qa_flags else None,
    )
    compute_issues(parsed)
    return parsed


def add_qa_flag(r: ParsedReport, flag: str) -> None:
    flags = set((r.qa_flags or "").split(";")) - {""}
    flags.add(flag)
    r.qa_flags = ";".join(sorted(flags))


def compute_issues(r: ParsedReport) -> None:
    issues: list[str] = []
    if r.market not in {"KR", "US"}:
        issues.append("non_us_kr_or_unparsed_market")
    if not r.ticker:
        issues.append("missing_ticker")
    if not r.report_date:
        issues.append("missing_report_date")
    # soft_buy 레코드는 목표가 없어도 parse error가 아님 (Hold = no target)
    rclass = classify_rating(r.rating)
    if r.target_price is None and rclass == "buy":
        issues.append("missing_target_price")
    r.parse_issue = ";".join(issues) if issues else None


def resolve_missing_tickers(parsed: list[ParsedReport]) -> None:
    """티커 미식별 + 회사명 보유 레코드를 네이버 증권 자동완성으로 복구한다.

    결과는 data/silver/reference/kr_name_ticker.json에 캐시되어 조회는 이름당 1회만 발생한다.
    이름이 정확히 일치할 때만 채택한다.
    """
    cache_path = lake.SILVER_NAME_TICKER
    cache: dict[str, dict | None] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    session = None
    changed = False
    for r in parsed:
        if r.ticker or not r.company or r.market == "US":
            continue
        name = re.sub(r"\s+", "", r.company)
        if not (2 <= len(name) <= 20):
            continue
        if name not in cache:
            import requests

            session = session or requests.Session()
            session.headers.setdefault("User-Agent", "Mozilla/5.0")
            items: list[dict] = []
            try:
                resp = session.get("https://ac.stock.naver.com/ac", params={"q": name, "target": "stock"}, timeout=15)
                items = resp.json().get("items", [])
            except Exception as exc:  # noqa: BLE001 - 조회 실패는 건너뛴다
                print(f"  ticker lookup failed for {name}: {exc}", file=sys.stderr)
            hit = next(
                (i for i in items if i.get("name", "").replace(" ", "") == name and re.fullmatch(r"\d{6}", i.get("code", ""))),
                None,
            )
            cache[name] = {"code": hit["code"], "market": hit.get("typeCode")} if hit else None
            changed = True
            time.sleep(1.0)
        info = cache.get(name)
        if info:
            r.ticker = info["code"]
            r.market = "KR"
            r.exchange = info.get("market") or "KRX"
            compute_issues(r)
    if changed:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def canonicalize_kr_names(parsed: list[ParsedReport]) -> None:
    """KR 6자리 티커 보유 레코드의 회사명을 pykrx 공식명으로 교정한다.

    기존 Bronze 종목명 스냅샷은 보존하고, 새 조회는 관측 로그에 추가한다 (없는 티커만 조회).
    상장폐지 등으로 pykrx가 빈 문자열을 반환하면 기존 이름을 유지한다.
    US 레코드는 건드리지 않는다.
    """
    cache_path = lake.BRONZE_KRX_NAMES
    cache: dict[str, str] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    observations = lake.BRONZE_KRX_NAME_OBSERVATIONS
    if observations.exists():
        for line in observations.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["schema_version"] != "1.0":
                raise ValueError("unsupported KRX name observation schema")
            cache[row["ticker"]] = row["name"]
    for r in parsed:
        if r.market != "KR" or not r.ticker or not re.fullmatch(r"\d{6}", r.ticker):
            continue
        if r.ticker not in cache:
            try:
                official = stock.get_market_ticker_name(r.ticker)
                # Preserve actual vendor strings, including an empty delisted name.
                # An unexpected object is not an authoritative empty response.
                if not isinstance(official, str):
                    continue
            except Exception:  # noqa: BLE001
                # A network failure is not an authoritative empty vendor response.
                continue
            from importlib.metadata import version
            from lake_prices import append_block

            observation = {"schema_version": "1.0", "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                           "source": "pykrx.get_market_ticker_name", "library_version": version("pykrx"),
                           "ticker": r.ticker, "name": official}
            append_block(observations, (json.dumps(observation, ensure_ascii=False) + "\n").encode("utf-8"))
            cache[r.ticker] = official
        official_name = cache.get(r.ticker, "")
        if official_name:
            r.company = official_name
            compute_issues(r)


def safe_pct(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0) or pd.isna(new) or pd.isna(old):
        return None
    return (float(new) / float(old) - 1.0) * 100.0


def date_from_string(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        # 구형 리포트의 표지 날짜 오인식(월/일 뒤바뀜 등)은 일/월 스왑을 시도하고, 그래도 안 되면 버린다
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
        if m:
            year, mon, day = (int(x) for x in m.groups())
            if mon > 12 >= day >= 1:
                try:
                    return dt.date(year, day, mon)
                except ValueError:
                    return None
        return None


def compute_age_maturity(report_date: str | None, filename_date: str | None, as_of: dt.date) -> tuple[int | None, str | None]:
    """age_days와 maturity 버킷을 계산한다."""
    effective = date_from_string(report_date) or date_from_string(filename_date)
    if effective is None:
        return None, None
    age = (as_of - effective).days
    if age < 90:
        maturity = "fresh"
    elif age < 365:
        maturity = "developing"
    elif age < 3 * 365:
        maturity = "seasoned"
    else:
        maturity = "veteran"
    return age, maturity


def build_display_name(market: str | None, company: str | None, ticker: str | None) -> str | None:
    """KR: 회사명 우선, US: 티커 우선."""
    if market == "US":
        return ticker or company
    return company or ticker


def fetch_kr_prices(ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    end = closed_bar_end("KR", end)
    if start > end:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker)
    record_raw_prices("pykrx_stock", f"KR_{ticker}", df, library="pykrx",
                      request={"ticker": ticker, "start": start.isoformat(), "end": end.isoformat(), "adjusted": True})
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = pd.DataFrame(index=pd.to_datetime(df.index))
    out["open"] = pd.to_numeric(df.iloc[:, 0], errors="coerce")
    out["high"] = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    out["low"] = pd.to_numeric(df.iloc[:, 2], errors="coerce")
    out["close"] = pd.to_numeric(df.iloc[:, 3], errors="coerce")
    out["volume"] = pd.to_numeric(df.iloc[:, 4], errors="coerce") if df.shape[1] > 4 else None
    return out.dropna(subset=["close"])


INDEX_CODES = {"KOSPI": "1001", "KOSDAQ": "2001"}


def fetch_index(name: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    if name == "US":
        return fetch_us_prices("^GSPC", start, end, symbol="IDX_US")
    end = closed_bar_end("KR", end)
    if start > end:
        return pd.DataFrame(columns=["close"])
    df = stock.get_index_ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), INDEX_CODES[name])
    record_raw_prices("pykrx_index", f"IDX_{name}", df, library="pykrx",
                      request={"ticker": INDEX_CODES[name], "start": start.isoformat(), "end": end.isoformat(), "adjusted": False})
    if df.empty:
        return pd.DataFrame(columns=["close"])
    out = pd.DataFrame(index=pd.to_datetime(df.index))
    out["close"] = pd.to_numeric(df.iloc[:, 3], errors="coerce")
    return out.dropna(subset=["close"])


def fetch_index_cached(name: str, start: dt.date, as_of: dt.date) -> pd.DataFrame:
    as_of = closed_bar_end("US" if name == "US" else "KR", as_of)
    cache_path = PRICE_CACHE_DIR / f"IDX_{name}.csv"
    cached: pd.DataFrame | None = None
    if cache_path.exists():
        try:
            cached = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            cached = cached.loc[cached.index <= pd.Timestamp(as_of)]
        except Exception:  # noqa: BLE001
            cached = None
    if cached is not None and not cached.empty and cached.index[0].date() <= start + dt.timedelta(days=12):
        last = cached.index[-1].date()
        if last >= as_of:
            return cached
        tail = fetch_index(name, last + dt.timedelta(days=1), as_of)
        if not tail.empty:
            cached = pd.concat([cached, tail[tail.index > cached.index[-1]]])
            cached = write_silver_prices(f"IDX_{name}", cached, out_dir=PRICE_CACHE_DIR)
        return cached
    df = fetch_index(name, start, as_of)
    if not df.empty:
        df = write_silver_prices(f"IDX_{name}", df, out_dir=PRICE_CACHE_DIR)
    return df


def fetch_prices(market: str, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    if market == "KR":
        return fetch_kr_prices(ticker, start, end)
    return fetch_us_prices(ticker, start, end)


def fetch_prices_cached(market: str, ticker: str, start: dt.date, as_of: dt.date, sleep: float) -> pd.DataFrame:
    """일별 시세를 silver 시세(data/silver/prices/)에 캐시하고 누락 구간만 증분 조회한다."""
    as_of = closed_bar_end(market, as_of)
    cache_path = PRICE_CACHE_DIR / f"{market}_{ticker}.csv"
    cached: pd.DataFrame | None = None
    if cache_path.exists():
        try:
            cached = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            cached = cached.loc[cached.index <= pd.Timestamp(as_of)]
        except Exception:  # noqa: BLE001 - 손상 캐시는 새로 받는다
            cached = None

    if cached is not None and not cached.empty and cached.index[0].date() <= start + dt.timedelta(days=12):
        last = cached.index[-1].date()
        if last >= as_of:
            return cached
        tail = fetch_prices(market, ticker, last + dt.timedelta(days=1), as_of)
        time.sleep(sleep)
        if not tail.empty:
            cached = pd.concat([cached, tail[tail.index > cached.index[-1]]])
            cached = write_silver_prices(f"{market}_{ticker}", cached, out_dir=PRICE_CACHE_DIR)
        return cached

    df = fetch_prices(market, ticker, start, as_of)
    time.sleep(sleep)
    if not df.empty:
        df = write_silver_prices(f"{market}_{ticker}", df, out_dir=PRICE_CACHE_DIR)
    return df


def fetch_us_prices(ticker: str, start: dt.date, end: dt.date, symbol: str | None = None) -> pd.DataFrame:
    end = closed_bar_end("US", end)
    if start > end:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    # yfinance end is exclusive; add one day at call site if an inclusive as-of is desired.
    df = yf.download(ticker, start=start.isoformat(), end=(end + dt.timedelta(days=1)).isoformat(), progress=False, auto_adjust=True, threads=False)
    record_raw_prices("yfinance", symbol or f"US_{ticker}", df, library="yfinance",
                      request={"ticker": ticker, "start": start.isoformat(), "end": end.isoformat(), "adjusted": True})
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    out = pd.DataFrame(index=pd.to_datetime(df.index))
    for src, dst in [("open", "open"), ("high", "high"), ("low", "low"), ("close", "close"), ("volume", "volume")]:
        if src in df.columns:
            out[dst] = pd.to_numeric(df[src], errors="coerce")
    return out.dropna(subset=["close"])


def benchmark_for(parsed: ParsedReport, benchmarks: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    if parsed.market == "US":
        return benchmarks.get("US")
    if (parsed.exchange or "").upper() == "KOSDAQ":
        return benchmarks.get("KOSDAQ")
    return benchmarks.get("KOSPI")


def performance_bucket(ret: float | None) -> str:
    if ret is None:
        return "No quote"
    if ret >= 900:
        return "Tenbagger"
    if ret >= 300:
        return "Multibagger"
    if ret >= 100:
        return "Double"
    if ret >= 30:
        return "Winner"
    if ret >= 0:
        return "Positive"
    if ret >= -30:
        return "Drawdown"
    return "Wrecked"


def evaluate_report(parsed: ParsedReport, prices: pd.DataFrame, as_of: dt.date, benchmarks: dict[str, pd.DataFrame] | None = None) -> PerformanceRow:
    age_days, maturity = compute_age_maturity(parsed.report_date, parsed.filename_date, as_of)
    display_name = build_display_name(parsed.market, parsed.company, parsed.ticker)
    rating_class = classify_rating(parsed.rating)

    data_issue = None
    # Preserve correction-supplied data_issue (e.g. "delisted") — skip price fetch entirely.
    if parsed.data_issue:
        return empty_performance(parsed, parsed.data_issue, age_days, maturity, display_name, rating_class)
    report_dt = date_from_string(parsed.report_date)
    if report_dt is None:
        data_issue = "missing_report_date"
        return empty_performance(parsed, data_issue, age_days, maturity, display_name, rating_class)
    if prices.empty:
        return empty_performance(parsed, "missing_market_prices", age_days, maturity, display_name, rating_class)

    prices = prices.sort_index()
    start_ts = pd.Timestamp(report_dt)
    available = prices[prices.index >= start_ts]
    if available.empty:
        return empty_performance(parsed, "no_price_on_or_after_report_date", age_days, maturity, display_name, rating_class)

    start_row = available.iloc[0]
    start_date = available.index[0].date()
    start_close = float(start_row["close"])
    latest_row = prices[prices.index <= pd.Timestamp(as_of)].iloc[-1]
    latest_date = prices[prices.index <= pd.Timestamp(as_of)].index[-1].date()
    latest_close = float(latest_row["close"])

    returns: dict[int, float | None] = {}
    for days in (30, 90, 180, 365):
        horizon = prices[(prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(report_dt + dt.timedelta(days=days)))]
        returns[days] = safe_pct(float(horizon.iloc[-1]["close"]), start_close) if not horizon.empty else None

    # YTD: 전년도 마지막 종가 → 최신 종가 (종목 자체의 연초 이후 수익률)
    prior_year = prices[prices.index < pd.Timestamp(dt.date(as_of.year, 1, 1))]
    return_ytd = safe_pct(latest_close, float(prior_year.iloc[-1]["close"])) if not prior_year.empty else None

    until_latest = prices[(prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(as_of))]
    max_high = float(until_latest["high"].max()) if not until_latest.empty and "high" in until_latest else None
    max_high_return = safe_pct(max_high, start_close)

    hit = None
    first_hit_date = None
    days_to_target = None
    if parsed.target_price is not None and max_high is not None:
        # Target hit measured from D+1 (first trading day AFTER publication date).
        # This eliminates spurious 적중 D+0: a target cannot be "hit" on the same day
        # the report is published, since the investor could not act on it yet.
        after_publication = until_latest[until_latest.index > pd.Timestamp(report_dt)]
        hit_frame = after_publication[after_publication["high"] >= parsed.target_price]
        hit = not hit_frame.empty
        if hit:
            first_hit_date = hit_frame.index[0].date().isoformat()
            days_to_target = (hit_frame.index[0].date() - report_dt).days

    # 24m peak: max intraday high within 24 months after first_trade_date (or up to as_of if younger)
    peak_24m_cutoff = min(start_date + dt.timedelta(days=365 * 2), as_of)
    window_24m = prices[(prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(peak_24m_cutoff))]
    peak_return_24m: float | None = None
    peak_date_24m_val: str | None = None
    if not window_24m.empty and "high" in window_24m.columns:
        peak_high_24m = float(window_24m["high"].max())
        peak_return_24m = safe_pct(peak_high_24m, start_close)
        peak_idx = window_24m["high"].idxmax()
        peak_date_24m_val = peak_idx.date().isoformat()

    latest_ret = safe_pct(latest_close, start_close)
    direction = None
    if latest_ret is not None:
        direction = "up" if latest_ret > 0 else "down" if latest_ret < 0 else "flat"

    # 같은 보유기간의 시장지수 수익률 → 초과수익(알파)
    bench_ret = None
    bench = benchmark_for(parsed, benchmarks or {})
    if bench is not None and not bench.empty:
        b_start = bench["close"].asof(pd.Timestamp(start_date))
        b_end = bench["close"].asof(pd.Timestamp(latest_date))
        bench_ret = safe_pct(b_end, b_start)
    alpha = latest_ret - bench_ret if latest_ret is not None and bench_ret is not None else None

    return PerformanceRow(
        source_file=parsed.source_file,
        school=parsed.school,
        report_type=parsed.report_type,
        era=compute_era(parsed.report_date, parsed.filename_date),
        report_date=parsed.report_date,
        authored_date=parsed.authored_date,
        filename_date=parsed.filename_date,
        market=parsed.market,
        company=parsed.company,
        ticker=parsed.ticker,
        exchange=parsed.exchange,
        rating=parsed.rating,
        rating_class=rating_class,
        display_name=display_name,
        target_price=parsed.target_price,
        target_price_raw=parsed.target_price_raw,
        report_current_price=parsed.report_current_price,
        report_current_price_raw=parsed.report_current_price_raw,
        stated_upside_pct=parsed.stated_upside_pct,
        first_trade_date=start_date.isoformat(),
        start_close=start_close,
        latest_trade_date=latest_date.isoformat(),
        latest_close=latest_close,
        return_latest_pct=latest_ret,
        direction_latest=direction,
        return_30d_pct=returns[30],
        return_90d_pct=returns[90],
        return_180d_pct=returns[180],
        return_365d_pct=returns[365],
        return_ytd_pct=return_ytd,
        benchmark_return_pct=bench_ret,
        alpha_latest_pct=alpha,
        max_high_until_latest=max_high,
        max_high_return_pct=max_high_return,
        target_hit_until_latest=hit,
        first_target_hit_date=first_hit_date,
        days_to_target=days_to_target,
        peak_return_24m_pct=peak_return_24m,
        peak_date_24m=peak_date_24m_val,
        age_days=age_days,
        maturity=maturity,
        data_issue=data_issue,
        parse_issue=parsed.parse_issue,
        qa_flags=parsed.qa_flags,
    )


def empty_performance(parsed: ParsedReport, data_issue: str, age_days: int | None = None, maturity: str | None = None, display_name: str | None = None, rating_class: str | None = None) -> PerformanceRow:
    if age_days is None:
        age_days, maturity = compute_age_maturity(parsed.report_date, parsed.filename_date, dt.date.today())
    if display_name is None:
        display_name = build_display_name(parsed.market, parsed.company, parsed.ticker)
    if rating_class is None:
        rating_class = classify_rating(parsed.rating)
    return PerformanceRow(
        source_file=parsed.source_file,
        school=parsed.school,
        report_type=parsed.report_type,
        era=compute_era(parsed.report_date, parsed.filename_date),
        report_date=parsed.report_date,
        authored_date=parsed.authored_date,
        filename_date=parsed.filename_date,
        market=parsed.market,
        company=parsed.company,
        ticker=parsed.ticker,
        exchange=parsed.exchange,
        rating=parsed.rating,
        rating_class=rating_class,
        display_name=display_name,
        target_price=parsed.target_price,
        target_price_raw=parsed.target_price_raw,
        report_current_price=parsed.report_current_price,
        report_current_price_raw=parsed.report_current_price_raw,
        stated_upside_pct=parsed.stated_upside_pct,
        first_trade_date=None,
        start_close=None,
        latest_trade_date=None,
        latest_close=None,
        return_latest_pct=None,
        direction_latest=None,
        return_30d_pct=None,
        return_90d_pct=None,
        return_180d_pct=None,
        return_365d_pct=None,
        return_ytd_pct=None,
        benchmark_return_pct=None,
        alpha_latest_pct=None,
        max_high_until_latest=None,
        max_high_return_pct=None,
        target_hit_until_latest=None,
        first_target_hit_date=None,
        days_to_target=None,
        peak_return_24m_pct=None,
        peak_date_24m=None,
        age_days=age_days,
        maturity=maturity,
        data_issue=data_issue,
        parse_issue=parsed.parse_issue,
        qa_flags=parsed.qa_flags,
    )


def round_floats(row: dict[str, object]) -> dict[str, object]:
    out = {}
    for key, value in row.items():
        if isinstance(value, float):
            if math.isnan(value):
                out[key] = None
            else:
                out[key] = round(value, 6)
        else:
            out[key] = value
    return out


def write_csv(path: Path, rows: Iterable[object]) -> None:
    dict_rows = [round_floats(asdict(row)) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    if not dict_rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(dict_rows[0].keys()))
        writer.writeheader()
        writer.writerows(dict_rows)


def load_hints() -> dict[str, dict]:
    """수집 매니페스트의 메타데이터를 (파일 stem → 항목)으로 인덱싱."""
    manifest_path = lake.BRONZE_REPORT_MANIFEST
    hints: dict[str, dict] = {}
    if manifest_path.exists():
        for entry in json.loads(manifest_path.read_text(encoding="utf-8")):
            if entry.get("file"):
                hints[Path(entry["file"]).stem] = entry
    return hints


def load_corrections() -> dict[str, dict]:
    """사람이 검증한 교정값 (source_name → 필드 오버라이드). 재파싱해도 유지된다."""
    path = lake.SILVER_CORRECTIONS
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def apply_corrections(parsed: list[ParsedReport], corrections: dict[str, dict]) -> None:
    for r in parsed:
        fix = corrections.get(Path(r.source_file).name)
        if not fix:
            continue
        for field, value in fix.items():
            if field.startswith("_") or not hasattr(r, field):
                continue
            setattr(r, field, value)
        r.parse_issue = None if not fix.get("_keep_issue") else r.parse_issue
        # Mark records whose target_price was manually set so unit-correction pass skips them.
        if "target_price" in fix:
            add_qa_flag(r, "target_manually_corrected")


def quality_score(r: ParsedReport) -> int:
    return sum((r.target_price is not None, r.parse_issue is None, r.company is not None, r.report_current_price is not None))


def dedup_reports(parsed: list[ParsedReport]) -> list[ParsedReport]:
    """같은 학교·종목·발간일 중복(재업로드 등)은 품질 점수가 높은 한 건만 남긴다."""
    best: dict[tuple[str, str, str], ParsedReport] = {}
    unkeyed: list[ParsedReport] = []
    order: list[tuple[str, str, str]] = []
    for r in parsed:
        if not r.ticker or not r.report_date:
            unkeyed.append(r)
            continue
        key = (r.school, r.ticker, r.report_date)
        if key not in best:
            best[key] = r
            order.append(key)
        elif quality_score(r) > quality_score(best[key]):
            best[key] = r
    return [best[k] for k in order] + unkeyed


def summarize(rows: list[dict], group: str) -> dict:
    # Only modern-era buy-class records count in headline stats (archive era excluded for fairness)
    modern_buy_rows = [r for r in rows if r.get("rating_class") == "buy" and r.get("era") == "modern"]
    priced = [r for r in modern_buy_rows if r["return_latest_pct"] is not None]
    returns = sorted(r["return_latest_pct"] for r in priced)
    mid = len(returns) // 2
    median = None if not returns else returns[mid] if len(returns) % 2 else (returns[mid - 1] + returns[mid]) / 2
    return {
        "group": group,
        "reports": len(modern_buy_rows),
        "priced_reports": len(priced),
        "with_target": sum(1 for r in modern_buy_rows if r["target_price"] is not None),
        "up_latest": sum(1 for r in priced if r["return_latest_pct"] > 0),
        "down_latest": sum(1 for r in priced if r["return_latest_pct"] < 0),
        "target_hit": sum(1 for r in modern_buy_rows if r["target_hit_until_latest"]),
        "avg_return_latest_pct": round(sum(returns) / len(returns), 6) if returns else None,
        "median_return_latest_pct": round(median, 6) if median is not None else None,
    }


def apply_market_prices_to_parsed(parsed: list[ParsedReport], price_cache: dict[tuple[str, str], pd.DataFrame]) -> None:
    """발간가를 시세 창고 기반으로 재계산한다.

    report_current_price = 발간 유효일 당일 또는 그 이전의 마지막 종가.
    텍스트 파싱값은 report_current_price_raw에 보존.
    stated_upside_pct도 시세 기반 발간가로 재계산한다.
    """
    for r in parsed:
        if not r.ticker or not r.market or r.market not in {"KR", "US"}:
            continue
        effective_date = date_from_string(r.report_date) or date_from_string(r.filename_date)
        if effective_date is None:
            continue
        prices = price_cache.get((r.market, r.ticker))
        if prices is None or prices.empty:
            continue
        prices_sorted = prices.sort_index()
        # Last available close ON OR BEFORE effective date
        on_or_before = prices_sorted[prices_sorted.index <= pd.Timestamp(effective_date)]
        if on_or_before.empty:
            continue
        market_price = float(on_or_before.iloc[-1]["close"])
        r.report_current_price = market_price
        # Recompute stated_upside_pct from market-based 발간가
        if r.target_price is not None and market_price > 0:
            r.stated_upside_pct = round((r.target_price / market_price - 1) * 100, 4)


def apply_target_sanity(parsed: list[ParsedReport]) -> None:
    """Buy 등급 레코드의 목표가 정합성을 검증하고 의심 레코드를 플래그한다.

    1. target_price <= report_current_price (시세 기반): 상승 여력 없음 → 스왑 시도
       - report_current_price_raw 값이 target_price보다 크고
         그 raw 값이 market 발간가의 ±15% 이내이면 → 파싱 스왑 오류: 값 교환
       - 그렇지 않으면 → target_price를 null, qa_flag 'target_price_suspect' 추가
    2. 스왑/null 처리 후 compute_issues 재계산
    3. Split-adjustment pass: price warehouse is split-adjusted but report targets are nominal.
       When the document's own nominal upside (target/doc_current) is plausible for a Buy
       [0.7–4.0] but the market price differs from the doc price by more than ±33%
       (adjustment factor f = market/doc_current outside [0.67, 1.5]), the target is in
       nominal (pre-split) space and must be rescaled: target_price ← target_price × f.
       The nominal value is preserved in target_price_raw.
    4. If doc current is missing/implausible (nominal upside outside [0.7, 4.0]) AND the
       market-based upside > 300% → flag target_price_suspect, null hit fields.
    """
    for r in parsed:
        if classify_rating(r.rating) != "buy":
            continue
        if r.target_price is None or r.report_current_price is None:
            continue
        market_price = r.report_current_price  # 시세 기반 발간가
        if r.target_price > market_price:
            continue  # 정상: 목표가 > 발간가
        # Suspicious: target <= market price on a Buy
        raw_current_val: float | None = None
        if r.report_current_price_raw:
            try:
                tm = PRICE_TOKEN_RE.search(r.report_current_price_raw)
                if tm:
                    raw_current_val, _ = value_from_price_token(tm, r.market)
            except Exception:  # noqa: BLE001
                pass
        swapped = False
        if raw_current_val is not None and raw_current_val > r.target_price:
            # Swap heuristic: swapped "current" must be within ±15% of market price
            if market_price > 0 and abs(raw_current_val / market_price - 1) <= 0.15:
                # Swap: the text "current" was actually the target, and vice versa.
                # Capture originals before mutation.
                old_target_raw = r.target_price_raw
                old_current_raw = r.report_current_price_raw
                r.target_price = raw_current_val
                r.target_price_raw = old_current_raw  # old parsed-current string becomes target raw
                r.report_current_price_raw = old_target_raw or ""  # old target string becomes current raw
                # report_current_price stays as market-derived value (already set)
                r.stated_upside_pct = round((r.target_price / market_price - 1) * 100, 4) if market_price > 0 else None
                add_qa_flag(r, "target_swapped_from_raw")
                swapped = True
        if not swapped:
            r.target_price = None
            r.target_price_raw = None
            r.stated_upside_pct = None
            add_qa_flag(r, "target_price_suspect")
        compute_issues(r)

    # ── Split-adjustment pass ────────────────────────────────────────────────
    # Price warehouse (pykrx / yfinance) delivers split-adjusted closes, but report
    # target prices and current prices are printed as nominal (pre-split) values.
    # For stocks that split after publication the market 발간가 will be much lower than
    # the doc-stated current price, making the market-based upside look enormous.
    #
    # Approach (US prices are already split-adjusted by yfinance with auto_adjust=True,
    # so the same logic applies to both markets):
    #   1. Parse doc_price from report_current_price_raw.
    #   2. f = market_price / doc_price  (adjustment factor; <1 means splits occurred)
    #   3. Nominal upside = target / doc_price.  Plausible for a Buy if in [0.7, 4.0].
    #   4a. Plausible nominal upside AND f outside [0.67, 1.5]:
    #       → rescale target_price *= f (now comparable to adjusted price series)
    #       → stated_upside_pct = target/market_price - 1  (≈ nominal upside, scale-invariant)
    #       → preserve original target string in target_price_raw
    #       → add qa_flag "target_split_adjusted"
    #   4b. Implausible nominal upside AND market-based upside > 300%:
    #       → add qa_flag "target_price_suspect", null target so hit fields will be null
    #   In both cases: skip manually-corrected targets.
    for r in parsed:
        if classify_rating(r.rating) != "buy":
            continue
        if r.target_price is None or r.report_current_price is None:
            continue
        if "target_manually_corrected" in (r.qa_flags or ""):
            continue
        # Only act when market-based upside is suspiciously large (>300%) — avoids
        # touching records that are already sensible after the swap pass.
        market_price = r.report_current_price
        if market_price <= 0:
            continue
        market_upside = r.target_price / market_price - 1.0  # fractional
        if market_upside <= 3.0:
            continue  # market-based upside <= 300%: nothing to do

        # Try to parse the doc-stated current price
        doc_price: float | None = None
        raw = r.report_current_price_raw
        # After the swap pass the raw field may have been reassigned; use it regardless.
        if raw:
            try:
                tm = PRICE_TOKEN_RE.search(raw)
                if tm:
                    doc_price, _ = value_from_price_token(tm, r.market)
            except Exception:  # noqa: BLE001
                pass

        if doc_price is not None and doc_price > 0:
            nominal_upside = r.target_price / doc_price - 1.0  # fractional
            nominal_plausible = 0.7 <= (r.target_price / doc_price) <= 4.0  # −30% to +300%
            f = market_price / doc_price  # adjustment factor (<1 → splits reduced the price)
            scales_agree = 0.67 < f < 1.5
            if nominal_plausible and not scales_agree:
                # Nominal target is credible but lives in a different price scale than the
                # warehouse series.  Rescale into adjusted space.
                nominal_raw = r.target_price_raw  # preserve for audit
                r.target_price = round(r.target_price * f, 4)
                r.target_price_raw = nominal_raw  # keep nominal string for reference
                r.stated_upside_pct = round((r.target_price / market_price - 1) * 100, 4)
                add_qa_flag(r, "target_split_adjusted")
                compute_issues(r)
                continue
            # nominal upside plausible but scales already agree → leave as-is
            if nominal_plausible and scales_agree:
                continue

        # Reach here when doc_price is missing or nominal upside is implausible,
        # yet market-based upside > 300% → cannot trust this target.
        if "target_price_suspect" not in (r.qa_flags or ""):
            add_qa_flag(r, "target_price_suspect")
        r.target_price = None
        r.target_price_raw = None
        r.stated_upside_pct = None
        compute_issues(r)

    # ── Unit-magnitude correction pass ──────────────────────────────────────
    # Buy records where target/발간가 > 6 — try ÷10 / ÷100 / ×1000 unit hypotheses.
    # Apply only when exactly one correction lands in the sane ratio window [1.05, 3.0].
    # IMPORTANT: use the document-stated current price (from raw text), not the yfinance
    # split-adjusted market price, to avoid false corrections on old split-heavy stocks (e.g. TSLA).
    for r in parsed:
        if classify_rating(r.rating) != "buy":
            continue
        if r.target_price is None:
            continue
        # Never touch records whose target_price was set by a manual correction.
        if "target_manually_corrected" in (r.qa_flags or ""):
            continue
        # Only apply unit correction when there is evidence of a parsing/OCR issue.
        # Without this guard, extreme-bull-case targets (e.g. 1400% upside) are wrongly halved.
        qa = r.qa_flags or ""
        has_issue_signal = "target_price_suspect" in qa or "ocr_fallback" in qa or "ocr_inconsistent" in qa
        if not has_issue_signal:
            continue
        # Prefer the raw document price over the yfinance-overwritten report_current_price.
        doc_price: float | None = None
        if r.report_current_price_raw:
            doc_price = parse_price_value(r.report_current_price_raw, r.market)
        if doc_price is None or doc_price <= 0:
            continue
        market_price = doc_price
        ratio = r.target_price / market_price
        if ratio <= 6:
            continue  # already sane
        candidates: list[tuple[float, str]] = []
        for factor, label in ((0.1, "div10"), (0.01, "div100"), (1000.0, "mul1000")):
            adjusted = r.target_price * factor
            adj_ratio = adjusted / market_price
            if 1.05 <= adj_ratio <= 3.0:
                candidates.append((adjusted, label))
        if len(candidates) == 1:
            r.target_price = round(candidates[0][0], 2)
            r.stated_upside_pct = round((r.target_price / market_price - 1) * 100, 4)
            add_qa_flag(r, "target_unit_corrected")
            # Remove suspect flag if it was set
            flags = set((r.qa_flags or "").split(";")) - {"", "target_price_suspect"}
            r.qa_flags = ";".join(sorted(flags)) or None
            compute_issues(r)


# ── 전성기(peak) 판결 버킷 ────────────────────────────────────────────────────
# performance_bucket()과 동일한 래더를 재사용하며, peak_return_24m_pct에 적용된다.
# (write_web_json에서 record["bucket_peak"] = performance_bucket(record["peak_return_24m_pct"]) 로 호출)

# ── 목표가 시퀀스 계산 ──────────────────────────────────────────────────────────

def compute_target_sequences(rows: list["PerformanceRow"]) -> None:
    """(school, ticker) 단위로 report_date 순서로 1-based target_seq를 부여한다.

    ticker가 None인 레코드는 seq=None으로 남긴다.
    """
    # group by (school, ticker), sort by report_date
    groups: dict[tuple[str, str], list["PerformanceRow"]] = {}
    for row in rows:
        if not row.ticker:
            continue
        key = (row.school, row.ticker)
        groups.setdefault(key, []).append(row)
    for key, group in groups.items():
        group.sort(key=lambda r: (r.report_date or "9999-99-99"))
        total = len(group)
        for i, row in enumerate(group):
            row.target_seq = i + 1
            row.target_seq_total = total


# ── 발간일 불가능 가드 ───────────────────────────────────────────────────────────
# voera/ewha는 2015년 이전 창립 불가능. 그 이전 날짜는 OCR 오인식(설립일 등)
SCHOOL_MIN_DATE: dict[str, dt.date] = {
    "voera": dt.date(2015, 1, 1),
    "ewha": dt.date(2015, 1, 1),
}
GLOBAL_MIN_DATE = dt.date(2000, 1, 1)


def guard_implausible_dates(parsed: list[ParsedReport], as_of: dt.date) -> None:
    """발간일이 불가능한 범위에 있으면 null 처리 + qa_flag를 추가한다.

    - voera/ewha: 2015-01-01 이전 → impossible
    - 모든 학교: 2000-01-01 이전 → impossible
    - 모든 학교: as_of + 30d 이후 → impossible (미래 날짜)
    """
    future_limit = as_of + dt.timedelta(days=30)
    for r in parsed:
        d = date_from_string(r.report_date)
        if d is None:
            continue
        school_min = SCHOOL_MIN_DATE.get(r.school, GLOBAL_MIN_DATE)
        if d < school_min or d < GLOBAL_MIN_DATE or d > future_limit:
            r.report_date = None
            add_qa_flag(r, "report_date_implausible")


GITHUB_RAW_BASE = "https://github.com/ChoiInYeol/SNUSMIC-Portfolio/blob/main"


def _url_encode_path(path_str: str) -> str:
    """URL-encode each path segment (preserves /, handles Korean chars, spaces, parens, commas)."""
    return "/".join(quote(seg, safe="") for seg in path_str.replace("\\", "/").split("/"))


def _build_sha_url_index(hints: dict[str, dict]) -> dict[str, str]:
    """manifest 항목의 sha256 → page_url 역인덱스를 한 번만 구성한다."""
    index: dict[str, str] = {}
    for entry in hints.values():
        sha = entry.get("sha256")
        url = entry.get("page_url")
        if sha and url:
            index[sha] = url
    return index


def _sha256_of_pdf(md_path: str) -> str | None:
    """마크다운 경로에서 대응 PDF 경로를 유추하고 SHA256을 계산한다.

    {silver markdown}/{school}/stem.md → {bronze pdfs}/{school}/stem.pdf (또는 {bronze pdfs}/stem.pdf).
    구 경로(data/markdown/...)도 "markdown" 세그먼트 기준이라 그대로 동작한다.
    파일이 없거나 0바이트면 None을 반환한다.
    """
    import hashlib

    p = Path(md_path)
    # 마크다운 경로에서 school 추론
    parts = p.parts
    # ..., "markdown", [school,] "stem.md"
    try:
        md_idx = parts.index("markdown")
    except ValueError:
        return None
    pdf_root = lake.BRONZE_PDFS
    stem = p.stem
    # school subdir 경로 먼저
    if md_idx + 2 < len(parts) - 1:
        school = parts[md_idx + 1]
        candidate = pdf_root / school / f"{stem}.pdf"
        if candidate.exists() and candidate.stat().st_size > 0:
            return hashlib.sha256(candidate.read_bytes()).hexdigest()
    # root-level pdfs/
    candidate_root = pdf_root / f"{stem}.pdf"
    if candidate_root.exists() and candidate_root.stat().st_size > 0:
        return hashlib.sha256(candidate_root.read_bytes()).hexdigest()
    return None


def build_source_urls(source_file: str, hints: dict[str, dict], sha_url_index: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    """(source_md_url, source_pdf_url) for a given source_file path string.

    source_md_url  — GitHub blob URL to the markdown file.
    source_pdf_url — post/detail PAGE URL from manifest (page_url field).
                     Direct PDF download URLs are intentionally NOT stored here.
                     None if no page_url is available for this record.

    sha_url_index: 미리 구성한 sha256→page_url 역인덱스 (stem 미일치 sha256_dedup 케이스).
    """
    # Normalise to forward-slash relative path from repo root
    rel = lake.current_rel(source_file)  # e.g. "data/silver/reports/markdown/yig/2025-11-10_피에스케이.md"
    md_url = f"{GITHUB_RAW_BASE}/{_url_encode_path(rel)}"

    stem = Path(source_file).stem
    hint = hints.get(stem)
    # Use the post/detail page URL, not the direct PDF download URL.
    page_url: str | None = hint.get("page_url") if hint else None

    # Fallback: SHA-based lookup for sha256_dedup entries whose stem differs from MD stem
    if page_url is None and sha_url_index:
        sha = _sha256_of_pdf(source_file)
        if sha:
            page_url = sha_url_index.get(sha)

    return md_url, page_url


def write_web_json(path: Path, rows: list[PerformanceRow], as_of: dt.date, excluded_sector_count: int, hints: dict[str, dict] | None = None) -> None:
    _hints = hints or {}
    sha_url_index = _build_sha_url_index(_hints)
    records = []
    for row in rows:
        record = round_floats(asdict(row))
        record["source_name"] = Path(str(record["source_file"])).name
        record["performance_bucket"] = performance_bucket(record["return_latest_pct"])  # type: ignore[arg-type]
        record["bucket_peak"] = performance_bucket(record["peak_return_24m_pct"])  # type: ignore[arg-type]
        md_url, pdf_url = build_source_urls(str(record["source_file"]), _hints, sha_url_index)
        record["source_md_url"] = md_url
        record["source_pdf_url"] = pdf_url
        records.append(record)

    groups: list[tuple[str, list[dict]]] = [("ALL", records)]
    for field in ("school", "market", "rating"):
        values = sorted({str(r[field]) for r in records})
        groups += [(f"{field}={v}", [r for r in records if str(r[field]) == v]) for v in values]

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "excluded_sector_count": excluded_sector_count,
        "records": records,
        "summary": [summarize(rows_, group) for group, rows_ in groups],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build point-in-time report target and price-performance dataset from converted Markdown equity reports.")
    parser.add_argument("--markdown-dir", default=lake.rel(lake.SILVER_MARKDOWN))
    parser.add_argument("--output", default=lake.rel(lake.GOLD_REPORT_PERFORMANCE))
    parser.add_argument("--parsed-output", default=lake.rel(lake.SILVER_REPORT_PARSED))
    parser.add_argument("--issues-output", default=lake.rel(lake.SILVER_REPORT_ISSUES))
    parser.add_argument("--web-output", default="src/data/report-performance.json")
    parser.add_argument("--as-of", default=dt.date.today().isoformat())
    parser.add_argument("--markets", nargs="+", default=["KR", "US"], choices=["KR", "US"])
    parser.add_argument("--schools", nargs="+", default=list(SCHOOLS), choices=list(SCHOOLS))
    parser.add_argument("--parse-only", action="store_true", help="시세 조회 없이 파싱 결과만 출력 (검증용)")
    parser.add_argument("--sleep", type=float, default=0.05, help="Seconds to sleep between market-data calls.")
    args = parser.parse_args()

    as_of = dt.date.fromisoformat(args.as_of)
    md_root = Path(args.markdown_dir)
    md_paths: list[tuple[Path, str]] = [(p, "smic") for p in sorted(md_root.glob("*.md"))]
    for school in SCHOOLS:
        sub = md_root / school
        if sub.exists():
            md_paths += [(p, school) for p in sorted(sub.glob("*.md"))]
    md_paths = [(p, s) for p, s in md_paths if s in set(args.schools)]

    hints = load_hints()
    parsed_all_raw = [parse_report(path, school, hints.get(path.stem)) for path, school in md_paths]
    apply_corrections(parsed_all_raw, load_corrections())
    resolve_missing_tickers(parsed_all_raw)
    canonicalize_kr_names(parsed_all_raw)

    # 다운스트림(백테스트·프론트엔드)이 ISO 날짜를 신뢰할 수 있도록 표지 날짜를 정규화한다
    for r in parsed_all_raw:
        normalized = date_from_string(r.report_date)
        r.report_date = normalized.isoformat() if normalized else None

    # 발간일 불가능 가드: voera/ewha <2015, 전체 <2000, 또는 미래 날짜
    guard_implausible_dates(parsed_all_raw, as_of)

    for r in parsed_all_raw:
        # 티커가 끝내 없으면 산업/전략 리포트로 분류 (목표가 채점 대상 아님)
        r.report_type = "company" if r.ticker else "sector"
        # STAR: 표지 날짜와 업로드일이 크게 어긋나면 발간일 신뢰도 플래그
        if r.school == "star" and r.report_date:
            hint_date = (hints.get(Path(r.source_file).stem) or {}).get("published_hint")
            parsed_date = date_from_string(r.report_date)
            uploaded = date_from_string(hint_date) if hint_date else None
            if parsed_date and uploaded and abs((parsed_date - uploaded).days) > 90:
                add_qa_flag(r, "report_date_far_from_upload")
    parsed_all = [r for r in parsed_all_raw if (r.ticker or "").upper() not in EXCLUDED_DELISTED_TICKERS]
    deduped = dedup_reports(parsed_all)
    dropped = len(parsed_all) - len(deduped)
    if dropped:
        print(f"deduped {dropped} duplicate reports")
    # 산업/전략 리포트는 시장 미식별이어도 아카이브에 포함한다 (채점 대상은 아님)
    parsed = [r for r in deduped if r.market in set(args.markets) or r.report_type == "sector"]

    if args.parse_only:
        write_csv(Path(args.parsed_output), parsed_all)
        write_csv(Path(args.issues_output), [r for r in parsed_all if r.parse_issue])
        by_school: dict[str, list[ParsedReport]] = {}
        for r in deduped:
            by_school.setdefault(r.school, []).append(r)
        for school, items in sorted(by_school.items()):
            with_target = sum(1 for r in items if r.target_price is not None)
            with_ticker = sum(1 for r in items if r.ticker)
            with_date = sum(1 for r in items if r.report_date)
            print(f"{school}: {len(items)} reports · ticker {with_ticker} · date {with_date} · target {with_target}")
        return 0

    write_csv(Path(args.parsed_output), parsed_all)
    write_csv(Path(args.issues_output), [r for r in parsed_all if r.parse_issue])

    # ── REQUIREMENT 1: only company reports go into the emitted dataset ────────
    sector_reports = [r for r in parsed if r.report_type == "sector"]
    company_reports = [r for r in parsed if r.report_type == "company"]
    excluded_sector_count = len(sector_reports)
    print(f"excluded sector/thematic reports: {excluded_sector_count}")

    groups: dict[tuple[str, str], list[ParsedReport]] = {}
    for r in company_reports:
        if r.ticker and r.report_date:
            groups.setdefault((r.market or "", r.ticker), []).append(r)

    year_start = dt.date(as_of.year, 1, 1)
    price_cache: dict[tuple[str, str], pd.DataFrame] = {}
    for i, ((market, ticker), reports) in enumerate(groups.items()):
        min_date = min(date_from_string(r.report_date) for r in reports if date_from_string(r.report_date))
        assert min_date is not None
        # YTD 계산을 위해 최소한 전년도 말 이전부터 확보
        start = min(min_date, year_start) - dt.timedelta(days=10)
        try:
            price_cache[(market, ticker)] = fetch_prices_cached(market, ticker, start, as_of, args.sleep)
        except Exception as exc:  # keep batch generation usable even when a single quote source fails
            print(f"market-data error {market} {ticker}: {exc}", file=sys.stderr)
            price_cache[(market, ticker)] = pd.DataFrame()
        if (i + 1) % 50 == 0:
            print(f"  prices {i + 1}/{len(groups)}", flush=True)

    # ── 시세 기반 발간가 적용 및 목표가 정합성 검증 ──────────────────────────────
    apply_market_prices_to_parsed(company_reports, price_cache)
    apply_target_sanity(company_reports)

    min_report = min((d for r in company_reports if (d := date_from_string(r.report_date))), default=year_start)
    idx_start = min(min_report, year_start) - dt.timedelta(days=10)
    benchmarks: dict[str, pd.DataFrame] = {}
    for name in ("KOSPI", "KOSDAQ", "US"):
        try:
            benchmarks[name] = fetch_index_cached(name, idx_start, as_of)
        except Exception as exc:  # noqa: BLE001 - 지수 조회 실패 시 알파 없이 진행
            print(f"index error {name}: {exc}", file=sys.stderr)
            benchmarks[name] = pd.DataFrame()

    rows: list[PerformanceRow] = []
    for r in company_reports:
        prices = price_cache.get((r.market or "", r.ticker or ""), pd.DataFrame())
        rows.append(evaluate_report(r, prices, as_of, benchmarks))

    rows.sort(key=lambda x: (x.report_date or "9999-99-99", x.market or "", x.ticker or "", x.source_file))
    compute_target_sequences(rows)
    write_csv(Path(args.output), rows)
    write_web_json(Path(args.web_output), rows, as_of, excluded_sector_count, hints)

    ok = sum(1 for row in rows if not row.data_issue)
    # rating_class distribution
    rc_dist: dict[str, int] = {}
    for row in rows:
        rc_dist[row.rating_class] = rc_dist.get(row.rating_class, 0) + 1
    print(f"parsed_reports={len(parsed_all)} included_us_kr={len(parsed)} company_rows={len(rows)} priced_rows={ok} as_of={as_of}")
    print(f"rating_class distribution: buy={rc_dist.get('buy', 0)} soft_buy={rc_dist.get('soft_buy', 0)} sell={rc_dist.get('sell', 0)}")
    print(f"excluded_sector_count={excluded_sector_count}")
    # Target sanity summary
    suspect_count = sum(1 for r in company_reports if r.qa_flags and "target_price_suspect" in r.qa_flags)
    swap_count = sum(1 for r in company_reports if r.qa_flags and "target_swapped_from_raw" in r.qa_flags)
    split_adj_count = sum(1 for r in company_reports if r.qa_flags and "target_split_adjusted" in r.qa_flags)
    d0_count = sum(1 for row in rows if row.days_to_target == 0)
    print(f"target_sanity: suspect={suspect_count} swapped={swap_count} split_adjusted={split_adj_count} days_to_target_zero={d0_count}")
    # bucket_peak distribution
    bp_dist: dict[str, int] = {}
    for row in rows:
        bp = performance_bucket(row.peak_return_24m_pct)
        bp_dist[bp] = bp_dist.get(bp, 0) + 1
    print(f"bucket_peak distribution: " + " ".join(f"{k}={v}" for k, v in sorted(bp_dist.items())))
    # target_seq sanity for 하이브 (352820)
    hybe_rows = [r for r in rows if r.ticker == "352820"]
    hybe_seq = [(r.school, r.report_date, r.target_seq, r.target_seq_total) for r in hybe_rows]
    print(f"하이브(352820) target_seq: {hybe_seq}")
    # source_pdf_url direct-PDF count (should be 0)
    pdf_direct_count = sum(1 for r in rows if r.source_pdf_url and r.source_pdf_url.endswith(".pdf"))
    print(f"source_pdf_url direct-PDF count (should be 0): {pdf_direct_count}")
    # implausible date guard count
    implausible_count = sum(1 for r in parsed_all_raw if r.qa_flags and "report_date_implausible" in r.qa_flags)
    print(f"report_date_implausible nulled: {implausible_count}")
    print(f"wrote {args.output}")
    print(f"wrote {args.parsed_output}")
    print(f"wrote {args.issues_output}")
    print(f"wrote {args.web_output}")
    return 0


if __name__ == "__main__":
    status = main()
    flush_bronze()  # a failed bronze write must fail the run; atexit cannot change the exit code
    raise SystemExit(status)
