"""Content-addressed Bronze backup on an independent filesystem (local disk, mounted NAS, etc.).

create -> immutable SHA256 objects and snapshot manifest; verify -> checksum every referenced
object; restore -> preflight all paths, hashes and existing files, then create missing files only.
No cache dependency, pruning, source replacement, symlinks or archive extraction.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath

import lake
from lake_prices import publish_new


def sha(path: Path) -> str:
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def safe_path(root: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).as_posix() != name or any(p in ("..", ".") or ":" in p or "\\" in p or p.endswith((".", " ")) for p in parts) or name.startswith("/"):
        raise ValueError("unsafe backup path")
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root.resolve()) or any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError("backup path escapes root or is a symlink")
    return path


def object_path(store: Path, digest: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid object hash")
    return safe_path(store, f"objects/{digest[:2]}/{digest}")


def create(root: Path, store: Path) -> Path:
    if store.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(store.resolve()):
        raise ValueError("store and Bronze must be separate directories")
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink in Bronze")
        if not path.is_file() or path.name.endswith((".tmp", ".lock")):
            continue
        name = path.relative_to(root).as_posix()
        target_tmp_root = store / "staging"
        target_tmp_root.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=target_tmp_root)
        try:
            before = path.stat()
            with os.fdopen(fd, "wb") as out, path.open("rb") as src:
                shutil.copyfileobj(src, out)
                out.flush(); os.fsync(out.fileno())
            temp = Path(temp_name)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError(f"source changed during backup; retry after writers finish: {name}")
            digest = sha(temp); target = object_path(store, digest)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if sha(target) != digest:
                    raise ValueError("corrupt existing backup object")
            else:
                try:
                    os.link(temp, target)
                except FileExistsError:
                    if sha(target) != digest:
                        raise ValueError("corrupt concurrent backup object")
            rows.append({"path": name, "sha256": digest, "bytes": temp.stat().st_size})
        finally:
            Path(temp_name).unlink(missing_ok=True)
    if not rows:
        raise ValueError("refusing empty Bronze backup")
    payload = {"schema_version": "1.0", "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "files": rows}
    encoded = json.dumps(payload, ensure_ascii=False, indent=1).encode()
    manifest = store / "snapshots" / f"{hashlib.sha256(encoded).hexdigest()}.json"
    publish_new(manifest, encoded)
    verify(store, manifest)
    return manifest


def verify(store: Path, manifest: Path) -> dict:
    encoded = manifest.read_bytes()
    if re.fullmatch(r"[a-f0-9]{64}", manifest.stem) and hashlib.sha256(encoded).hexdigest() != manifest.stem:
        raise ValueError("manifest checksum mismatch")
    doc = json.loads(encoded)
    if doc.get("schema_version") != "1.0" or not doc.get("files"):
        raise ValueError("invalid manifest")
    names = set()
    for row in doc["files"]:
        safe_path(store / "validation-root", row["path"])
        if row["path"].casefold() in names:
            raise ValueError("duplicate backup path")
        names.add(row["path"].casefold())
        path = object_path(store, row["sha256"])
        if path.stat().st_size != row["bytes"] or sha(path) != row["sha256"]:
            raise ValueError(f"backup checksum mismatch: {row['path']}")
    return doc


def restore(store: Path, manifest: Path, destination: Path) -> int:
    doc = verify(store, manifest)
    return restore_document(store, doc, destination)


def restore_document(store: Path, doc: dict, destination: Path) -> int:
    pending = []
    for row in doc["files"]:
        target = safe_path(destination, row["path"])
        if target.exists():
            if not target.is_file() or sha(target) != row["sha256"]:
                raise ValueError(f"restore conflicts with existing source: {row['path']}")
        else:
            pending.append((row, target))
    for row, target in pending:
        # publish_new refuses a concurrent writer rather than overwriting source bytes.
        publish_new(target, object_path(store, row["sha256"]).read_bytes())
    return len(pending)


def restore_latest(store: Path, destination: Path) -> int:
    """Compose the newest version of each path across full and subsequent delta snapshots."""
    docs = [verify(store, p) for p in sorted((store / "snapshots").glob("*.json"))]
    if not docs: raise ValueError("no backup snapshots")
    newest = {}
    for doc in sorted(docs, key=lambda d: d["created_at"]):
        for row in doc["files"]: newest[row["path"]] = row
    return restore_document(store, {"files": list(newest.values())}, destination)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["create", "verify", "restore", "restore-latest"])
    p.add_argument("--store", type=Path, required=True)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--destination", type=Path)
    args = p.parse_args()
    if args.action == "create":
        print(create(lake.BRONZE, args.store))
    elif args.action == "verify":
        if not args.manifest: p.error("--manifest required")
        print(f"Verified {len(verify(args.store, args.manifest)['files'])} files")
    elif args.action == "restore-latest":
        if not args.destination: p.error("--destination required")
        print(f"Restored {restore_latest(args.store, args.destination)} files without overwriting")
    else:
        if not args.manifest or not args.destination: p.error("--manifest and --destination required")
        print(f"Restored {restore(args.store, args.manifest, args.destination)} files without overwriting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
