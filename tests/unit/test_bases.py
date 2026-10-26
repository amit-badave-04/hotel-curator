"""Tests for pinned base-model fetching, extraction and conversion."""

import hashlib
import io
import re
import tarfile
from pathlib import Path

import pytest

import hotel_curator.ml.bases as bases
from hotel_curator.data.download import ChecksumMismatchError
from hotel_curator.ml.bases import (
    BASE_MODELS,
    BaseModel,
    LayerMember,
    RemoteFile,
    extract_members,
    fetch_base,
)

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _layer(path: Path, files: dict[str, bytes], link: str = "") -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if link:
            info = tarfile.TarInfo(link)
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)


def test_registry_is_pinned_and_https_only() -> None:
    assert set(BASE_MODELS) == {
        "roberta-base",
        "electra-small",
        "bge-small",
        "mmbert-small",
    }
    for model in BASE_MODELS.values():
        assert model.files or model.layer
        for remote in model.files:
            assert remote.url.startswith("https://")
            assert HEX64.match(remote.sha256)
        if model.layer:
            assert HEX64.match(model.layer.digest)
            assert all(HEX64.match(m.sha256) for m in model.layer.members)
            assert {m.name for m in model.layer.members} >= {
                "config.json",
                "model.safetensors",
                "tokenizer.json",
            }


def test_extract_members_reads_only_listed_regular_files(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "layer.tar.gz"
    _layer(
        archive,
        {"app/m/config.json": b"{}", "../evil.txt": b"x"},
        link="app/m/model.safetensors",
    )
    target = tmp_path / "out"
    members = (LayerMember("config.json", "app/m/config.json", _sha(b"{}")),)
    extract_members(archive, members, target)
    assert sorted(p.name for p in target.iterdir()) == ["config.json"]
    assert not (tmp_path / "evil.txt").exists()


def test_extract_members_rejects_links_and_missing_files(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "layer.tar.gz"
    _layer(archive, {}, link="app/m/model.safetensors")
    members = (
        LayerMember("model.safetensors", "app/m/model.safetensors", "0" * 64),
    )
    with pytest.raises(KeyError, match=r"model\.safetensors"):
        extract_members(archive, members, tmp_path / "out")


def test_extract_members_verifies_checksums(tmp_path: Path) -> None:
    archive = tmp_path / "layer.tar.gz"
    _layer(archive, {"app/m/config.json": b"{}"})
    members = (LayerMember("config.json", "app/m/config.json", "0" * 64),)
    with pytest.raises(ChecksumMismatchError):
        extract_members(archive, members, tmp_path / "out")
    assert not (tmp_path / "out" / "config.json").exists()


def test_convert_pickle_to_safetensors_round_trip(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    from safetensors.torch import load_file

    weights = {"a.weight": torch.arange(6.0).reshape(2, 3)}
    weights["b.weight"] = weights["a.weight"]  # tied, shares storage
    torch.save(weights, tmp_path / bases.PICKLE_WEIGHTS)
    out = bases.convert_pickle_to_safetensors(tmp_path)
    loaded = load_file(str(out))
    assert torch.equal(loaded["a.weight"], weights["a.weight"])
    assert torch.equal(loaded["b.weight"], weights["a.weight"])
    assert not (tmp_path / bases.PICKLE_WEIGHTS).exists()


def test_convert_refuses_pickled_code(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    import pickle

    class Evil:
        def __reduce__(self) -> tuple[object, tuple[str]]:
            return (print, ("pwned",))

    with (tmp_path / bases.PICKLE_WEIGHTS).open("wb") as handle:
        pickle.dump({"x": Evil()}, handle, protocol=2)
    with pytest.raises(pickle.UnpicklingError):
        bases.convert_pickle_to_safetensors(tmp_path)
    assert torch is not None


def test_fetch_base_downloads_once_and_records_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b'{"model_type": "bert"}'
    calls: list[str] = []

    def fake_download(url: str, target: Path, sha256: str, **_: object) -> Path:
        calls.append(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        assert _sha(payload) == sha256
        return target

    monkeypatch.setattr(bases, "download_file", fake_download)
    model = BaseModel(
        key="tiny",
        title="tiny",
        params_millions=0,
        license="MIT",
        provenance="test",
        files=(RemoteFile("config.json", "https://x/c.json", _sha(payload)),),
    )
    first = fetch_base(model, tmp_path)
    second = fetch_base(model, tmp_path)
    assert first == second == tmp_path / "tiny"
    assert calls == ["https://x/c.json"]
    assert '"license": "MIT"' in (first / "PROVENANCE.json").read_text()
