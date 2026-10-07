# Contributing

Software contributions: open a pull request with the behavior change, relevant
verification and updated docs. Keep vendor assets, captures, account/session
material, build products and cloud credentials out of Git. Do not commit AWS
upload tickets; they are short-lived bearer credentials.

Recording contributions are automatic through `brisk_archive.py`. No maintainer
approval is required. The automatic service checks the schema, integrity,
continuity and contributor declaration, then publishes accepted recordings.
Choose a public alias and a data license you may grant. Do not include account,
authentication or order information. The accepted decoded schema is documented
in `archive_schema.py`; it contains market/auction data and local timing only.

Data quality metadata includes source, JST trading date and source-time range,
security coverage, batches, quote count and expanded size. The SHA-256 refers
to the gzip object. Packaging uses deterministic gzip headers. Distinct receipt
clocks remain distinct recordings; identical payloads deduplicate by hash.

Ordinary tests are local. `BRISK_MOCK_CACHE` opts into fixture replay. Optional
v2 tests need the separately installed pinned runtime and native extension,
as described in `tools/brisk_mock/NAUTILUS_V2.md`. Aim for at least 85% coverage
of new behavior. Real cloud smoke tests require an operational configured API
and publish explicitly synthetic data.
