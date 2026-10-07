import copy
import gzip
import io
import json
from pathlib import Path
import uuid

import pytest
from botocore.exceptions import ClientError

import archive_schema as schema
import archive_service as service
import brisk_archive as cli


def batches(source='synthetic_test'):
    q = {'issue_id': 0, 'code': '0000', 'frame': 1, 'source_time_us': 100, 'indicative_price10': 123}
    return [dict(type='bootstrap', seq=0, source=source, trading_date='20210927', source_time_us=100,
                 market_issue_count=1, master=[dict(issue_id=0,code='0000',name='Synthetic fixture')], quotes=[q],
                 input_transport={'kind':'local_cache'}, exchange_delay_ms=None,
                 source_timestamp_origin='brisk_decoder_unverified'),
            dict(type='quotes', seq=1, source_time_us=110, quotes=[{**q,'frame':2,'source_time_us':110}],
                 decode_ns=1,received_unix_ms=1,replay_lateness_ms=0),
            dict(type='end',seq=2,source_time_us=110,frames=2,quote_updates=1,replay_wall_ms=1)]

def stream(bs):
    return io.BytesIO(b''.join(json.dumps(b).encode()+b'\n' for b in bs))

@pytest.fixture
def packaged(tmp_path):
    events=tmp_path/'source.jsonl'; events.write_bytes(stream(batches()).read())
    directory=tmp_path/'package'
    manifest=cli.package(events,directory,'test','CC0-1.0')
    return directory,manifest

class S3:
    def __init__(self):
        self.objects={}
        self.versions={}
    def put_object(self, Bucket, Key, Body, **kw):
        if kw.get('IfNoneMatch') and Key in self.objects:
            raise ClientError({'Error':{'Code':'PreconditionFailed'}},'PutObject')
        self.objects[Key]=Body.read() if hasattr(Body,'read') else Body
        return {}
    def get_object(self,Bucket,Key,VersionId=None):
        try:
            data=self.versions[(Key,VersionId)] if VersionId else self.objects[Key]
        except KeyError:
            raise ClientError({'Error':{'Code':'NoSuchKey'}},'GetObject')
        return {'Body':io.BytesIO(data),'ContentLength':len(data)}
    def generate_presigned_post(self,**kw):
        self.post_args=kw
        return {'url':'https://example.test','fields':{'key':kw['Key']}}
    def get_paginator(self,name):
        return self
    def paginate(self,Bucket,Prefix):
        keys=[{'Key':k} for k in self.objects if k.startswith(Prefix)]
        return [{'Contents':keys[:1]},{'Contents':keys[1:]}]

class DB:
    def __init__(self):
        self.calls=[]; self.versions={}; self.reject=False
    def update_item(self,**kw):
        self.calls.append(kw)
        key=kw['Key']['id']['S']; values=kw['ExpressionAttributeValues']
        if ':version' in values:
            version=values[':version']['S']
            if key in self.versions and self.versions[key]!=version:
                raise ClientError({'Error':{'Code':'ConditionalCheckFailedException'}},'UpdateItem')
            self.versions[key]=version
        if self.reject:
            raise ClientError({'Error':{'Code':'ConditionalCheckFailedException'}},'UpdateItem')
        return {}

def event(method='POST',body=None,query=''):
    return {'requestContext':{'http':{'method':method,'sourceIp':'127.0.0.1'}},
            'body':json.dumps(body),'rawQueryString':query}

def upload(s3,manifest,directory,ticket,version='v1',data=None):
    key=f'incoming/{ticket}/events.jsonl.gz'
    s3.versions[(key,version)] = data if data is not None else (directory/'events.jsonl.gz').read_bytes()
    return {'s3':{'bucket':{'name':service.BUCKET},'object':{'key':key,'versionId':version}}}

def test_automatic_publish_public_pull(packaged,tmp_path,monkeypatch):
    directory,m=packaged;s3=S3();db=DB()
    ticket=json.loads(service.api(event(body=m),s3,db)['body'])['ticket']
    assert s3.post_args['Conditions'][-1]==['content-length-range',m['bytes'],m['bytes']]
    rec=upload(s3,m,directory,ticket)
    service.ingest(rec,s3,db)
    status=json.loads(service.api(event('GET',query='ticket='+ticket),s3,db)['body'])
    assert status['status']=='published'
    entries=list(cli.manifests(s3,'bucket'))
    assert len(entries)==1 and entries[0][1]==m
    result=cli.pull(s3,'bucket',status['prefix'],tmp_path/'download')
    assert result==m and (tmp_path/'download/events.jsonl').read_bytes()==stream(batches()).read()
    # At-least-once event delivery and overwrite attempts cannot change a publication.
    service.ingest(rec,s3,db)
    service.ingest(upload(s3,m,directory,ticket,'v2',b'bad'),s3,db)
    assert json.loads(service.api(event('GET',query='ticket='+ticket),s3,db)['body'])['status']=='published'
    with pytest.raises(ValueError,match='already exists'):
        cli.pull(s3,'bucket',status['prefix'],tmp_path/'download')

