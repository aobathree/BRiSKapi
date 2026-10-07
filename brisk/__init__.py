"""Python API for BRiSK auction data: recordings, the shared archive and the public demo.

    import brisk

    feed = brisk.connect(web=True)          # live: state updates as frames arrive
    brisk.Ticker("7203").quote()            # current auction quote
    for q in feed.quotes("7203"): ...       # or feed.on_quote(callback)

    brisk.pull(prefix)                      # or brisk.load(path) / brisk.record(...)
    brisk.Ticker("7203").quote(at="08:59:59")
    brisk.Market().imbalances(top=10).to_pandas()
"""
from __future__ import annotations

import brisk_archive as _cli
from brisk._archive import Archive
from brisk._live import Feed, connect as _connect, record as _record, stream
from brisk._market import Market, Ticker
from brisk._recording import JST, BriskError, NotFoundError, Recording, Table

__version__ = '0.2.0'
__all__ = ['JST', 'Archive', 'BriskError', 'Feed', 'Market', 'NotFoundError', 'Recording', 'Table', 'Ticker',
           'connect', 'consent', 'current', 'load', 'pull', 'record', 'recordings', 'stream']

_current: Recording | Feed | None = None


def load(source) -> Recording | Feed:
    """Use a live Feed or a recording (path, directory or Recording) as the default for Ticker and Market."""
    global _current
    _current = source if isinstance(source, (Recording, Feed)) else Recording(source)
    return _current


def current() -> Recording | Feed:
    if _current is None:
        raise BriskError('Nothing loaded: use brisk.connect(...), brisk.load(path), brisk.pull(prefix) or brisk.record(...)')
    return _current


def connect(**options) -> Feed:
    """Start a live feed (see Feed) and make it the default for Ticker and Market."""
    return load(_connect(**options))


def recordings(date=None, source=None) -> Table:
    """Published archive recordings (see Archive.recordings)."""
    return Archive().recordings(date, source)


def pull(prefix, output=None) -> Recording:
    """Download and verify an archive recording, then make it the default."""
    return load(Archive().pull(prefix, output))


def record(output, **options) -> Recording:
    """Record the public demo (see brisk._live.record), contribute per consent and make it the default."""
    return load(_record(output, **options))


def consent(accept=False, revoke=False, contributor=None, license=None) -> dict | None:
    """Show (no arguments), accept or revoke automatic contribution. See PRIVACY.md."""
    if accept and revoke:
        raise ValueError('Choose accept or revoke')
    if accept:
        return _cli.save_consent(True, contributor, license)
    return _cli.save_consent(False) if revoke else _cli.load_consent()
