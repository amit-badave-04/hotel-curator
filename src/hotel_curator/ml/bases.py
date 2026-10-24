"""Pretrained base encoders: where they come from and how they are checked.

huggingface.co is blocked on the build machine (docs/BLOCKERS.md, B1), so the
base models are fetched from the two reachable hosts found in ADR 005:

* Hugging Face's legacy public S3 bucket (the original 2019-2020 uploads of
  roberta-base and ELECTRA-small), file by file;
* image layers of a third-party inference image on Docker Hub (bge-small and
  mmBERT-small). A layer is a ``tar.gz`` addressed by its SHA-256 digest; only
  named regular files are read from it, never extracted wholesale.

Every file is pinned by SHA-256. Hashes of files that have no published hash
were recorded on first download (2026-10-24) and are checked on every later
download. Pickled PyTorch weights are loaded with ``weights_only=True`` (no
code execution) and re-saved as safetensors; the pickle is then deleted.
"""

import json
import logging
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from hotel_curator.data.download import (
    TIMEOUT_SECONDS,
    ChecksumMismatchError,
    download_file,
    sha256_file,
)

logger = logging.getLogger(__name__)

S3_BASE = "https://s3.amazonaws.com/models.huggingface.co/bert"
DOCKER_REPOSITORY = "semitechnologies/transformers-inference"
DOCKER_AUTH = (
    "https://auth.docker.io/token?service=registry.docker.io"
    f"&scope=repository:{DOCKER_REPOSITORY}:pull"
)
DOCKER_BLOB = f"https://registry-1.docker.io/v2/{DOCKER_REPOSITORY}/blobs/"
PICKLE_WEIGHTS = "pytorch_model.bin"
SAFE_WEIGHTS = "model.safetensors"


@dataclass(frozen=True)
class RemoteFile:
    """One file fetched over HTTPS and saved as ``name``."""

    name: str
    url: str
    sha256: str


@dataclass(frozen=True)
class LayerMember:
    """One regular file inside a Docker layer, saved as ``name``."""

    name: str
    path: str
    sha256: str


@dataclass(frozen=True)
class DockerLayer:
    """A Docker Hub image layer (``tar.gz``) addressed by its digest."""

    digest: str
    size: int
    members: tuple[LayerMember, ...]


@dataclass(frozen=True)
class BaseModel:
    """A pretrained encoder and the pinned files that make it up."""

    key: str
    title: str
    params_millions: int
    license: str
    provenance: str
    files: tuple[RemoteFile, ...] = ()
    layer: DockerLayer | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def _s3(prefix: str, names: dict[str, str]) -> tuple[RemoteFile, ...]:
    """Files ``S3_BASE/<prefix><name>`` with their pinned digests."""
    return tuple(
        RemoteFile(name, f"{S3_BASE}/{prefix}{name}", digest)
        for name, digest in names.items()
    )


_MMBERT_BLOBS = "app/models/model/models--jhu-clsp--mmBERT-small/blobs/"

