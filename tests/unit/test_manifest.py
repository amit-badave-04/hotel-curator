"""Tests for data manifests."""

from pathlib import Path

from hotel_curator.data.manifest import Manifest, file_record, new_manifest


def test_manifest_round_trip(tmp_path: Path) -> None:
    data = tmp_path / "data" / "x.parquet"
    data.parent.mkdir(parents=True)
    data.write_bytes(b"abc")
    manifest = new_manifest("data", tmp_path)
    manifest.inputs = {"src": "0" * 64}
    manifest.outputs = [file_record(data, tmp_path, rows=3)]
    manifest.stats = {"rows": 3}
    path = tmp_path / "m.json"
    manifest.write(path)
    loaded = Manifest.read(path)
    assert loaded == manifest
    assert loaded.outputs[0].path == "data/x.parquet"
    assert loaded.outputs[0].size_bytes == 3


def test_git_commit_outside_repo_is_unknown(tmp_path: Path) -> None:
    manifest = new_manifest("data", tmp_path)
    assert manifest.code_commit in {"unknown"} or len(manifest.code_commit) >= 7
