"""Manifest-backed filenames, including the EIA collector's collision fallback."""
import re
import json
from functools import cache
import unicodedata
from pathlib import PurePosixPath
import lake


@cache
def transcription_version() -> str:
    package = json.loads((lake.ROOT / "tools" / "kordoc" / "package.json").read_text(encoding="utf-8"))
    return package["dependencies"]["kordoc"]


def transcription_complete(row: dict) -> bool:
    """A verified manifest object with a current, nonempty committed transcript needs no PDF cache."""
    if row.get("error") or not row.get("file") or not re.fullmatch(r"[a-f0-9]{64}", row.get("sha256") or ""):
        return False
    school = row.get("school", "")
    if not re.fullmatch(r"[a-z]+", school):
        return False
    for stem in manifest_stems(row):
        candidate = lake.SILVER_MARKDOWN / school / f"{stem}.md"
        try:
            with candidate.open(encoding="utf-8") as fh:
                first, body = fh.readline(), fh.read(512).strip()
            if first.startswith(f"<!-- kordoc {transcription_version()} ") and len(body) >= 32:
                return True
        except (OSError, UnicodeError):
            continue
    return False


def manifest_stems(row: dict) -> set[str]:
    stems = {PurePosixPath(row["file"].replace("\\", "/")).stem} if row.get("file") else set()
    if row.get("school") == "ewha" and row.get("published_hint") and row.get("title"):
        # collect_ewha uses the title first, then source_id if that path already exists.
        alias = unicodedata.normalize("NFC", f"{row['published_hint']}_{row['title']}")
        alias = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", alias)
        stems.add(re.sub(r"\s+", " ", alias).strip().strip(".")[:80])
    return stems
