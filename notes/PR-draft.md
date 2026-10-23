# fix: a broker's day of thousands of spool parts is saved on Windows, and a save that fails no longer loses the day

## What happened

Two SBI days (2026-10-22 and 10-23) were received whole, 08:01 to 15:30 without a gap (135,349,740 updates on 10/23),
and nothing of either was saved.

At ten thousand updates a second a spool part closes after `PART_BYTES` (64 MiB of decoder output) every 4 to 11 seconds,
so a day has thousands of parts: 1,670 by 11:11, when the first piece reached `MAX_PIECE_ROWS`. `_native.package()` and
`_native.state()` passed every part's path as an argument of `brisk-parquet`. Windows' `CreateProcess` takes 32,767
characters of command line, about 675 such paths, so the program never started:
`[WinError 206] The filename or extension is too long`. macOS and Linux take megabytes, which is why e-shiten days
were fine.

The failure then compounded. The piece's parts counted as shipped although nothing had been packaged (`shipped.json`
= 1670), so every later rescue (`Spool.keep`) looked for a bootstrap in parts that had none; the feed retried at every
part written, every 0.7 s (22,295 times by 15:31), each retry closing another part (17,959 parts by the close); the
close's save failed the same way; and `_finish_segment`'s `finally: spool.remove()` deleted the spool.

## Changes

1. **The part list leaves the command line** (`rust/brisk_parquet/src/main.rs`, `parts.rs`, `briskapi/_native.py`).
   `brisk-parquet package` and `state` take `--parts-file FILE` (a path a line, UTF-8; `-` reads stdin) before the
   positional parts, which still work. briskapi sends the list on stdin, so the command line is the same for any
   number of parts. `_native.run` also reads the program's pipes as bytes and decodes UTF-8 (a text pipe used the
   console's code page, cp932 here, and could not decode a message with a character outside it).
2. **A save that fails does not lose the day** (`briskapi/_spool.py`, `briskapi/_live.py`).
   - A piece's parts count as shipped once packaged, refused (the program or the archive's rules found it wanting), or
     kept; one that could not be packaged here and now leaves its parts unshipped.
   - Such a piece is tried again from the same parts after `RETRY_SECONDS` (300 s), `MAX_ATTEMPTS` (3) times in the
     day, and once more at the day's end, not at every part written.
   - `Spool.keep` saves the piece last tried, or the day from its own bootstrap when the cut before the piece could not
     be made; it closes no part of a running day, cleans its staging folder when it fails, and counts the parts it kept
     as shipped.
   - `_finish_segment` removes the spool only when every failed piece has a `path`; otherwise it warns which folder is
     left, and the next feed to start finishes it (`Feed._recover`) like a crashed session's.
3. **A `Live` session connecting before the day begins** no longer kills its gateway thread when it asks `latest()`
   for imbalance records (`records._imbalance` without an equity table: no rows, null collars).

## Verification

- `pytest tests` with coverage, `cargo test`, `cargo fmt --check`, `cargo clippy -D warnings` (Windows; see the
  results in the PR checks on Linux).
- New tests: a 500-part segment and a 10,000-part list package and `state` whatever the platform's argument limit, and
  the command line is checked to stay under 1,000 characters (`tests/test_native.py`, `tests/test_spool.py`); the
  retry, refusal, bootstrap-fallback, open-part and staging rules of `Spool` (`tests/test_spool.py`); a day nothing of
  which could be saved leaves its spool and the next `brisk live` finishes it (`tests/test_brisk.py`); `latest()`
  before the day begins (`tests/test_gateway.py`); `listed()` in Rust.
- Real data: a copy of the 10/23 spool (17,705 parts, 21.45 GB) packaged with the fixed program into the morning's
  piece (parts 1–1670, the piece the recorder attempted at 11:12) and the rest through 14:30, both passing
  `brisk-parquet verify`. See the numbers in the PR comment.

Documentation: `ARCHITECTURE.md` (spool, pieces, rescue), `README.md` / `README.ja.md` (what a failed save keeps).
`pyproject.toml`'s version is unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
