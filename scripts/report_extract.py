"""리포트 표지에서 목표주가·현재주가·상승여력·투자의견을 뽑는 후보-채점 추출기.

기존 parse_prices/parse_rating은 "첫 매치가 이긴다" 체인이라 앞 단계가 틀린 값을 잡으면 끝이다.
여기서는 모든 레이아웃 해석기가 후보를 내고, 조합 전체를 함께 채점한다:

  score(t, c, u) = Σ 후보 근거점수 + 산술 정합(t/c−1 ≈ u) + 시세 정합(c ≈ 발간일 종가) − 비현실성

입력은 kordoc 마크다운(GFM 표, 탭 구분 OCR 줄)이지만 일반 텍스트에도 동작한다.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from report_text import strip_missing_images

COVER_LINES = 200
HEADER_RE = re.compile(r"^<!--.*?-->\s*$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}")

TARGET_LABEL = r"목표\s*주가|적정\s*주가|목표주7[Hh]|Target\s*Price|TP\b"
CURRENT_LABEL = r"현재\s*주가|현재가(?!치)|Current\s*Price|주가\s*\(\s*\d{1,2}\s*[./월]"
UPSIDE_LABEL = r"상승\s*여력|하락\s*여력|Upside(?:\s*Potential)?|Downside"
RATING_LABEL = r"투자\s*의견|Rating|Investment\s*Opinion|Recommendation|Opinion"
LABEL_QUALIFIER = r"(?:\s*[\(\[][^)\]]{0,20}[\)\]])?"
LABELS = {
    "target": re.compile(rf"(?:{TARGET_LABEL}){LABEL_QUALIFIER}", re.I),
    "current": re.compile(rf"(?:{CURRENT_LABEL}){LABEL_QUALIFIER}", re.I),
    "upside": re.compile(rf"(?:{UPSIDE_LABEL}){LABEL_QUALIFIER}", re.I),
}
RATING_LABEL_RE = re.compile(RATING_LABEL, re.I)
RATING_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])(Strong\s*Buy|Buy|Sell|Hold|Neutral|Outperform|Underperform|Overweight|Underweight)(?![A-Za-z])"
    r"|(강력\s*매수|적극\s*매수|매수|매도|중립|보유)",
    re.I,
)
RATING_LEAD_RE = re.compile(
    r"^\W{0,3}(Strong\s*Buy|Buy|Sell|Hold|Neutral|Outperform|Underperform|Overweight|Underweight)()"
    r"\s*[\(\[]\s*(?:신규|유지|상향|하향|maintain|initiate|upgrade|downgrade)\s*[\)\]]",
    re.I,
)
RATING_CANON = {
    "strongbuy": "Strong Buy", "buy": "Buy", "sell": "Sell", "hold": "Hold", "neutral": "Neutral",
    "outperform": "Outperform", "underperform": "Underperform", "overweight": "Overweight",
    "underweight": "Underweight", "강력매수": "Strong Buy", "적극매수": "Strong Buy", "매수": "Buy",
    "매도": "Sell", "중립": "Neutral", "보유": "Hold",
}
PRICE_RE = re.compile(
    r"(?P<prefix>[$₩])?\s*(?P<a>\d{1,3}(?:[,.]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s*[~\-–]\s*[$₩]?\s*(?P<b>\d{1,3}(?:[,.]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?))?"
    r"\s*(?P<suffix>원|KRW|달러|USD|\$)?"
)
PCT_RE = re.compile(r"(?P<sign>[+\-−])?\s*(?P<v>\d+(?:\.\d+)?)\s*%")
BAD_UNIT_AFTER = re.compile(r"^\s*(%|배|x\b|X\b|억|조|만|천|bn|mn|주\b|년|월|일|E\b|F\b)")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")


@dataclass(frozen=True)
class Candidate:
    field: str
    value: float
    raw: str
    source: str
    score: float
    order: int


@dataclass(frozen=True)
class Extraction:
    target: float | None
    target_raw: str | None
    current: float | None
    current_raw: str | None
    upside: float | None
    rating: str | None
    target_source: str | None
    score: float
    consistent: bool  # t, c, u all present and t/c−1 matches u within rounding


# ── text → rows of segments ──────────────────────────────────────────────────

def cover_rows(text: str, limit: int = COVER_LINES) -> list[list[str]]:
    """표지 영역을 '행 = 세그먼트 목록'으로. GFM 표는 셀, <br>은 하위 행, 탭/넓은 공백은 세그먼트."""
    rows: list[list[str]] = []
    for line in strip_missing_images(text).splitlines()[:limit]:
        line = line.strip()
        if not line or HEADER_RE.match(line) or TABLE_SEPARATOR_RE.match(line):
            continue
        line = line.lstrip("#").strip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            parts = [[p.strip() for p in c.split("<br>")] for c in cells]
            if any(len(p) > 1 for p in parts):
                # 셀마다 줄 수가 다르면 셀 내부 줄을 각각 행으로 편다(표지 박스 안 라벨/값 줄)
                for p in parts:
                    rows.extend([[x] for x in p if x])
                continue
            rows.append([c for c in cells if c])
        else:
            segs = [s.strip() for s in re.split(r"\t|\s{3,}", line) if s.strip()]
            rows.append(segs)
    return [r for r in rows if r]


def _number(raw: str, market: str | None) -> float | None:
    s = raw.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s) and market != "US":
        s = s.replace(".", "")
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def price_tokens(segment: str, market: str | None) -> list[tuple[int, float, str, bool]]:
    """세그먼트 안의 가격 후보 (시작위치, 값, 원문, 통화표기여부). 멀티플·비율·연도·단위금액 제외."""
    out = []
    for m in PRICE_RE.finditer(segment):
        if not m.group("a"):
            continue
        if BAD_UNIT_AFTER.match(segment[m.end():m.end() + 4]):
            continue
        a = _number(m.group("a"), market)
        if a is None:
            continue
        b = _number(m.group("b"), market) if m.group("b") else None
        value = (a + b) / 2 if b is not None else a
        currency = bool(m.group("prefix") or m.group("suffix"))
        if not currency and YEAR_RE.match(m.group("a")):
            continue
        if market == "US" and m.group("suffix") == "원":
            continue
        if market == "KR" and (m.group("prefix") == "$" or (m.group("suffix") or "") in {"$", "USD", "달러"}):
            continue
        out.append((_lead(m), value, m.group(0).strip(), currency))
    return out


def _lead(m: re.Match[str]) -> int:
    """매치의 첫 비공백 문자 위치 — 패턴 앞 공백을 포함하면 값이 라벨보다 앞서 정렬된다."""
    return m.start() + len(m.group(0)) - len(m.group(0).lstrip())


DATE_MASK_RE = re.compile(r"\d{4}\s*[./-]\s*\d{1,2}(?:\s*[./-]\s*\d{1,2})?|\b\d{1,2}\s*/\s*\d{1,2}\b|\d{2,4}\s*E\b")


@dataclass(frozen=True)
class Token:
    kind: str  # "label" | "price" | "pct"
    field: str | None
    value: float
    raw: str
    currency: bool
    downside: bool
    row: int
    seg: int


def tokenize(rows: list[list[str]], market: str | None) -> list[Token]:
    """표지를 읽기 순서의 라벨/값 토큰 열로. 라벨·날짜 구간은 값 스캔에서 가린다."""
    stream: list[Token] = []
    for ri, row in enumerate(rows):
        for si, seg in enumerate(row):
            items: list[tuple[int, Token]] = []
            masked = seg
            for field, rx in LABELS.items():
                for m in rx.finditer(seg):
                    down = bool(re.search(r"하락|Downside", m.group(0), re.I))
                    items.append((m.start(), Token("label", field, 0, m.group(0), False, down, ri, si)))
                    masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
            masked = DATE_MASK_RE.sub(lambda m: " " * len(m.group(0)), masked)
            for m in PCT_RE.finditer(masked):
                v = float(m.group("v")) * (-1 if m.group("sign") in {"-", "−"} else 1)
                items.append((_lead(m), Token("pct", None, v, m.group(0).strip(), True, False, ri, si)))
                masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
            for pos, v, raw, cur in price_tokens(masked, market):
                items.append((pos, Token("price", None, v, raw, cur, False, ri, si)))
            stream.extend(t for _, t in sorted(items, key=lambda x: x[0]))
    return stream


def _pair_score(label: Token, value: Token, block: bool) -> float:
    if block:
        return 0.85
    if (label.row, label.seg) == (value.row, value.seg):
        return 1.0
    if label.row == value.row:
        return 0.95
    return 0.7 if value.row - label.row <= 2 else 0.4


# ── candidate generators ─────────────────────────────────────────────────────

def candidates(rows: list[list[str]], market: str | None) -> list[Candidate]:
    """라벨 다음에 오는 같은 종류의 값과 짝짓는다. 라벨이 연달아 k개면 뒤따르는 값 k개를 순서대로."""
    stream = tokenize(rows, market)
    found: list[Candidate] = []
    i = 0
    while i < len(stream):
        if stream[i].kind != "label":
            i += 1
            continue
        labels = [stream[i]]
        j = i + 1
        while j < len(stream) and stream[j].kind == "label" and len(labels) < 4:
            if stream[j].field != labels[-1].field:
                labels.append(stream[j])
            j += 1
        block = len(labels) > 1
        values = []
        k = j
        while k < len(stream) and stream[k].kind != "label" and len(values) < 3 * len(labels):
            values.append(stream[k])
            k += 1
        used: set[int] = set()
        for label in labels:
            want = "pct" if label.field == "upside" else "price"
            for vi, value in enumerate(values):
                if vi in used or value.kind != want:
                    continue
                used.add(vi)
                v = -abs(value.value) if label.downside else value.value
                score = _pair_score(label, value, block) + (0.15 if value.currency else 0.0)
                found.append(Candidate(label.field, v, value.raw, "block" if block else "pair",
                                       score - len(found) * 1e-4, len(found)))
                break
        i = j
    return found


def rating_candidates(rows: list[list[str]]) -> list[Candidate]:
    found: list[Candidate] = []
    flat = [(ri, si, seg) for ri, row in enumerate(rows) for si, seg in enumerate(row)]
    for idx, (ri, si, seg) in enumerate(flat):
        canon = None
        lab = RATING_LABEL_RE.search(seg)
        if lab:
            for offset, s in enumerate([seg[lab.end():]] + [x[2] for x in flat[idx + 1:idx + 4]]):
                m = RATING_TOKEN_RE.search(s)
                if m:
                    canon = _canon(m)
                    found.append(Candidate("rating", 0, canon, "labeled", 1.0 - 0.1 * offset, idx))
                    break
            continue
        if idx >= 120:
            continue
        m = RATING_TOKEN_RE.fullmatch(re.sub(r"[\[\]()（）:：*\s]+|유지|상향|하향|신규|maintain|initiate", " ", seg, flags=re.I).strip())
        if m:
            found.append(Candidate("rating", 0, _canon(m), "standalone", 0.8, idx))
            continue
        # 세그먼트 맨 앞 등급 + 변경 표기: "Buy(신규) 목표주가(신규): 190,500원"
        m = RATING_LEAD_RE.match(seg)
        if m:
            found.append(Candidate("rating", 0, _canon(m), "lead", 0.75, idx))
    return found


def _canon(m: re.Match[str]) -> str:
    key = re.sub(r"\s+", "", (m.group(1) or m.group(2))).lower()
    return RATING_CANON.get(key, key)


# ── joint selection ──────────────────────────────────────────────────────────

def _top(cands: list[Candidate], field: str, n: int = 5) -> list[Candidate | None]:
    best: dict[float, Candidate] = {}
    for c in cands:
        if c.field == field and (c.value not in best or c.score > best[c.value].score):
            best[c.value] = c
    return [*sorted(best.values(), key=lambda c: -c.score)[:n], None]


def _combo_score(t: Candidate | None, c: Candidate | None, u: Candidate | None,
                 market: str | None, market_close: float | None) -> tuple[float, bool]:
    score = sum(x.score for x in (t, c, u) if x)
    minimum = 0.2 if market == "US" else 100
    for x in (t, c):
        if x and x.value < minimum:
            score -= 3
    consistent = False
    if t and c:
        ratio = t.value / c.value if c.value else 0
        if t.raw == c.raw and t.order == c.order:
            score -= 3
        if not 0.2 <= ratio <= 10:
            score -= 2
        if u:
            err = abs((ratio - 1) * 100 - u.value)
            if err <= 1.0:
                score += 1.5
                consistent = True
            elif err <= 3.0:
                score += 0.8
                consistent = True
            else:
                score -= 1.0
    if market_close:
        if c:
            rel = abs(c.value / market_close - 1)
            score += 1.0 if rel <= 0.03 else 0.4 if rel <= 0.10 else -1.0 if rel > 0.35 else 0
        if t and not 0.2 <= t.value / market_close <= 10:
            score -= 1.5
    return score, consistent


def extract(text: str, market: str | None, market_close: float | None = None) -> Extraction:
    rows = cover_rows(text)
    cands = candidates(rows, market)
    best = (float("-inf"), None, None, None, False)
    for t, c, u in itertools.product(_top(cands, "target"), _top(cands, "current"), _top(cands, "upside", 4)):
        s, ok = _combo_score(t, c, u, market, market_close)
        if s > best[0]:
            best = (s, t, c, u, ok)
    s, t, c, u, ok = best
    ratings = rating_candidates(rows)
    rating = max(ratings, key=lambda r: (r.score, -r.order)).raw if ratings else None
    return Extraction(
        target=t.value if t else None, target_raw=t.raw if t else None,
        current=c.value if c else None, current_raw=c.raw if c else None,
        upside=u.value if u else None, rating=rating,
        target_source=t.source if t else None, score=round(s, 3), consistent=ok,
    )