@pytest.mark.parametrize('mutation',[
    lambda b:b[1].update(seq=3),
    lambda b:b[0].update(source='live'),
    lambda b:b[0].update(trading_date='20250230'),
    lambda b:b[0].update(quotes=[]),
    lambda b:b[0].update(exchange_delay_ms=0),
    lambda b:b[0].update(input_transport={'token':'secret'}),
    lambda b:b[0]['master'].append(b[0]['master'][0]),
    lambda b:b[1]['quotes'][0].update(code='1111'),
    lambda b:b[1]['quotes'][0].update(frame=0),
    lambda b:b[1]['quotes'][0].update(source_time_us=111),
    lambda b:b[1]['quotes'][0].update(token='secret'),
    lambda b:b[1].update(source_time_us=99),
    lambda b:b[1].update(type='bootstrap'),
    lambda b:b[1].update(master=[]),
    lambda b:b[1].update(decode_ns=-1),
    lambda b:b[1].update(replay_lateness_ms=-1),
    lambda b:b[2].update(quotes=b[0]['quotes']),
    lambda b:b.pop(),
    lambda b:b.append(b[-1]),
])
def test_reject_invalid_stream(mutation):
    b=batches();mutation(b)
    with pytest.raises((ValueError,KeyError)):
        schema.validate_stream(stream(b))

def test_decode_limits(monkeypatch):
    monkeypatch.setattr(schema,'MAX_LINE',3)
    with pytest.raises(ValueError,match='limit'):
        schema.validate_stream(stream(batches()))
    monkeypatch.setattr(schema,'MAX_LINE',1024*1024)
    monkeypatch.setattr(schema,'MAX_EXPANDED',3)
    with pytest.raises(ValueError,match='limit'):
        schema.validate_stream(stream(batches()))
    with pytest.raises(ValueError):
        schema.validate_stream(io.BytesIO(b'{"seq":NaN}\n'))

@pytest.mark.parametrize('change',[{'sha256':'bad'},{'bytes':0},{'contributor':'email@test'},{'license':'MIT'},
    {'redistribution_permitted':False},{'summary':{'source':'live'}},{'schema':'raw'}])
def test_manifest_permissions(packaged,change):
    directory,m=packaged;m.update(change)
    with pytest.raises(ValueError):
        schema.validate_manifest(m)

def test_hash_summary_and_corrupt_gzip(packaged):
    directory,m=packaged;p=directory/'events.jsonl.gz'
    bad=copy.deepcopy(m);bad['summary']['batches']=9
    with pytest.raises(ValueError,match='Summary'):
        schema.inspect_package(p,bad)
    p.write_bytes(b'not gzip');m['bytes']=p.stat().st_size;m['sha256']=schema.digest(p)
    with pytest.raises(OSError):
        schema.inspect_package(p,m)

@pytest.mark.parametrize('corrupt',[b'bad',None])
def test_rejection_stays_private(packaged,corrupt):
    directory,m=packaged;s3=S3();db=DB()
    ticket=json.loads(service.api(event(body=m),s3,db)['body'])['ticket']
    if corrupt is None:
        m=copy.deepcopy(m);m['summary']['batches']=123
        service.json_put(s3,f'incoming/{ticket}/manifest.json',m)
    service.ingest(upload(s3,m,directory,ticket,data=corrupt),s3,db)
    status=service.json_get(s3,f'incoming/{ticket}/status.json')
    assert status['status']=='rejected'
    assert not list(cli.manifests(s3,'bucket'))

def test_api_errors_and_handler(packaged,monkeypatch):
    directory,m=packaged;s3=S3();db=DB()
    monkeypatch.setattr(service,'clients',lambda:(s3,db))
    assert service.handler(event('DELETE'),None)['statusCode']==400
    assert service.handler(event('GET',query='ticket='+str(uuid.uuid4())),None)['statusCode']==404
    assert service.handler(event('GET',query='ticket=wrong'),None)['statusCode']==400
    assert service.handler(event(body={'secret':'bad'}),None)['statusCode']==400
    db.reject=True
    assert service.handler(event(body=m),None)['statusCode']==400
    db.reject=False
    request=event(body=m)
    import base64
    request['isBase64Encoded']=True;request['body']=base64.b64encode(request['body'].encode()).decode()
    ticket=json.loads(service.handler(request,None)['body'])['ticket']
    assert service.handler({'Records':[upload(s3,m,directory,ticket)]},None)=={'ok':True}

