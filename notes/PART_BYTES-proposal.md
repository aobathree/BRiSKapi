# PART_BYTES: why parts close every few seconds, and two ways to change it (no code changed)

## What the 10/23 copy shows

- A part closes after 64 MiB of decoder JSON (`PART_BYTES`) or 30 s (`PART_SECONDS`), whichever comes first.
  At the open SBI sends ~10,000 updates/s at ~1.3 KB of JSON each, so the byte rule wins: a part every 4–11 s.
- Each such part holds ~50,000 depth rows and is ~1.7 MB of Parquet (ratio JSON:Parquet ≈ 38:1). 1,670 parts reached
  the 80-million-row piece boundary at 11:12. Without the retry storm the day would still have ~3,000 parts.
- Upstream's design notes (`ARCHITECTURE.md`, `_spool.py`) assume "a day's thousand parts" and "a part of 50,000 quote
  updates": the byte rule was sized for the part writer's speed (0.15 s per part), not for the day's part count.
  Packaging re-reads every part's footer for each range of issues, so its cost grows with the part count, and so does
  the length of any list of parts (the bug just fixed).

## Measured on the copy (fixed program, 6 threads, a 28-thread desktop while the production feed idled after the close)

| piece | parts | quote rows | packaging | of which reading parts for the quotes |
|---|---|---|---|---|
| 08:01–11:12 | 1,670 (~50 k rows each) | 80.0 M | 867 s | 784 s |
| 11:12–12:00 | 171 | 5.7 M | 22 s (+ ~8 min rebuilding the book from 1,670 parts) | 19 s |
| 12:00–14:30 | 15,864 (retry-storm parts, mostly tiny) | 36.1 M | 5,552 s | 4,288 s |

The afternoon held under half the morning's rows and took 6.4× longer: packaging cost follows the part count, not the
rows. The retry fix removes the storm's parts; the morning's 1,670 are the normal case this proposal is about.

## Option A: raise PART_BYTES, keep PART_SECONDS (recommended to try first)

Set `PART_BYTES` to 256 MiB (or 512 MiB). The time rule then closes most parts (every 30 s); the byte rule only trims
the opening burst (13 MB/s → a part every ~20 s at 256 MiB). Expected parts per day: ~900–1,100 instead of ~3,000.
- Crash loss is unchanged: at most `PART_SECONDS` = 30 s, as README promises.
- Cost: the open part's lines are held in memory, so up to 256 MiB more RAM in the feed process (512 MiB at 512 MiB),
  and `brisk-parquet part` parses a 4–8× larger input per part (it streams, so time per part rises, count falls).
- Gateway replays (`Spool.replay`) re-encode closed parts and then send the open part's lines: unchanged in behaviour.

## Option B: aim the limit at the Parquet size

A part's Parquet size cannot be measured before it is written, but the ratio is stable (~38:1 for SBI depths), so a
target of, say, 8 MB of Parquet per part ≈ 300 MiB of JSON. This is Option A with the constant explained in the terms
that matter for reading (row groups per part, footer size), and could be stated so in the comment: e.g. "PART_BYTES is
sized so a part holds about 250,000 quote updates (~8 MB of Parquet), a thousand-odd parts a day."

## Option C: time only, with a memory cap

Drop the byte rule to a safety cap (e.g. 1 GiB) and let `PART_SECONDS` alone close parts. Simplest to reason about
(exactly ~900 parts per trading day), but the opening burst would hold ~400 MB of JSON per part in memory; fine on a
desktop, less comfortable on an 8 GB Mac running the feed and a gateway.

## Not recommended: lowering PART_SECONDS to match

Shorter parts would make the count worse; the half-minute loss bound is already what users are told.

## Suggested measurement before changing anything

Replay the copied spool's first 1,670 parts through `brisk-parquet package` twice: as they are, and re-spooled with 4×
larger parts (concatenate 4 parts' batch/depth/trade tables via a small script, or re-record the demo with
`PART_BYTES` raised), and compare packaging time and peak memory. If the gain is under ~20 %, keep 64 MiB and only
rely on the retry fix (which already removes the 16,000 junk parts of a failed day).
