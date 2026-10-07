"""Tokyo serverless crowd archive: ticket API and automatic S3 ingest."""
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import parse_qs
import uuid
import zlib

import boto3
from botocore.exceptions import ClientError
from briskapi.schema import encode, repack, validate_manifest, validate_timing, require, MAX_COMPRESSED, MAX_TIMING_BYTES

BUCKET = os.environ.get('ARCHIVE_BUCKET', '')
TABLE = os.environ.get('QUOTA_TABLE', '')
# Per-deployment key: stored quota IDs cannot be reversed to IP addresses.
QUOTA_KEY = os.environ.get('QUOTA_SALT', '').encode()
FINAL = {'published', 'rejected'}
# Malformed input of any kind: corrupt deflate data and deeply nested JSON included.
INVALID = (ValueError, KeyError, TypeError, EOFError, OSError, zlib.error, RecursionError)

def clients():
    from botocore.config import Config
    region = os.environ.get('AWS_REGION', 'ap-northeast-1')
    return boto3.client('s3', region_name=region, endpoint_url=f'https://s3.{region}.amazonaws.com',
                        config=Config(signature_version='s3v4')), boto3.client('dynamodb', region_name=region)

def json_put(s3, key, data, immutable=False):
    args = dict(Bucket=BUCKET, Key=key, Body=json.dumps(data, sort_keys=True).encode(), ContentType='application/json')
    if immutable:
        args['IfNoneMatch'] = '*'
    try:
        s3.put_object(**args)
    except ClientError as e:
        if not immutable or e.response['Error']['Code'] not in {'PreconditionFailed', 'ConditionalRequestConflict'}:
            raise

def json_get(s3, key):
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    with obj['Body'] as body:
        raw = body.read(65537)
    require(len(raw) <= 65536, 'Metadata too large')
    return json.loads(raw)

def discard(s3, key, version):
    """Permanently remove one staging object version; already-removed versions are fine."""
    try:
        s3.delete_object(Bucket=BUCKET, Key=key, VersionId=version)
    except ClientError as e:
        if e.response['Error']['Code'] not in {'NoSuchKey', 'NoSuchVersion'}:
            raise

def quota(db, scope, amount, limit, window):
    now = int(time.time())
    try:
        db.update_item(TableName=TABLE, Key={'id': {'S': f'{scope}:{now // window}'}},
            UpdateExpression='SET expires = :expires ADD used :amount',
            ConditionExpression='attribute_not_exists(used) OR used <= :remaining',
            ExpressionAttributeValues={':expires': {'N': str(now + window * 2)},
                ':amount': {'N': str(amount)}, ':remaining': {'N': str(limit - amount)}})
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise ValueError('Contribution quota reached; retry later') from e
        raise

def response(status, value):
    return {'statusCode': status, 'headers': {'content-type': 'application/json', 'cache-control': 'no-store'},
            'body': json.dumps(value)}

def api(event, s3, db):
    method = event['requestContext']['http']['method']
    if method == 'GET':
        ticket = parse_qs(event.get('rawQueryString', '')).get('ticket', [''])[0]
        require(str(uuid.UUID(ticket)) == ticket, 'Invalid ticket')
        try:
            return response(200, json_get(s3, f'incoming/{ticket}/status.json'))
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                return response(404, {'error': 'Ticket not found'})
            raise
    require(method == 'POST', 'Use POST to request an upload ticket')
    raw = event.get('body', '')
    if event.get('isBase64Encoded'):
        raw = base64.b64decode(raw, validate=True).decode()
    require(len(raw) <= 65536, 'Manifest too large')
    body = json.loads(raw)
    require(QUOTA_KEY, 'Contribution service is not configured')
    ip = 'ip-' + hmac.new(QUOTA_KEY, event['requestContext']['http'].get('sourceIp', 'unknown').encode(), hashlib.sha256).hexdigest()
    if isinstance(body, dict) and set(body) == {'timing'}:
        return timing(body['timing'], ip, s3, db)
    # Validate the complete manifest before retaining any contributor metadata.
    m = validate_manifest(body)
    require(len(json.dumps(m['summary'])) < 60000, 'Summary too large')
    quota(db, ip, 1, 4, 3600)
    quota(db, 'global-tickets', 1, 64, 3600)
    quota(db, 'global-bytes', m['bytes'], 5 * 1024**3, 86400)
    ticket = str(uuid.uuid4())
    json_put(s3, f'incoming/{ticket}/manifest.json', m)
    json_put(s3, f'incoming/{ticket}/status.json', {'status': 'awaiting_upload', 'ticket': ticket})
    fields = {'Content-Type': 'application/gzip', 'x-amz-server-side-encryption': 'AES256'}
    post = s3.generate_presigned_post(Bucket=BUCKET, Key=f'incoming/{ticket}/events.jsonl.gz',
        Fields=fields, Conditions=[{'Content-Type': 'application/gzip'},
        {'x-amz-server-side-encryption': 'AES256'}, ['content-length-range', m['bytes'], m['bytes']]], ExpiresIn=900)
    return response(201, {'ticket': ticket, 'upload': post, 'expires_seconds': 900})

