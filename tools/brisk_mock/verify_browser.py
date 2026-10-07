"""Compare the adapter with the pinned demo's own JS book accessors (no account)."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

CODES = ["7203", "6758", "8306", "9984", "6920", "5659"]
MAIN = "https://next-demo.brisk.jp/main.e7eddef108d814fc1852.js"


def verify(cache: Path) -> None:
    command = ["node", str(Path(__file__).resolve().parents[2] / "briskapi/decoder/decoder.cjs"), "--cache", str(cache),
               "--codes", ",".join(CODES), "--speed", "0", "--limit-frames", "1"]
    baseline = json.loads(subprocess.check_output(command, text=True).splitlines()[0])
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def expose(route):
            response = route.fetch()
            source = response.text()
            needle = "this.op=new _U(this.flexOperatorService,e.flexVersion)"
            if source.count(needle) != 1:
                raise ValueError("Demo bundle changed; re-audit the accessor bridge")
            source = source.replace(needle, "window.__briskMarket=" + needle)
            route.fulfill(response=response, body=source)

        page.route(MAIN, expose)
        page.goto("https://next-demo.brisk.jp/", wait_until="domcontentloaded")
        # The tutorial keeps the replay paused. Wait for snapshot loading.
        page.wait_for_function("window.__briskMarket?.masterLoaded && window.__briskMarket.getTimestamp() > 0", timeout=60000)
        actual = page.evaluate("""codes => {
            const op = window.__briskMarket;
            const emptyRow = () => ({bid:{}, ask:{}, bidClose:{}, askClose:{}});
            return codes.map(code => {
                const id = op.issueCodeMap[code], master = op.stockMasters[id];
                const view = {master, ohlc:Array.from({length:60}, () => ({})), predictLastPrice:{}};
                if (!op.ops.getStockView(op.id, id, view)) throw Error('No view');
                const market = emptyRow(), over = emptyRow(), under = emptyRow();
                const index = op.ops.fitItaViewRowPrice10(op.id, id, view.predictPrice10 || master.basePrice10, 1);
                if (!op.ops.getItaRow(op.id, id, index, 1, [emptyRow()], market, over, under)) throw Error('No book');
                return {code, indicative_price10:view.predictPrice10, indicative_volume:view.predictVolume,
                    bid_price10:view.bidPrice10, ask_price10:view.askPrice10,
                    market_buy_quantity:market.bid.quantity, market_sell_quantity:market.ask.quantity};
            });
        }""", CODES)
        browser.close()
    expected = {q["code"]: q for q in baseline["quotes"]}
    for quote in actual:
        for key, value in quote.items():
            assert expected[quote["code"]][key] == value, (quote["code"], key, value)
    print(json.dumps({"verified_codes": CODES, "fields_per_code": len(actual[0]) - 1,
                      "snapshot_time_us": baseline["source_time_us"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    verify(parser.parse_args().cache)
