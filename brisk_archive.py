#!/usr/bin/env python3
"""Record, automatically contribute, discover and verify shared BRiSK streams."""
import argparse
import gzip
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from archive_schema import SCHEMA, digest, inspect_package, validate_manifest, validate_stream, require

ROOT = Path(__file__).resolve().parent

def settings():
    return json.loads((ROOT / 'archive.json').read_text())

def package(events, output, contributor, license):
    with events.open('rb') as stream:
        summary = validate_stream(stream)
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'events.jsonl.gz'
    require(not target.exists(), 'Package output already exists')
    with events.open('rb') as source, target.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed:
            shutil.copyfileobj(source, compressed)
    m = dict(schema=SCHEMA, sha256=digest(target), bytes=target.stat().st_size,
             summary=summary, contributor=contributor, license=license, redistribution_permitted=True)
    inspect_package(target, m)
    (output / 'manifest.json').write_text(json.dumps(m, indent=2) + '\n')
    return m

def request_json(url, data=None):
    request = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)

def contribute(directory, api_url, timeout=660):
    m = json.loads((directory / 'manifest.json').read_text())
    path = directory / 'events.jsonl.gz'
    inspect_package(path, m)
    ticket = request_json(api_url, m)
    # S3 browser POST policies bind key, encryption, content type and exact size.
    # The archive's 64 MiB cap bounds the multipart body below 65 MiB.
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in ticket['upload']['fields'].items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="events.jsonl.gz"\r\nContent-Type: application/gzip\r\n\r\n'.encode())
    parts.extend([path.read_bytes(), f'\r\n--{boundary}--\r\n'.encode()])
    request = urllib.request.Request(ticket['upload']['url'], data=b''.join(parts),
             headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}, method='POST')
    with urllib.request.urlopen(request, timeout=120) as response:
        response.read()
    status_url = api_url.rstrip('/') + '/?ticket=' + ticket['ticket']
    print(json.dumps({'ticket': ticket['ticket'], 'status_url': status_url}), flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = request_json(status_url)
        if status['status'] == 'published':
            return status
        require(status['status'] != 'rejected', 'Automatic validation rejected recording: ' + status.get('reason', 'unknown'))
        time.sleep(3)
    raise TimeoutError(f'Publication still pending; check {status_url}')

def client(config):
    return boto3.client('s3', region_name=config['region'], config=Config(signature_version=UNSIGNED))

def manifests(s3, bucket, date=None):
    prefix = 'archive/'
    if date:
        require(len(date) == 8 and date.isdigit(), 'Date must be YYYYMMDD')
        prefix += date + '/'
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get('Contents', []):
            if item['Key'].endswith('/manifest.json'):
                body = s3.get_object(Bucket=bucket, Key=item['Key'])['Body']
                with body:
                    m = json.loads(body.read(65537))
                validate_manifest(m)
                yield item['Key'].rsplit('/', 1)[0], m

def pull(s3, bucket, prefix, output):
    import re
    require(re.fullmatch(r'archive/\d{8}/[0-9a-f]{64}', prefix), 'Invalid archive prefix')
    require(not output.exists(), 'Download output already exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        directory = Path(tmp)
        for name in ('manifest.json', 'events.jsonl.gz'):
            obj = s3.get_object(Bucket=bucket, Key=f'{prefix}/{name}')
            limit = 65536 if name == 'manifest.json' else 64 * 1024**2
            require(obj['ContentLength'] <= limit, 'Remote object exceeds limit')
            with obj['Body'] as body, (directory / name).open('wb') as target:
                size = 0
                while chunk := body.read(1024 * 1024):
                    size += len(chunk)
                    require(size <= limit, 'Remote object exceeds limit')
                    target.write(chunk)
        m = json.loads((directory / 'manifest.json').read_text())
        inspect_package(directory / 'events.jsonl.gz', m)
        require(prefix == f"archive/{m['summary']['trading_date']}/{m['sha256']}", 'Archive identity mismatch')
        with gzip.open(directory / 'events.jsonl.gz', 'rb') as source, (directory / 'events.jsonl').open('wb') as target:
            shutil.copyfileobj(source, target)
        shutil.move(str(directory), str(output))
    return m

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'archive.json')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('record', 'package'):
        p = sub.add_parser(name)
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--contributor', required=True)
        p.add_argument('--license', choices=['CC0-1.0', 'CC-BY-4.0'], required=True)
        p.add_argument('--redistribution-permitted', action='store_true', required=True,
                       help='Declare permission to redistribute this recording under the selected data license')
        p.add_argument('--upload', action='store_true')
        if name == 'record':
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument('--web', action='store_true')
            group.add_argument('--cache', type=Path)
            p.add_argument('--codes')
            p.add_argument('--limit-frames', type=int)
            p.add_argument('--speed', type=float, default=1)
            p.add_argument('--binary', type=Path, default=ROOT / 'rust/brisk_quote_ingest/target/release/brisk_quote_ingest')
        else:
            p.add_argument('--events', type=Path, required=True)
    p = sub.add_parser('upload'); p.add_argument('directory', type=Path)
    p = sub.add_parser('list'); p.add_argument('--date'); p.add_argument('--source', choices=['historical_mock','synthetic_test'])
    p = sub.add_parser('pull'); p.add_argument('prefix'); p.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    if args.command in {'record', 'package'}:
        if args.command == 'record':
            with tempfile.TemporaryDirectory() as tmp:
                events = Path(tmp) / 'events.jsonl'
                cmd = [str(args.binary), '--events', str(events), '--latest', str(Path(tmp) / 'latest.json'), '--speed', str(args.speed)]
                cmd += ['--web'] if args.web else ['--cache', str(args.cache)]
                if args.codes:
                    cmd += ['--codes', args.codes]
                if args.limit_frames:
                    cmd += ['--limit-frames', str(args.limit_frames)]
                subprocess.run(cmd, check=True)
                m = package(events, args.output, args.contributor, args.license)
        else:
            m = package(args.events, args.output, args.contributor, args.license)
        print(json.dumps(m))
        if args.upload:
            print(json.dumps(contribute(args.output, config['api_url'])))
    elif args.command == 'upload':
        print(json.dumps(contribute(args.directory, config['api_url'])))
    elif args.command == 'list':
        for prefix, m in manifests(client(config), config['bucket'], args.date):
            if args.source is None or m['summary']['source'] == args.source:
                print(json.dumps({'prefix': prefix, **m}))
    elif args.command == 'pull':
        print(json.dumps(pull(client(config), config['bucket'], args.prefix, args.output)))

if __name__ == '__main__':
    main()