def timing(report, ip, s3, db):
    """Publish a timing-only report: validated, re-serialized by the service, content addressed."""
    report = validate_timing(report, today=dt.datetime.now(dt.timezone.utc).date())
    body = encode(report)
    require(len(body) <= MAX_TIMING_BYTES, 'Timing report too large')
    quota(db, 'timing-' + ip, 1, 6, 3600)
    quota(db, 'global-timing', 1, 600, 3600)
    key = f"timing/{report['trading_date']}/{hashlib.sha256(body).hexdigest()}.json"
    try:
        s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType='application/json', IfNoneMatch='*')
    except ClientError as e:
        if e.response['Error']['Code'] not in {'PreconditionFailed', 'ConditionalRequestConflict'}:
            raise
    return response(201, {'status': 'published', 'key': key})

def ingest(record, s3, db):
    from urllib.parse import unquote_plus
    require(record['s3']['bucket']['name'] == BUCKET, 'Unexpected bucket')
    obj = record['s3']['object']
    key = unquote_plus(obj['key'])
    parts = key.split('/')
    require(len(parts) == 3 and parts[0] == 'incoming' and parts[2] == 'events.jsonl.gz', 'Unexpected key')
    ticket = str(uuid.UUID(parts[1]))
    require(ticket == parts[1], 'Invalid ticket')
    version = obj.get('versionId')
    # Require a version so validation and publication use the same immutable input.
    require(version, 'Versioned upload required')
    try:
        db.update_item(TableName=TABLE, Key={'id': {'S': 'ticket-' + ticket}},
            UpdateExpression='SET version = :version, expires = :expires',
            ConditionExpression='attribute_not_exists(version) OR version = :version',
            ExpressionAttributeValues={':version': {'S': version}, ':expires': {'N': str(int(time.time()) + 172800)}})
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            # A ticket admits only its first object version. Later POSTs with the
            # same upload form never become data and are not kept in staging.
            discard(s3, key, version)
            return
        raise
    if json_get(s3, f'incoming/{ticket}/status.json').get('status') in FINAL:
        discard(s3, key, version)  # Redelivered event for a finished ticket.
        return
    try:
        m = json_get(s3, f'incoming/{ticket}/manifest.json')
        validate_manifest(m)
        with tempfile.TemporaryDirectory() as tmp:
            path, output = Path(tmp) / 'upload.gz', Path(tmp) / 'events.jsonl.gz'
            data = s3.get_object(Bucket=BUCKET, Key=key, VersionId=version)
            require(data['ContentLength'] == m['bytes'] <= MAX_COMPRESSED, 'Invalid upload size')
            with data['Body'] as body, path.open('wb') as target:
                total = 0
                while chunk := body.read(1024 * 1024):
                    total += len(chunk)
                    require(total <= MAX_COMPRESSED, 'Upload too large')
                    target.write(chunk)
            # Publish the service's own compression of validated canonical lines,
            # never the uploaded bytes.
            published = repack(path, m, output)
            prefix = f"archive/{published['summary']['trading_date']}/{published['sha256']}"
            with output.open('rb') as body:
                try:
                    s3.put_object(Bucket=BUCKET, Key=f'{prefix}/events.jsonl.gz', Body=body,
                                  ContentType='application/gzip', IfNoneMatch='*')
                except ClientError as e:
                    if e.response['Error']['Code'] not in {'PreconditionFailed', 'ConditionalRequestConflict'}:
                        raise
            # Manifest is the commit marker. Readers list only committed datasets.
            json_put(s3, f'{prefix}/manifest.json', published, immutable=True)
            json_put(s3, f'incoming/{ticket}/status.json', {'status': 'published', 'prefix': prefix, 'sha256': published['sha256']})
    except INVALID as e:
        # No raw payload or potentially sensitive contents in public status/logs.
        json_put(s3, f'incoming/{ticket}/status.json', {'status': 'rejected', 'reason': type(e).__name__})
    discard(s3, key, version)

def handler(event, context):
    s3, db = clients()
    if 'Records' in event:
        for record in event['Records']:
            ingest(record, s3, db)
        return {'ok': True}
    try:
        return api(event, s3, db)
    except INVALID as e:
        return response(400, {'error': str(e)[:160]})
