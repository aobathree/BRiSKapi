"""Bounded, streaming validation shared by local packaging and automatic ingest."""
import datetime as dt
import gzip
import hashlib
import json
import re

MAX_COMPRESSED = 64 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
MAX_LINE = 16 * 1024 * 1024
SCHEMA = 'brisk-decoded-jsonl-v1'
SOURCES = {'historical_mock', 'synthetic_test'}
QUOTE_KEYS = set('issue_id code frame source_time_us max_frame last_price10 open_price10 bid_price10 ask_price10 indicative_price10 indicative_volume indicative_side closing_indicative_price10 closing_indicative_volume quote_flag quote_side special_quote_time_us indicative_open_price10 auction_reference_price10 volume issue_status market_buy_quantity market_sell_quantity closing_market_buy_quantity closing_market_sell_quantity'.split())
BATCH_KEYS = set('type seq source_time_us source trading_date input_transport source_timestamp_origin exchange_delay_ms market_issue_count master quotes decode_ns received_unix_ms replay_lateness_ms frames quote_updates replay_wall_ms'.split())
MASTER_KEYS = set('issue_id code tick_type base_price10 limit_up10 limit_down10 lot_size issue_type market name'.split())

def require(condition, message):
    if not condition:
        raise ValueError(message)

def integer(value, maximum=2**53-1):
    require(type(value) is int and 0 <= value <= maximum, 'Invalid unsigned integer')
    return value

def code(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Z0-9]{4,8}', value), 'Invalid security code')
    return value

def validate_stream(stream):
    """Require bootstrap, continuous deltas and end; retain only per-issue clocks."""
    identities, clocks = {}, {}
    seq = last = count = expanded = 0
    first = source = date = None
    ended = False
    while True:
        line = stream.readline(MAX_LINE + 1)
        if not line:
            break
        expanded += len(line)
        require(len(line) <= MAX_LINE and expanded <= MAX_EXPANDED, 'Recording exceeds decode limit')
        require(not ended, 'Data after end batch')
        b = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        require(isinstance(b, dict) and not set(b) - BATCH_KEYS, 'Unknown batch fields')
        require(integer(b['seq']) == seq, 'Sequence gap')
        now = integer(b['source_time_us'], 86_400_000_000 - 1)
        require(now >= last, 'Market clock regression')
        kind = b['type']
        if seq == 0:
            require(kind == 'bootstrap' and b['source'] in SOURCES, 'Unsupported bootstrap/source')
            source, date, first = b['source'], b['trading_date'], now
            require(isinstance(date, str) and re.fullmatch(r'\d{8}', date), 'Invalid date')
            dt.datetime.strptime(date, '%Y%m%d')
            require(source != 'historical_mock' or date == '20210927', 'Unexpected mock date')
            master = b['master']
            require(isinstance(master, list) and 0 < len(master) <= 20000, 'Invalid master size')
            require(integer(b['market_issue_count']) >= len(master), 'Invalid market count')
            for m in master:
                require(isinstance(m, dict) and not set(m) - MASTER_KEYS, 'Unknown master fields')
                issue = integer(m['issue_id'], 2**32-1)
                require(issue not in identities, 'Duplicate master identity')
                identities[issue] = code(m['code'])
                for key, value in m.items():
                    if key == 'name':
                        require(isinstance(value, str) and len(value) <= 256, 'Invalid security name')
                    elif key != 'code':
                        integer(value)
            # No arbitrary account/session information is retained in the archive.
            transport = b.get('input_transport', {})
            require(isinstance(transport, dict) and set(transport) <= {'kind', 'origin', 'asset_fetch_ms'}, 'Invalid transport fields')
            require(transport.get('kind') in {None, 'https_recorded_assets', 'local_cache'}, 'Invalid transport kind')
            require(transport.get('origin') in {None, 'https://next-demo.brisk.jp', 'https://next-demo.brisk.jp/'}, 'Invalid transport origin')
            if 'asset_fetch_ms' in transport:
                require(type(transport['asset_fetch_ms']) in (int, float) and 0 <= transport['asset_fetch_ms'] < 1e9, 'Invalid fetch time')
            require(b.get('source_timestamp_origin') in {None, 'brisk_decoder_unverified'}, 'Invalid clock provenance')
            require(b.get('exchange_delay_ms') is None, 'Unverified exchange delay')
        else:
            require(kind in {'quotes', 'end'}, 'Expected delta/end')
            require(not set(b) & {'master', 'source', 'trading_date', 'input_transport'}, 'Repeated bootstrap metadata')
        quotes = b.get('quotes', [])
        require(isinstance(quotes, list) and len(quotes) <= len(identities), 'Invalid quote count')
        seen = set()
        for q in quotes:
            require(isinstance(q, dict) and not set(q) - QUOTE_KEYS, 'Unknown quote fields')
            issue = integer(q['issue_id'], 2**32-1)
            require(issue not in seen and identities.get(issue) == code(q['code']), 'Quote identity mismatch/duplicate')
            seen.add(issue)
            frame, clock = integer(q['frame'], 2**32-1), integer(q['source_time_us'], now)
            previous = clocks.get(issue, (0, 0))
            require(frame >= previous[0] and clock >= previous[1], 'Quote frame/time regression')
            clocks[issue] = (frame, clock)
            for key, value in q.items():
                if key != 'code':
                    integer(value)
        if seq == 0:
            require(seen == set(identities), 'Incomplete bootstrap')
        for key in ('decode_ns', 'received_unix_ms', 'frames', 'quote_updates'):
            if key in b:
                integer(b[key])
        for key in ('replay_lateness_ms', 'replay_wall_ms'):
            if key in b and b[key] is not None:
                require(type(b[key]) in (int, float) and 0 <= b[key] < 1e15, 'Invalid local timing')
        if kind == 'end':
            require(not quotes, 'End must not contain quotes')
            ended = True
        seq += 1
        last, count = now, count + len(quotes)
    require(ended, 'Recording has no clean end')
    return dict(source=source, trading_date=date, first_source_time_us=first,
                last_source_time_us=last, codes=sorted(identities.values()), batches=seq,
                quote_updates=count, expanded_bytes=expanded)

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def validate_manifest(m):
    fields = {'schema', 'sha256', 'bytes', 'summary', 'contributor', 'license', 'redistribution_permitted'}
    require(isinstance(m, dict) and set(m) == fields, 'Invalid manifest fields')
    require(m['schema'] == SCHEMA and re.fullmatch('[0-9a-f]{64}', m['sha256']), 'Invalid schema/hash')
    require(0 < integer(m['bytes'], MAX_COMPRESSED), 'Empty upload')
    require(isinstance(m['contributor'], str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', m['contributor']), 'Invalid contributor alias')
    require(m['license'] in {'CC0-1.0', 'CC-BY-4.0'} and m['redistribution_permitted'] is True, 'Redistribution declaration required')
    summary = m['summary']
    require(isinstance(summary, dict) and summary.get('source') in SOURCES, 'Invalid summary')
    return m

def inspect_package(path, manifest):
    validate_manifest(manifest)
    require(path.stat().st_size == manifest['bytes'] and digest(path) == manifest['sha256'], 'Size/hash mismatch')
    with gzip.open(path, 'rb') as stream:
        summary = validate_stream(stream)
    require(summary == manifest['summary'], 'Summary mismatch')
    return summary
