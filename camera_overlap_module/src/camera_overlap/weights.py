"""Strict local checkpoint loading; inference never downloads weights."""

import hashlib
from pathlib import Path

from .errors import ModelWeightsError

ASSETS = {
    "superpoint_v1.pth": "52b6708629640ca883673b5d5c097c4ddad37d8048b33f09c8ca0d69db12c40e",
    "superpoint_lightglue_v0-1_arxiv.pth": "6ff7040d0a497fc6639337946d7538dae07428c18f77a067a0b5a960e7cc551a",
}


def verify_weights(directory=None):
    directory = Path(directory) if directory is not None else Path(__file__).parent / "weights"
    paths = {}
    for name, expected in ASSETS.items():
        path = directory / name
        try:
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as error:
            raise ModelWeightsError(f"Cannot read local weights {path}: {error}") from error
        if actual != expected:
            raise ModelWeightsError(f"SHA-256 mismatch for {path}")
        paths[name] = path
    return paths
