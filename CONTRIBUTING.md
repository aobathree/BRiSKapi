# Contributing

Software contributions: open a pull request with the behavior change, relevant
verification and updated docs. Keep vendor assets, captures, account/session
material, build products and cloud credentials out of Git. Do not commit AWS
upload tickets; they are short-lived bearer credentials.

Recording contributions are automatic once a user accepts the one-time prompt
(`brisk consent`); see `PRIVACY.md`. Any change to what is collected must update
that policy and bump `POLICY_VERSION` in `brisk_archive.py`, so saved choices lapse
and users are asked again. No maintainer approval is required for recordings: the
service checks canonical encoding, the schema, integrity, continuity, the
reference replay and the contributor declaration, then publishes its own
compression of accepted recordings. Do not include account, authentication or
order information. The accepted decoded schema is documented in
`archive_schema.py`; it contains market/auction data and local timing only.

Data quality metadata includes source, JST trading date and source-time range,
security coverage, batches, quote count and expanded size. The SHA-256 refers
to the published gzip object, which the service creates deterministically.
Distinct receipt clocks remain distinct recordings; identical payloads
deduplicate by hash. `references/historical_mock.json` pins the demo replay; it
changes only with a re-audited asset pin (`tools/brisk_mock/build_reference.py`).

Ordinary tests are local. `BRISK_MOCK_CACHE` opts into fixture replay. Optional
v2 tests need the separately installed pinned runtime and native extension,
as described in `tools/brisk_mock/NAUTILUS_V2.md`. Aim for at least 85% coverage
of new behavior. Real cloud smoke tests require an operational configured API
and publish explicitly synthetic data.
