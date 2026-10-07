"""The shared public archive: list, pull (verified and cached) and contribute."""
from __future__ import annotations

import os
from pathlib import Path
import re

import brisk_archive as cli
from brisk._recording import Recording, Table


def cache_dir() -> Path:
    return Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'brisk'


class Archive:
    """Recordings published in the Tokyo archive. Reading needs no AWS account.

        archive = Archive()
        rows = archive.recordings(source="historical_mock")
        rec = archive.pull(rows[0]["prefix"])
    """

    def __init__(self, config=None, s3=None):
        self.config = cli.settings(Path(config) if config else None)
        self._s3 = s3

    @property
    def s3(self):
        if self._s3 is None:
            self._s3 = cli.client(self.config)
        return self._s3

    def recordings(self, date=None, source=None) -> Table:
        """Published recordings, optionally filtered by YYYYMMDD trading date and source."""
        rows = []
        for prefix, m in cli.manifests(self.s3, self.config['bucket'], date):
            s = m['summary']
            if source is None or s['source'] == source:
                rows.append({'prefix': prefix, 'source': s['source'], 'trading_date': s['trading_date'],
                             'securities': len(s['codes']), 'batches': s['batches'],
                             'quote_updates': s['quote_updates'], 'bytes': m['bytes'],
                             'contributor': m['contributor'], 'license': m['license'], 'sha256': m['sha256']})
        return Table(rows)

    def pull(self, prefix, output=None) -> Recording:
        """Download, verify and decode one recording. Without `output`, downloads are cached."""
        if not re.fullmatch(r'archive/\d{8}/[0-9a-f]{64}', prefix):
            raise ValueError('Invalid archive prefix')
        target = Path(output) if output else cache_dir() / prefix
        # Cache entries only appear after verification (pull moves them atomically).
        if output or not target.exists():
            cli.pull(self.s3, self.config['bucket'], prefix, target)
        return Recording(target)

    def contribute(self, directory, timeout=660) -> dict:
        """Upload a prepared package and wait for automatic validation and publication."""
        return cli.contribute(Path(directory), self.config['api_url'], timeout)
