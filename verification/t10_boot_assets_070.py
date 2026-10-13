"""Show what the e-shiten/SBI boot serves today (sizes and ids only; tokens and ws_url never printed)."""
import sys, hashlib, briskapi
from briskapi import sbi
site = sys.argv[1] if len(sys.argv) > 1 else "e-shiten"
c = sbi.login(site=site)
b = c.boot
print("boot keys:", sorted(b.keys()))
print({k: b.get(k) for k in ("date", "series", "flex_version", "master", "snapshot")})
print("base_prices:", len(b.get("base_prices") or []), "exceptional_sq:", len(b.get("exceptional_sq") or []),
      "ws_url present:", bool(b.get("ws_url")))
for name, key in (("master", "master"), ("snapshot", "snapshot")):
    data = c.session.get(f"/api/{name}/{b[key]}", raw=True)
    print(f"{name}: {len(data):,} bytes sha256 {hashlib.sha256(data).hexdigest()[:16]}…")
