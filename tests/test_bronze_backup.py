import json

import pytest

import bronze_backup as backup


def test_restore_after_cache_loss_and_deduplicated_objects(tmp_path):
    bronze = tmp_path / "bronze"; bronze.mkdir()
    (bronze / "source.pdf").write_bytes(b"%PDF-original")
    (bronze / "manifest.json").write_text('{"version":1}')
    store = tmp_path / "independent-store"
    first = backup.create(bronze, store)
    backup.create(bronze, store)
    assert len(list((store / "objects").glob("*/*"))) == 2
    destination = tmp_path / "empty-machine" / "bronze"
    assert backup.restore(store, first, destination) == 2
    assert (destination / "source.pdf").read_bytes() == b"%PDF-original"
    assert backup.restore(store, first, destination) == 0


def test_corruption_and_conflict_fail_before_partial_restore(tmp_path):
    bronze = tmp_path / "bronze"; bronze.mkdir()
    (bronze / "a").write_bytes(b"a"); (bronze / "b").write_bytes(b"b")
    store = tmp_path / "store"; manifest = backup.create(bronze, store)
    dest = tmp_path / "restore"; dest.mkdir(); (dest / "b").write_bytes(b"newer source")
    with pytest.raises(ValueError, match="conflicts"):
        backup.restore(store, manifest, dest)
    assert not (dest / "a").exists()
    doc = json.loads(manifest.read_text())
    backup.object_path(store, doc["files"][0]["sha256"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        backup.verify(store, manifest)


@pytest.mark.parametrize("name", ["../secret", "/absolute", "C:/secret", "a\\..\\secret", "a/../../secret"])
def test_untrusted_manifest_cannot_escape_restore_root(tmp_path, name):
    with pytest.raises(ValueError): backup.safe_path(tmp_path, name)


def test_latest_restore_composes_full_and_delta_snapshots(tmp_path):
    bronze = tmp_path / "bronze"; bronze.mkdir()
    (bronze / "a").write_bytes(b"original")
    store = tmp_path / "store"; backup.create(bronze, store)
    (bronze / "a").write_bytes(b"new manifest version")
    (bronze / "b").write_bytes(b"new PDF")
    backup.create(bronze, store)
    dest = tmp_path / "restore"
    assert backup.restore_latest(store, dest) == 2
    assert (dest / "a").read_bytes() == b"new manifest version"
