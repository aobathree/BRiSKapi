"""SBI BRiSK client with pybrisk's sample payloads and a fake HTTP layer (no account needed)."""
import base64
import datetime as dt
import io
import json
import os
import stat
import sys
import urllib.error
import zlib

import pytest

import briskapi
import briskapi.cli as cli
from briskapi import sbi
from briskapi._recording import JST

BOOT = {'result': True, 'user_id': 'test-uuid', 'api_token': 'v2.local.testtoken', 'api_endpoint': 'https://api.brisk.jp'}
APP_BOOT = {'result': True, 'series': 0, 'date': '2026-03-11', 'session_status': 'running',
            'ws_url': '/realtime/0?session=abc', 'master': 'masterhash', 'snapshot': 'snaphash',
            'schedule_info': {'morning_session_pre_open_time': 28800000000, 'morning_session_open_time': 32400000000,
                              'morning_session_close_time': 41400000000, 'afternoon_session_pre_open_time': 43500000000,
                              'afternoon_session_open_time': 45000000000, 'afternoon_session_pre_close_time': 55500000000,
                              'afternoon_session_close_time': 55800000000, 'sq_jump_interval': 180}}
OHLC = {'ohlc5min': [{'date': '2026-03-11', 'index': 0, 'diff': 0, 'open_price': 2000, 'high_price': 2050,
                      'low_price': 1980, 'close_price': 2030, 'turnover': 100000000}],
        'ohlc1day': [{'date': '2026-03-11', 'open_price': 2000, 'high_price': 2100, 'low_price': 1950,
                      'close_price': 2080, 'turnover': 500000000}],
        'ohlc1week': [{'year': 2026, 'week': 10, 'open_price': 1, 'high_price': 2, 'low_price': 1, 'close_price': 2, 'turnover': 3}],
        'ohlc1month': [{'year': 2026, 'month': 3, 'open_price': 1, 'high_price': 2, 'low_price': 1, 'close_price': 2, 'turnover': 3}]}
JSFC = {'0': {'date': '2026-03-11', 'kakuhoLongShares': 100000, 'kakuhoShortShares': 200000, 'gyakuhibuFee': 0.05}}
MARKETS = {'market_conditions': [{'index': 0, 'issue_code': '3655', 'kind': 1, 'type': 6, 'price10': 26910,
                                  'value10': 2691000000, 'diff_bps_from_last': 0, 'time': '08:00:00.048598'}]}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


class Opener:
    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def open(self, request, timeout):
        self.requests.append(request)
        path = request.full_url.removeprefix(sbi.ORIGIN).split('?')[0]
        value = self.routes.get(path, 404)
        if isinstance(value, int):
            raise urllib.error.HTTPError(request.full_url, value, 'error', {}, io.BytesIO(b'details'))
        return Response(value if isinstance(value, bytes) else json.dumps(value).encode())


def client(**routes):
    opener = Opener({'/api/frontend/boot': BOOT, '/api/app/boot': APP_BOOT, **routes})
    session = sbi.Session({'session_bfaf77a2': 'v2.local.cookie'}, rate_limit=0, opener=opener)
    return sbi.Client(session=session), opener


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.delenv('BRISK_SBI_COOKIES', raising=False)
    monkeypatch.setattr(sbi, '_client', None)
    monkeypatch.setattr(briskapi, '_current', None)


def test_boot_and_headers():
    c, opener = client()
    assert c.date == '2026-03-11' and c.boot['series'] == 0
    boot, app = opener.requests
    assert boot.get_header('Cookie') == 'session_bfaf77a2=v2.local.cookie' and not boot.has_header('Authorization')
    assert app.get_header('Authorization') == 'Bearer v2.local.testtoken'
    c.boot  # cached: no further requests
    assert len(opener.requests) == 2


def test_ticker_ohlc_and_jsfc():
    c, opener = client(**{'/api/ohlc/7203': OHLC, '/api/jsfc/7203': JSFC})
    t = c.ticker('7203')
    assert repr(t) == "Ticker('7203')"
    five = t.ohlc('5m')
    assert five == [{'date': '2026-03-11', 'index': 0, 'open': 2000, 'high': 2050, 'low': 1980, 'close': 2030,
                     'turnover': 100000000}]
    assert t.ohlc()[0]['close'] == 2080 and t.ohlc('1w')[0]['week'] == 10 and t.ohlc('1mo')[0]['month'] == 3
    assert 'date=2026-03-11' in opener.requests[2].full_url
    with pytest.raises(ValueError, match='interval'):
        t.ohlc('1h')
    margin = t.jsfc(count=30)
    assert margin[0]['long_shares'] == 100000 and margin[0]['borrowing_fee'] == 0.05 and margin[0]['borrowing_fee_max'] is None
    assert 'count=30' in opener.requests[-1].full_url


