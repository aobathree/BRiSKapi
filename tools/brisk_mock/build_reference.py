"""Fingerprint the pinned demo replay for archive validation.

The output holds only truncated SHA-256 chains, never market data. Regenerate it
only after re-auditing a changed asset pin in assets.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from archive_schema import REFERENCE_DIR, build_reference, canonical_lines  # noqa: E402


class LineReader:
    """Present an iterator of byte lines through the readline interface used by validation."""

    def __init__(self, lines):
        self.lines = iter(lines)

    def readline(self, size=-1):
        return next(self.lines, b"")


def replay(cache: Path, codes: list[str] | None = None):
    """Yield canonical archive lines from an unpaced replay of the cached demo."""
    command = ["node", str(ROOT / "tools/brisk_mock/decoder.cjs"), "--cache", str(cache), "--speed", "0"]
    if codes:
        command += ["--codes", ",".join(codes)]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
        yield from canonical_lines(process.stdout)
    if process.returncode:
        raise RuntimeError(f"Decoder exited with {process.returncode}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=REFERENCE_DIR / "historical_mock.json")
    args = parser.parse_args(argv)
    reference = build_reference(LineReader(replay(args.cache.expanduser())))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(reference, indent=1) + "\n")
    print(json.dumps({k: v for k, v in reference.items() if k != "issues"}))


if __name__ == "__main__":
    main()
