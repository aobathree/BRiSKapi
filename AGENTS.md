# Contributor automation guidance

Use Python in `.venv`; install `requirements-dev.txt` for local archive tests.
Do not commit downloaded vendor assets, market recordings, AWS credentials or
upload tickets. Public `archive.json` contains only bucket, region and API URL.

Run focused archive coverage (minimum 85%), Node tests, Rust tests/fmt/Clippy and,
when changing the bus integration, the actual v2 tests. CI supplies downloaded
demo fixtures. Keep README and schema documentation synchronized.

`infra/deploy.py` mutates AWS resources and is reserved for explicit deployment
work. `cloud_smoke.py` publishes self-authored synthetic data to the configured
archive. Unit tests must never publish data or mutate cloud resources.
