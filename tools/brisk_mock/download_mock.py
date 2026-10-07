"""Download pinned public demo assets; never vendor broker code or recordings."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


def download(cache: Path) -> None:
    manifest = json.loads((Path(__file__).resolve().parents[2] / "briskapi/decoder/assets.json").read_text())
    cache.mkdir(parents=True, exist_ok=True)
    for name, asset in manifest["assets"].items():
        dest = cache / name
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == asset["sha256"]:
            continue
        with urlopen(asset["url"], timeout=60) as response:
            data = response.read()
        # The demo sometimes serves gzip bytes even without Accept-Encoding.
        if data.startswith(b"\x1f\x8b"):
            data = gzip.decompress(data)
        if hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError(f"Public demo asset changed: {name}; re-audit before changing the pin")
        temp = dest.with_suffix(dest.suffix + ".tmp")
        temp.write_bytes(data)
        temp.replace(dest)
        print(f"{name}: {len(data):,} bytes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, type=Path)
    download(parser.parse_args().cache.expanduser())
