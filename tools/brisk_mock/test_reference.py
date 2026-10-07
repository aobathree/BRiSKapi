"""The committed archive reference must match the pinned demo replay exactly."""
import io
import json
import os
from pathlib import Path
import sys

import pytest

sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).parent)]
import briskapi.schema as schema  # noqa: E402
from build_reference import LineReader, replay  # noqa: E402

CACHE = os.environ.get("BRISK_MOCK_CACHE")
pytestmark = pytest.mark.skipif(not CACHE, reason="set BRISK_MOCK_CACHE for fixture replay")


def test_committed_reference_matches_replay():
    built = schema.build_reference(LineReader(replay(Path(CACHE))))
    assert built == json.loads((schema.REFERENCE_DIR / "historical_mock.json").read_text())


def test_genuine_subset_accepted_and_tampering_rejected():
    lines = list(replay(Path(CACHE), ["7203", "6758"]))
    summary = schema.validate_stream(io.BytesIO(b"".join(lines)))
    assert summary["codes"] == ["6758", "7203"] and summary["batches"] == 18002
    index = next(i for i, line in enumerate(lines) if i and b'"code":"7203"' in line)
    batch = json.loads(lines[index])
    batch["quotes"][0]["bid_price10"] += 10
    lines[index] = schema.encode(batch)
    with pytest.raises(ValueError, match="reference"):
        schema.validate_stream(io.BytesIO(b"".join(lines)))