BASE_MODELS: dict[str, BaseModel] = {
    "roberta-base": BaseModel(
        key="roberta-base",
        title="RoBERTa base (Liu et al., 2019)",
        params_millions=125,
        license="MIT (fairseq release)",
        provenance="Hugging Face legacy S3 bucket, 2019 upload",
        files=_s3(
            "roberta-base-",
            {
                "config.json": "ef0185e2aae6e06c5f105a285006952c"
                "340e20c7dbf43c86ec82601b13fc45e9",
                "vocab.json": "9e7f63c2d15d666b52e21d250d2e513b"
                "87c9b713cfa6987a82ed89e5e6e50655",
                "merges.txt": "1ce1664773c50f3e0cc8842619a93edc"
                "4624525b728b188a9e0be33b7726adc5",
                "tokenizer.json": (
                    "847bbeab6174d66a88898f729d52fa8d"
                    "355fafe1bea101cf960dd404581df70e"
                ),
                "pytorch_model.bin": "278b7a95739c4392fae9b818bb5343dd"
                "e20be1b89318f37a6d939e1e1b9e461b",
            },
        ),
    ),
    "electra-small": BaseModel(
        key="electra-small",
        title="ELECTRA small discriminator (Clark et al., 2020)",
        params_millions=14,
        license="Apache-2.0",
        provenance="Hugging Face legacy S3 bucket, 2020 upload",
        files=_s3(
            "google/electra-small-discriminator/",
            {
                "config.json": "b93f1ddda33ec110bb655e03f68f0241"
                "7bb75b48ad209b0368486d85c941f257",
                "vocab.txt": "07eced375cec144d27c900241f3e3394"
                "78dec958f92fddbc551f295c992038a3",
                "tokenizer.json": (
                    "ce64fce797c24f68df90b40a3f74f579"
                    "b336a493db14bd583fd520ea0d8c9a98"
                ),
                "pytorch_model.bin": "735154b1a8a78ba97da3c8c53c2e3d1d"
                "58b8dec5fa5caf0e144661c8bbe0e787",
            },
        ),
    ),
    "bge-small": BaseModel(
        key="bge-small",
        title="BGE small en v1.5 sentence embeddings (BAAI, 2023)",
        params_millions=33,
        license="MIT",
        provenance="Docker Hub layer of a third-party inference image",
        layer=DockerLayer(
            digest="6b0719da2b99ca58dfb0b8ec758253b2"
            "1921d982496a6dad42d942a1f63b0287",
            size=79_218_585,
            members=tuple(
                LayerMember(name, f"app/models/model/{name}", digest)
                for name, digest in (
                    (
                        "config.json",
                        "67653e9737f9ab713f139ef760a0506e"
                        "042c22f0616c7fc0f1dd29d26eb37ad2",
                    ),
                    (
                        "model.safetensors",
                        "ea1d11a3f23d14fe09fc1826fc7944e8"
                        "9c09a634d2217d57a21dd136805ee3e8",
                    ),
                    (
                        "tokenizer.json",
                        "d241a60d5e8f04cc1b2b3e9ef7a4921b"
                        "27bf526d9f6050ab90f9267a1f9e5c66",
                    ),
                    (
                        "tokenizer_config.json",
                        "7b082b48a08a0b5c5939d99472e33add"
                        "ce3d96202155e33f7928cecd171186a0",
                    ),
                    (
                        "special_tokens_map.json",
                        "5d5b662e421ea9fac075174bb0688ee0"
                        "d9431699900b90662acd44b2a350503a",
                    ),
                    (
                        "vocab.txt",
                        "07eced375cec144d27c900241f3e3394"
                        "78dec958f92fddbc551f295c992038a3",
                    ),
                )
            ),
        ),
    ),
    "mmbert-small": BaseModel(
        key="mmbert-small",
        title="mmBERT small, ModernBERT architecture (JHU CLSP, 2025)",
        params_millions=140,
        license="MIT (model card; not re-verified in-session)",
        provenance="Docker Hub layer of a third-party inference image; "
        "weights match the content hashes Hugging Face assigned",
        layer=DockerLayer(
            digest="064b4b77c94b674c5a86b2e704a78acd"
            "31f0858ab2856ab6e997d1981e3ec45c",
            size=1_048_363_696,
            members=(
                LayerMember(
                    "config.json",
                    _MMBERT_BLOBS + "f17138090dfd6455f8d54bbc4026dc2a85b51fa6",
                    "3b0eea47c4734682370b66dbd73d655f"
                    "1100387f683d7c69015f33aa1bac1be9",
                ),
                LayerMember(
                    "model.safetensors",
                    _MMBERT_BLOBS + "e329b134965c970aef086941b8770d81"
                    "e877933bac96a730d1f57a18aa8b141a",
                    "e329b134965c970aef086941b8770d81"
                    "e877933bac96a730d1f57a18aa8b141a",
                ),
                LayerMember(
                    "tokenizer.json",
                    _MMBERT_BLOBS + "197d4cc5406ee12cc50c8b5511f2393c"
                    "c32d9db321545979ce041c1199178356",
                    "197d4cc5406ee12cc50c8b5511f2393c"
                    "c32d9db321545979ce041c1199178356",
                ),
                LayerMember(
                    "tokenizer_config.json",
                    _MMBERT_BLOBS + "9f755196cd8890b3d1bf9c1a9db9e4151456c19f",
                    "ea349495764b24570b4560fe2e4a557e"
                    "5c0a47e6bce782c2409b1d6445cf8633",
                ),
                LayerMember(
                    "special_tokens_map.json",
                    _MMBERT_BLOBS + "8d6368f7e735fbe4781bf6e956b7c6ad0586df80",
                    "baec30ea10906f16adb8c18af7a34023"
                    "002c1746542612b8b41c9f09e1351351",
                ),
            ),
        ),
        notes=(
            "Multilingual; the only ModernBERT-architecture encoder "
            "reachable without huggingface.co.",
        ),
    ),
}


