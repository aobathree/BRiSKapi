"""Build the optional in-process Rust state for Python 3.12+ Nautilus v2."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, default=Path("/tmp/brisk-native-target"))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYO3_PYTHON=sys.executable)
    command = ["cargo", "build", "--locked", "--release", "--lib", "--features", "python",
               "--manifest-path", str(root / "rust/brisk_quote_ingest/Cargo.toml"),
               "--target-dir", str(args.target_dir.resolve())]
    if args.offline:
        command.append("--offline")
    subprocess.run(command, env=env, check=True)
    if sys.platform == "darwin":
        filename = "libbrisk_quote_ingest.dylib"
    elif sys.platform == "win32":
        filename = "brisk_quote_ingest.dll"
    else:
        filename = "libbrisk_quote_ingest.so"
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / ("brisk_state_native" + sysconfig.get_config_var("EXT_SUFFIX"))
    shutil.copy2(args.target_dir / "release" / filename, destination)
    print(destination.resolve())


if __name__ == "__main__":
    main()