@pytest.mark.parametrize('error',['AccessDenied','InternalError'])
def test_cloud_errors_retry(error):
    class Fail:
        def update_item(self,**kw):
            raise ClientError({'Error':{'Code':error}},'UpdateItem')
        def put_object(self,**kw):
            raise ClientError({'Error':{'Code':error}},'PutObject')
    with pytest.raises(ClientError):
        service.quota(Fail(),'x',1,4,3600)
    with pytest.raises(ClientError):
        service.json_put(Fail(),'x',{})

def test_pull_tampered_atomic(packaged,tmp_path):
    directory,m=packaged;s3=S3()
    prefix=f"archive/20210927/{m['sha256']}"
    service.json_put(s3,prefix+'/manifest.json',m)
    s3.objects[prefix+'/events.jsonl.gz']=b'bad'
    with pytest.raises(ValueError,match='mismatch'):
        cli.pull(s3,'bucket',prefix,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()
    with pytest.raises(ValueError):
        list(cli.manifests(s3,'bucket','../'))
    with pytest.raises(ValueError):
        cli.pull(s3,'bucket','incoming/secret',tmp_path/'bad')

class HTTP:
    def __init__(self,value=b''):self.value=value
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self,*a):return self.value

@pytest.mark.parametrize('status',['published','rejected','timeout'])
def test_contribute_auto_poll(packaged,monkeypatch,status):
    directory,m=packaged
    responses=[{'ticket':str(uuid.uuid4()),'upload':{'url':'https://example.test','fields':{'key':'k'}}},
               {'status':'awaiting_upload'}, {'status':status}]
    monkeypatch.setattr(cli,'request_json',lambda *a:responses.pop(0))
    monkeypatch.setattr(cli.urllib.request,'urlopen',lambda *a,**k:HTTP())
    monkeypatch.setattr(cli.time,'sleep',lambda *a:None)
    if status=='timeout':
        monkeypatch.setattr(cli.time,'monotonic',iter([0,999]).__next__)
        with pytest.raises(TimeoutError):cli.contribute(directory,'https://api.test',1)
    elif status=='rejected':
        with pytest.raises(ValueError):cli.contribute(directory,'https://api.test')
    else:
        assert cli.contribute(directory,'https://api.test')['status']=='published'

def test_request_json(monkeypatch):
    monkeypatch.setattr(cli.urllib.request,'urlopen',lambda *a,**kw:HTTP(b'{"ok":true}'))
    assert cli.request_json('https://example.test',{'a':1})=={'ok':True}

@pytest.mark.parametrize('command',['package','record','upload','list','pull'])
def test_cli(command,packaged,tmp_path,monkeypatch,capsys):
    directory,m=packaged;config=tmp_path/'config.json'
    config.write_text(json.dumps(dict(bucket='b',region='ap-northeast-1',api_url='https://api.test')))
    monkeypatch.setattr(cli,'contribute',lambda *a:{'status':'published'})
    monkeypatch.setattr(cli,'client',lambda *a:None)
    monkeypatch.setattr(cli,'manifests',lambda *a:[('archive/p',m)])
    monkeypatch.setattr(cli,'pull',lambda *a:m)
    def run(cmd,check):
        Path(cmd[cmd.index('--events')+1]).write_bytes(stream(batches()).read())
    monkeypatch.setattr(cli.subprocess,'run',run)
    args=['--config',str(config),command]
    if command in {'package','record'}:
        args+=['--output',str(tmp_path/'out'),'--contributor','test','--license','CC0-1.0','--redistribution-permitted','--upload']
        if command=='package':args+=['--events',str(tmp_path/'source.jsonl')]
        else:args+=['--web','--codes','0000','--limit-frames','2']
    elif command=='upload':args+=[str(directory)]
    elif command=='list':args+=['--source','synthetic_test']
    else:args+=['archive/p','--output',str(tmp_path/'out')]
    cli.main(args)
    assert capsys.readouterr().out


def test_mock_transport_validation():
    b=batches('historical_mock')
    b[0]['input_transport']={'kind':'https_recorded_assets','origin':'https://next-demo.brisk.jp','asset_fetch_ms':123.5}
    assert schema.validate_stream(stream(b))['source']=='historical_mock'
    b[0]['input_transport']['origin']='https://bad.test'
    with pytest.raises(ValueError):schema.validate_stream(stream(b))
