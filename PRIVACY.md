# Privacy policy

Policy version 2, effective 7 October 2026. Applies to the `brisk` Python API and
CLI in this repository and the shared recording archive they contribute to.

## Automatic contribution

Sessions are contributed automatically. The first time you record, package or
open a live session from the command line, the tool shows a short notice and asks
once. Pressing Enter accepts, and the choice is saved for future runs. After that,
every clean and complete demo session (`brisk record`, `brisk live`, `briskapi.connect()`,
`briskapi.record()`) is uploaded and published without further prompts. A live
session is written to a temporary file while it runs; the file is deleted after
the upload. Python API calls never prompt.

The tool never uploads until you accept at that prompt, run `brisk consent --accept`,
or pass the explicit per-run declaration (`--contributor`, `--license` and
`--redistribution-permitted`). If the run is non-interactive (no terminal) and you
have not decided yet, the recording stays local and the tool tells you how to
decide. Partial replays (`--limit-frames`) and live sessions you close before
the end always stay local.

Opting out:

- decline at the first prompt;
- `brisk consent --revoke` (or `briskapi.consent(revoke=True)`) turns contribution off
  until you turn it back on with `brisk consent --accept`;
- `BRISK_CONTRIBUTE=0` disables uploads for any process with that environment;
- `--no-upload` skips one run.

## What a contribution contains

Each published recording consists of `events.jsonl.gz` and `manifest.json`:

- **Market data you recorded**: the BRiSK demo's securities master and decoded
  auction quotes. The archive checks that this is identical to the pinned
  reference replay, so it cannot contain anything else.
- **Local timing measurements**: per-frame decode time (`decode_ns`), your
  computer's wall-clock receipt time in milliseconds (`received_unix_ms`), replay
  lateness, total replay time and demo asset download time, plus whether the
  assets came from the website or a local cache. The receipt clock shows the date
  and time you made the recording. Timing values can loosely reflect your
  computer's speed and load.
- **Manifest**: your public alias, the data license you chose, your
  redistribution declaration, and a summary (source, trading date, securities
  covered, clock range, counts, sizes and SHA-256).

The default alias is random (`anon-` followed by eight hexadecimal characters) and
is the same for all your contributions until you change it. Choose a different
alias with `brisk consent --accept --contributor NAME`. Do not use your real name
or email address unless you want it public.

## What is never collected

The tool sends nothing about brokerage or BRiSK accounts, cookies, session tokens,
orders, usernames, hostnames, file paths, environment variables or hardware
identifiers. The archive service accepts only the fields listed above and rejects
any recording that contains other fields, so a modified client cannot add them.

## SBI BRiSK and Tachibana e-shiten BRiSK Next

The broker client (`briskapi.sbi`, `brisk live --sbi` / `--e-shiten`) uses session
cookies from your own browser. They are credentials:

- They are sent only to the site you logged in to (`https://sbi.brisk.jp` or
  `https://next.e-shiten.brisk.jp`), together with the API token it issues. The
  client never follows redirects, so they can't be forwarded elsewhere. The live
  stream itself goes to the BRiSK API host the site names (`api.brisk.jp`),
  authenticated by a session token the site issues for that purpose; cookies are
  not sent there, and any host outside `brisk.jp` is refused.
- They stay in memory unless you call `sbi.login(..., remember=True)`, which saves
  them to `~/.config/brisk/sbi-cookies.json` or `e-shiten-cookies.json` (or under
  `$XDG_CONFIG_HOME/brisk/`), readable only by you. `sbi.logout()` deletes those files.
- The live host receives them through its environment, not its command line.
- Broker market data (prices, quantities, codes, quotes, master data) never leaves
  your computer.
- With contribution on, a broker session contributes one **timing summary** when it
  ends (or when you close it after at least 100 frames): the p50/p90/p99/max of
  per-frame decode time, of the feed clock's age at local receipt and of the
  spacing between frames; the number of gaps over one second; the frame count;
  the trading date; the first and last minute (JST) of the session; the briskapi
  version; and your alias and license. It is published under `timing/` in the
  public archive, permanently. `contribute=False` on `sbi.connect()` or
  `BRISK_CONTRIBUTE=0` keeps it local.

## Network metadata

Contributing contacts an AWS Lambda function URL and Amazon S3 in Tokyo
(`ap-northeast-1`). These services receive your IP address with each request.

- The service uses your IP address only to rate limit upload tickets (four per
  hour). It stores a keyed hash (HMAC-SHA-256 with a secret per-deployment key),
  never the address itself. The hash is marked to expire after two hours, and
  DynamoDB deletes expired entries, usually within a few days.
- The service does not log IP addresses or recording contents. Its logs record
  infrastructure errors and are deleted after 14 days. S3 server access logging
  is not enabled.

`--web` recording downloads the demo's assets from `https://next-demo.brisk.jp/`.
That request goes to BRiSK's servers and is covered by their policies, not this
one. Reading the public archive (`list`, `pull`) sends requests to S3 but sends
nothing about you to the archive operator beyond the IP address that S3 sees.

## Storage, access and retention

- **Published recordings are public and permanent.** Anyone can list and download
  them without an account. They are content addressed and immutable, and you
  cannot delete them yourself. The operator may remove recordings on request.
- Uploads are validated in a private staging area and deleted immediately after
  validation, whether they are accepted or rejected. Ticket metadata (manifest and
  status) in staging expires after two days. Rejected uploads are never published.
- On your computer, the tool stores your choice, alias and license in
  `~/.config/brisk/contribution.json` (or `$XDG_CONFIG_HOME/brisk/`).
  `briskapi.pull()` caches verified downloads in `~/.cache/brisk/` (or
  `$XDG_CACHE_HOME/brisk/`). Delete these files at any time.

## Data licensing

Accepting contribution declares that you may redistribute your recordings under
the license you chose (CC0-1.0 by default, or CC-BY-4.0). The BRiSK demo's market
data and decoder belong to their respective owners. This project's MIT license
does not grant redistribution rights to vendor or exchange data. Revoke
contribution if you cannot make that declaration.

## Operator and changes

The archive is operated by the owner of
[honvl/briskapi](https://github.com/honvl/briskapi). AWS provides the
infrastructure. Send removal requests and privacy questions to the operator through
that GitHub account. Do not post personal data in a public issue.

If a future version collects anything beyond what is described here, the policy
version increases. The tool then asks again before contributing, and earlier
choices no longer apply.