def test_market_endpoints():
    groups = base64.b64encode(zlib.compress(json.dumps({'groups': [{'items': [{'code': '7203'}, {'x': 1}]}]}).encode()))
    flat = base64.b64encode(zlib.compress(json.dumps(['6758', {'code': '9984'}, {'x': 1}]).encode()))
    c, opener = client(**{
        '/api/stocks_info': [{'issue_code': '7203', 'turnover': 5000000000, 'calc_shares_outstanding': 1000000000}],
        '/api/stock_lists': {'version': '1', 'stock_lists': [{'id': 'nk225etf', 'name': 'NK225', 'issue_codes': ['1332', '7203']}]},
        '/api/markets': MARKETS,
        '/api/frontend/watchlist': {'empty': False, 'data': groups.decode()}})
    m = c.market()
    assert m.stocks_info() == [{'code': '7203', 'turnover': 5000000000, 'shares_outstanding': 1000000000}]
    assert m.stock_lists() == {'nk225etf': ['1332', '7203']}
    alert = m.alerts()[0]
    assert alert['price'] == 2691.0 and alert['value'] == 269100000.0 and alert['code'] == '3655'
    assert alert['time'] == dt.datetime(2026, 3, 11, 8, 0, 0, 48598, tzinfo=JST)
    assert 'series=0' in opener.requests[-1].full_url and 'index_to=618' in opener.requests[-1].full_url
    schedule = m.schedule()
    assert schedule['status'] == 'running' and schedule['morning_open'] == dt.datetime(2026, 3, 11, 9, tzinfo=JST)
    assert schedule['afternoon_close'].hour == 15 and schedule['afternoon_close'].minute == 30
    assert m.watchlist() == ['7203']
    opener.routes['/api/frontend/watchlist'] = {'empty': False, 'data': flat.decode()}
    assert m.watchlist() == ['6758', '9984']
    opener.routes['/api/frontend/watchlist'] = {'empty': True}
    assert m.watchlist() == []


@pytest.mark.parametrize('status,error', [(401, sbi.SessionExpiredError), (302, sbi.SessionExpiredError),
                                          (404, briskapi.NotFoundError), (429, sbi.RateLimitError), (500, sbi.APIError)])
def test_errors(status, error):
    c, _ = client(**{'/api/ohlc/7203': status})
    with pytest.raises(error):
        c.ticker('7203').ohlc()
    assert str(sbi.APIError(500)) == 'HTTP 500'


def test_session_rules(monkeypatch):
    with pytest.raises(sbi.SessionExpiredError, match='login'):
        sbi.Session({})
    assert sbi._NoRedirect().redirect_request(None, None, 302, 'Found', {}, 'https://elsewhere.test/') is None
    slept = []
    monkeypatch.setattr(sbi.time, 'sleep', slept.append)
    session = sbi.Session({'a': 'b'}, rate_limit=2, opener=Opener({'/raw': b'\x00\x01'}))
    assert session.get('/raw', raw=True) == b'\x00\x01'
    session.get('/raw', raw=True)
    assert slept and 0 < slept[0] <= 0.5


def test_login_sources(monkeypatch, tmp_path):
    with pytest.raises(sbi.SessionExpiredError, match='Not logged in'):
        sbi.Ticker('7203').ohlc()
    with pytest.raises(sbi.SessionExpiredError):
        sbi.login()
    monkeypatch.setenv('BRISK_SBI_COOKIES', '{"from": "env"}')
    assert sbi.login().session.cookies == {'from': 'env'}
    monkeypatch.delenv('BRISK_SBI_COOKIES')
    sbi.login({'saved': 'yes'}, remember=True)
    path = sbi.cookies_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and json.loads(path.read_text()) == {'saved': 'yes'}
    assert sbi.login().session.cookies == {'saved': 'yes'}
    assert sbi.Market().client is sbi._client and sbi.Ticker('1').client is sbi._client
    sbi.logout()
    assert not path.exists() and sbi._client is None


@pytest.fixture
def fake_host(tmp_path, monkeypatch):
    """A stand-in for sbi.cjs that checks its cookies and prints a tiny live session."""
    q = {'issue_id': 0, 'code': '7203', 'frame': 1, 'max_frame': 1, 'source_time_us': 32400000000,
         'indicative_price10': 101500, 'market_buy_quantity': 3, 'market_sell_quantity': 1}
    batches = [dict(type='bootstrap', seq=0, source='sbi_live', trading_date='20260311', source_time_us=32400000000,
                    market_issue_count=1, master=[{'issue_id': 0, 'code': '7203', 'name': 'Toyota'}], quotes=[q]),
               dict(type='quotes', seq=1, source_time_us=32400000100, quotes=[{**q, 'frame': 2, 'last_price10': 101600}]),
               dict(type='end', seq=2, source_time_us=32400000100, frames=2, quote_updates=1)]
    script = tmp_path / 'sbi_fake.cjs'
    script.write_text(
        "const c = JSON.parse(process.env.BRISK_SBI_COOKIES); if (c.session_bfaf77a2 !== 'v') process.exit(9);\n"
        f"console.error(JSON.stringify(process.argv.slice(2)));\n"
        f"for (const b of {json.dumps(batches)}) console.log(JSON.stringify(b));\n")
    monkeypatch.setattr(sbi, 'DECODER', script)


def test_live_feed_via_node(fake_host, capfd):
    sbi.login({'session_bfaf77a2': 'v'})
    feed = sbi.connect(codes=['7203'], history=True)
    assert briskapi.current() is feed and feed.source == 'sbi_live'
    feed.wait()
    assert briskapi.Ticker('7203').quote()['last_price'] == 10160.0 and feed.contribution is None
    assert len(briskapi.Ticker('7203').history()) == 2
    # Cookies reach the host through its environment, never its (world-readable) arguments.
    assert capfd.readouterr().err.strip() == '["--codes","7203"]'


def test_live_feed_failure_closes(fake_host, tmp_path, monkeypatch):
    sbi.login({'session_bfaf77a2': 'wrong'})
    with pytest.raises(briskapi.BriskError, match='before bootstrap'):
        sbi.connect()


def test_cli_live_sbi(fake_host, monkeypatch, capsys):
    monkeypatch.setenv('BRISK_SBI_COOKIES', '{"session_bfaf77a2": "v"}')
    monkeypatch.setattr(cli, 'interactive', lambda: True)  # never asked: SBI sessions are not shared
    cli.main(['live', '--sbi', '--codes', '7203'])
    out = capsys.readouterr()
    lines = [json.loads(line) for line in out.out.splitlines()]
    assert lines[-1]['last_price'] == 10160.0 and 'public and permanent' not in out.err
    assert cli.load_consent() is None
