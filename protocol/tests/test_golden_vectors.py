"""Consumes tests/vectors/*.json to pin frame encoding, SAS derivation and code
normalization. These vectors are the cross-language contract: the TypeScript
implementations added in Milestones 7-8 must reproduce the same output, checked by an
equivalent Vitest suite reading the same files (see docs/testing-strategy.md,
"Golden vectors").
"""

import json
from pathlib import Path
from typing import Any

from beam_protocol.codes import normalize_code
from beam_protocol.frames import Frame
from beam_protocol.sas import derive_sas

VECTORS_DIR = Path(__file__).resolve().parents[2] / "tests" / "vectors"


def _load(name: str) -> dict[str, Any]:
    return json.loads((VECTORS_DIR / name).read_text(encoding="utf-8"))


def test_vectors_directory_exists() -> None:
    assert VECTORS_DIR.is_dir(), VECTORS_DIR


def test_frame_vectors() -> None:
    data = _load("frames.json")
    assert data["cases"], "expected at least one frame vector"
    for case in data["cases"]:
        frame = Frame(
            file_index=case["file_index"],
            offset=case["offset"],
            payload=bytes.fromhex(case["payload_hex"]),
            last_of_block=case["last_of_block"],
        )
        assert frame.pack().hex() == case["packed_hex"], case


def test_sas_vectors() -> None:
    data = _load("sas.json")
    assert data["cases"], "expected at least one SAS vector"
    for case in data["cases"]:
        symbols = derive_sas(case["room_id"], case["fp_a"], case["fp_b"])
        actual = [{"emoji": s.emoji, "name": s.name} for s in symbols]
        assert actual == case["symbols"], case


def test_code_normalization_vectors() -> None:
    data = _load("codes.json")
    assert data["cases"], "expected at least one code vector"
    for case in data["cases"]:
        parsed = normalize_code(case["raw"])
        assert parsed.nameplate == case["nameplate"], case
        assert list(parsed.words) == case["words"], case
        assert str(parsed) == case["canonical"], case
