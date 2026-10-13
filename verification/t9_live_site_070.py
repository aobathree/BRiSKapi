"""Live-feed check for a broker BRiSK site (0.7.0). Run after a fresh sign-in has saved cookies
(sbi.login(cookies=..., site=SITE, remember=True) or `brisk login`). Nothing is shared or recorded.

    BRISK_CONTRIBUTE=0 python checks/t9_live_site_070.py e-shiten|sbi [seconds=60]
"""
import sys, time, briskapi
from briskapi import sbi
site, secs = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 60
sbi.login(site=site)
print("schedule:", briskapi.Market().schedule())
t0 = time.time()
feed = sbi.connect(codes=["7203", "6758"], history=True, contribute=False, trace_protocol=True, share_market_data=False)
print("connect returned in %.1fs" % (time.time() - t0))
m, t = briskapi.Market(), briskapi.Ticker("7203")
print("summary:", m.summary())
print("stocks:", len(m.stocks()), "first:", m.stocks()[0] if m.stocks() else None)
print("info 7203:", t.info())
print("quote 7203:", t.quote())
n = {"updates": 0, "first": None}
def on_q(q):
    n["updates"] += 1
    if n["first"] is None: n["first"] = q
feed.on_quote(on_q)
time.sleep(secs)
feed.close()
print("updates in %ds: %d; first: %s" % (secs, n["updates"], n["first"]))
print("history rows 7203:", len(t.history()))
