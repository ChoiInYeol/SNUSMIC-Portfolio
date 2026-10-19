"""Manifest-backed filenames, including the EIA collector's collision fallback."""
import re
import unicodedata
from pathlib import PurePosixPath


def manifest_stems(row: dict) -> set[str]:
    stems = {PurePosixPath(row["file"].replace("\\", "/")).stem} if row.get("file") else set()
    if row.get("school") == "ewha" and row.get("published_hint") and row.get("title"):
        # collect_ewha uses the title first, then source_id if that path already exists.
        alias = unicodedata.normalize("NFC", f"{row['published_hint']}_{row['title']}")
        alias = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", alias)
        stems.add(re.sub(r"\s+", " ", alias).strip().strip(".")[:80])
    return stems