def _docker_client() -> httpx.Client:
    """Client carrying an anonymous pull token for the image repository.

    httpx drops the ``Authorization`` header when the registry redirects the
    blob request to its CDN on another host.
    """
    response = httpx.get(DOCKER_AUTH, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    token = response.json()["token"]
    return httpx.Client(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"Authorization": f"Bearer {token}"},
    )


def extract_members(
    archive: Path, members: tuple[LayerMember, ...], target: Path
) -> None:
    """Copy named regular files out of a ``tar.gz`` and verify each one.

    Only the listed paths are read; links, devices and every other member are
    ignored, so a hostile archive cannot write outside ``target``.

    Raises:
        KeyError: If a listed member is missing or is not a regular file.
        ChecksumMismatchError: If a member's bytes do not match its pin.
    """
    wanted = {m.path: m for m in members}
    found: set[str] = set()
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode="r:gz") as tar:
        for info in tar:
            member = wanted.get(info.name)
            if member is None or not info.isfile():
                continue
            source = tar.extractfile(info)
            if source is None:
                continue
            out = target / Path(member.name).name
            with source, out.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            if sha256_file(out) != member.sha256:
                out.unlink()
                raise ChecksumMismatchError(f"{member.path} in {archive.name}")
            found.add(info.name)
    missing = sorted(set(wanted) - found)
    if missing:
        raise KeyError(f"{archive.name} lacks regular files {missing}")


def convert_pickle_to_safetensors(model_dir: Path) -> Path:
    """Re-save ``pytorch_model.bin`` as ``model.safetensors`` and delete it.

    ``torch.load(weights_only=True)`` refuses anything but tensors and plain
    containers, so a tampered pickle cannot execute code.
    """
    import torch
    from safetensors.torch import save_file

    source = model_dir / PICKLE_WEIGHTS
    state = torch.load(source, map_location="cpu", weights_only=True)
    tensors = {k: v.contiguous().clone() for k, v in state.items()}
    target = model_dir / SAFE_WEIGHTS
    save_file(tensors, str(target), metadata={"format": "pt"})
    source.unlink()
    return target


def _write_provenance(model: BaseModel, target: Path) -> None:
    record: dict[str, Any] = {
        "key": model.key,
        "title": model.title,
        "license": model.license,
        "provenance": model.provenance,
        "files": {f.name: f.sha256 for f in model.files},
    }
    if model.layer is not None:
        record["layer_digest"] = f"sha256:{model.layer.digest}"
        record["files"] = {m.name: m.sha256 for m in model.layer.members}
    (target / "PROVENANCE.json").write_text(json.dumps(record, indent=2))


def fetch_base(model: BaseModel, root: Path) -> Path:
    """Download, verify and convert one base model into ``root/<key>``.

    Idempotent: a directory with ``PROVENANCE.json`` is returned as is.

    Returns:
        The model directory, loadable with ``from_pretrained``.
    """
    target = root / model.key
    if (target / "PROVENANCE.json").is_file():
        logger.info("Base model %s already present", model.key)
        return target
    target.mkdir(parents=True, exist_ok=True)
    for remote in model.files:
        download_file(remote.url, target / remote.name, remote.sha256)
    if model.layer is not None:
        archive = root / "_layers" / f"{model.layer.digest}.tar.gz"
        with _docker_client() as client:
            download_file(
                DOCKER_BLOB + f"sha256:{model.layer.digest}",
                archive,
                model.layer.digest,
                client=client,
            )
        extract_members(archive, model.layer.members, target)
        archive.unlink()
    if (target / PICKLE_WEIGHTS).is_file():
        convert_pickle_to_safetensors(target)
    _write_provenance(model, target)
    return target
