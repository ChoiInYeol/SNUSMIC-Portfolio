"""bronze PDF(data/bronze/reports/pdfs/)를 kordoc으로 silver 마크다운(data/silver/reports/markdown/)에 전사한다.

- 엔진: kordoc (tools/kordoc 에 버전 고정, `npm ci --prefix tools/kordoc`). 먼저 텍스트층으로
  전사하고, kordoc 페이지 품질 판정상 표지나 문서 대부분에 텍스트층이 없는 문서만 내장 PP-OCRv5
  한국어 OCR(`--ocr`)로 다시 전사한다. 표는 GFM 파이프 표.
- 대상: {pdfs}/{school}/*.pdf → {markdown}/{school}/{stem}.md, 그리고 레거시 SMIC
  {pdfs}/*.pdf → {markdown}/{stem}.md. 경로는 scripts/lake.py 가 정한다.
- 각 .md 첫 줄은 출처 헤더다: `<!-- kordoc 4.22.13 ocr_pages=1,2 -->` (OCR 없으면 `ocr_pages=`).
  같은 kordoc 버전 헤더가 있는 파일은 건너뛴다 → 엔진 버전을 올리거나 --force 로만 재전사.

사용:
    python scripts/transcribe_pdfs.py [--school smic|...|legacy] [--limit N] [--shard i:n] [--force] [--jobs N]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from lake import BRONZE_PDFS as PDF_ROOT, ROOT, SILVER_MARKDOWN as MD_ROOT, resolve_data_path

KORDOC_DIR = ROOT / "tools" / "kordoc"
KORDOC_CLI = KORDOC_DIR / "node_modules" / "kordoc" / "dist" / "cli.js"
NO_MODELS_DIR = ROOT / "cache" / "kordoc-no-models"
SCHOOLS = ("smic", "yig", "star", "kuvic", "ewha", "voera")
BATCH_SIZE = 32
LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")
HEADER_RE = re.compile(r"^<!-- kordoc (?P<version>\S+) ocr_pages=(?P<pages>[\d,]*) -->")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def kordoc_version() -> str:
    if not KORDOC_CLI.exists():
        raise SystemExit("kordoc 미설치: npm ci --prefix tools/kordoc")
    package = json.loads((KORDOC_DIR / "node_modules" / "kordoc" / "package.json").read_text(encoding="utf-8"))
    return package["version"]


def header(version: str, ocr_pages: list[int]) -> str:
    return f"<!-- kordoc {version} ocr_pages={','.join(map(str, ocr_pages))} -->"


def is_current(md_path: Path, version: str) -> bool:
    if not md_path.exists():
        return False
    with open(md_path, encoding="utf-8", errors="ignore") as f:
        match = HEADER_RE.match(f.readline())
    return bool(match and match["version"] == version)


def render(result: dict, version: str) -> str:
    ocr_pages = [q["page"] for q in result.get("pageQuality", []) if q.get("ocrApplied")]
    # 일부 PDF 텍스트층은 짝 없는 UTF-16 서로게이트를 내보낸다 → UTF-8로 쓸 수 없으므로 U+FFFD로 치환
    body = LONE_SURROGATE_RE.sub("�", result.get("markdown", "").strip())
    return f"{header(version, ocr_pages)}\n\n{body}\n"


def needs_ocr(result: dict) -> bool:
    """kordoc 페이지 품질 판정 기준: 표지(1~3쪽)에 텍스트층이 없거나 문서 대부분이 스캔본.

    차트 이미지 페이지까지 전부 OCR하면 문서당 수 분이 걸리고 추출에는 기여하지 않는다.
    """
    pages = result.get("pageQuality", [])
    if not pages:
        return False
    cover = any(q.get("needsOcr") for q in pages if q.get("page", 99) <= 3)
    scanned = sum(1 for q in pages if q.get("needsOcr")) > len(pages) / 2
    return cover or scanned


def run_kordoc(pdfs: list[Path], out_dir: Path, jobs: int, ocr: bool, pages: str | None) -> dict[Path, dict]:
    cmd = ["node", str(KORDOC_CLI), *map(str, pdfs), "--format", "json", "--no-images",
           "--table-format", "gfm", "--silent", "--jobs", str(jobs), "-d", str(out_dir),
           *(["--ocr"] if ocr else []), *(["--pages", pages] if pages else [])]
    env = os.environ.copy()
    if not ocr:
        # kordoc은 OCR 모델이 캐시돼 있으면 --ocr 없이도 PDF 안의 이미지(차트)를 자동 OCR한다:
        # 문서당 1.7s → 70s, 대부분 저신뢰 차트 글자. 텍스트층 단계는 빈 모델 경로로 이를 막는다.
        NO_MODELS_DIR.mkdir(parents=True, exist_ok=True)
        env["KORDOC_MODEL_CACHE"] = str(NO_MODELS_DIR)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    results = {}
    for pdf in pdfs:
        out = out_dir / f"{pdf.stem}.json"
        if out.exists():
            result = json.loads(out.read_text(encoding="utf-8"))
            if result.get("success") and result.get("markdown", "").strip():
                results[pdf] = result
            out.unlink()
    if proc.returncode != 0 and len(results) == len(pdfs):
        print(f"  ! kordoc exit {proc.returncode} without missing outputs: {proc.stderr.strip()[-300:]}", flush=True)
    return results


def transcribe_batch(pdfs: list[Path], target_dir: Path, version: str, jobs: int,
                     pages: str | None = None) -> tuple[int, int, list[str]]:
    """텍스트층 전사 → kordoc 품질 판정상 필요한 문서만 --ocr 재전사 → 헤더 붙여 원자적 저장.

    반환: (성공 수, OCR 적용 문서 수, 실패 파일명)
    """
    valid = [pdf for pdf in pdfs if pdf.stat().st_size > 0]
    failed = [pdf.name for pdf in pdfs if pdf.stat().st_size == 0]
    if not valid:
        return 0, 0, failed
    with tempfile.TemporaryDirectory(prefix="kordoc_") as tmp:
        results = run_kordoc(valid, Path(tmp), jobs, ocr=False, pages=pages)
        scans = [pdf for pdf in valid if pdf not in results or needs_ocr(results[pdf])]
        if scans:
            results.update(run_kordoc(scans, Path(tmp), jobs, ocr=True, pages=pages))
    done = 0
    for pdf in valid:
        if pdf not in results:
            failed.append(pdf.name)
            continue
        target = target_dir / f"{pdf.stem}.md"
        partial = target.with_suffix(".md.partial")
        partial.write_text(render(results[pdf], version), encoding="utf-8")
        partial.replace(target)
        done += 1
    return done, len(scans), failed


def targets(school: str, md_root: Path) -> tuple[Path, Path]:
    if school == "legacy":
        return PDF_ROOT, md_root
    return PDF_ROOT / school, md_root / school


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--school", choices=[*SCHOOLS, "legacy"], default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--shard", default=None, help="i:n — 정렬된 대기열의 i번째 1/n만 처리")
    parser.add_argument("--force", action="store_true", help="헤더가 최신이어도 재전사")
    parser.add_argument("--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 2) // 2)))
    parser.add_argument("--pages", default=None, help="쪽 범위 (예: 1-3). 지정 시 --md-root 를 본문 전사본 밖으로 둘 것")
    parser.add_argument("--md-root", type=Path, default=MD_ROOT, help="출력 루트 (기본 data/silver/reports/markdown)")
    parser.add_argument("--only", type=Path, default=None, help="처리할 PDF 경로 JSON 목록 (저장소 루트 기준, 구 data/pdfs/... 경로도 허용)")
    args = parser.parse_args()
    if args.pages and args.md_root.resolve() == MD_ROOT.resolve():
        parser.error("--pages 결과로 본문 전사본(data/silver/reports/markdown)을 덮어쓸 수 없다 — --md-root 를 지정하라")
    only = None
    if args.only:
        only = {resolve_data_path(p).resolve() for path in [args.only] for p in json.loads(path.read_text(encoding="utf-8"))}

    version = kordoc_version()
    total_done, total_failed = 0, []
    for school in ([args.school] if args.school else [*SCHOOLS, "legacy"]):
        pdf_dir, md_dir = targets(school, args.md_root)
        if not pdf_dir.exists():
            continue
        md_dir.mkdir(parents=True, exist_ok=True)
        pending = [pdf for pdf in sorted(pdf_dir.glob("*.pdf"))
                   if (only is None or pdf.resolve() in only)
                   and (args.force or not is_current(md_dir / f"{pdf.stem}.md", version))]
        if args.shard:
            i, n = map(int, args.shard.split(":"))
            pending = pending[i::n]
        if args.limit:
            pending = pending[: args.limit]
        print(f"[{school}] {len(pending)} PDFs to transcribe with kordoc {version}", flush=True)
        for start in range(0, len(pending), BATCH_SIZE):
            done, ocr_docs, failed = transcribe_batch(pending[start:start + BATCH_SIZE], md_dir, version, args.jobs, args.pages)
            total_done += done
            total_failed += [f"{school}/{name}" for name in failed]
            print(f"  [{school}] {min(start + BATCH_SIZE, len(pending))}/{len(pending)} "
                  f"(+{done}, ocr {ocr_docs}, failed {len(failed)})", flush=True)

    print(f"done: {total_done} transcribed, {len(total_failed)} failed", flush=True)
    for name in total_failed:
        print(f"  FAILED {name}", flush=True)
    # 개별 PDF 실패는 기존 .md(있다면)를 보존하고 진행한다; 전부 실패하면 엔진 장애다.
    return 1 if total_failed and total_done == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
