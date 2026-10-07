"""Opt-in real automatic-publication/anonymous-download smoke test (synthetic data)."""
import json
from pathlib import Path
import tempfile
import uuid
import urllib.request

from brisk_archive import package, contribute, client, manifests, pull, settings

def main():
    config=settings()
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        quote=dict(issue_id=0,code='0000',frame=1,source_time_us=100,indicative_price10=123)
        batches=[dict(type='bootstrap',seq=0,source='synthetic_test',trading_date='20210927',
            source_time_us=100,market_issue_count=1,master=[dict(issue_id=0,code='0000')],quotes=[quote]),
            dict(type='end',seq=1,source_time_us=100)]
        events=root/'events.jsonl'
        events.write_text(''.join(json.dumps(b)+'\n' for b in batches))
        m=package(events,root/'package','archive-smoke-test','CC0-1.0')
        result=contribute(root/'package',config['api_url'])
        s3=client(config)
        found=[(p,v) for p,v in manifests(s3,config['bucket'],'20210927') if v['sha256']==m['sha256']]
        assert found
        pull(s3,config['bucket'],result['prefix'],root/'download')
        assert (root/'download/events.jsonl').read_bytes()==events.read_bytes()
        # Public listing and writing to staging must remain forbidden without a ticket.
        from botocore.exceptions import ClientError
        for method,kwargs in [(s3.list_objects_v2,dict(Prefix='incoming/')),
                              (s3.put_object,dict(Key='incoming/forbidden',Body=b'no'))]:
            try:
                method(Bucket=config['bucket'],**kwargs)
            except ClientError as e:
                assert e.response['Error']['Code']=='AccessDenied'
            else:
                raise AssertionError('Anonymous staging access was allowed')
        print(json.dumps({'automatic_publication':'passed','anonymous_verified_pull':'passed',
                          'staging_private':'passed','region':config['region'],'prefix':result['prefix']}))

if __name__=='__main__':
    main()
