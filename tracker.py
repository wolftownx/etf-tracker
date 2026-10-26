"""
TBG Dividend Growth ETF - daily holdings tracker.

Each run:
  1. Opens https://tbgdividendgrowth.com/ in a headless browser and reads the
     "Fund Holdings" table (all rows).
  2. Saves it as data/holdings/YYYY-MM-DD.csv (date = the holdings' effective date).
  3. Compares it with the previous saved day and sends an alert for any
     holding whose share count changed by more than THRESHOLD, after removing
     the fund-wide inflow/outflow effect.
  4. Rebuilds the dashboard page at docs/index.html.
"""

import csv
import html
import os
import re
import statistics
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- settings --
FUND_URL = "https://tbgdividendgrowth.com/"
FUND_NAME = "TBG"
THRESHOLD = 0.03  # 3% - alert when a holding's (flow-adjusted) share change is bigger than this
IGNORE = {"Cash&Other", "FGXXX"}  # cash / money-market lines change every day; never alert on them
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")  # phone alerts via the free ntfy app; blank = no push

ROOT = Path(os.environ.get("DATA_ROOT", Path(__file__).parent))
HOLDINGS_DIR = ROOT / "data" / "holdings"
ALERTS_FILE = ROOT / "data" / "alerts.csv"
DASHBOARD_FILE = ROOT / "docs" / "index.html"
# -----------------------------------------------------------------------------


def scrape():
    """Return (as_of_date 'YYYY-MM-DD', list of holding dicts)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # BROWSER_CHANNEL=chrome uses the Google Chrome already installed on GitHub's machines.
        browser = p.chromium.launch(
            channel=os.environ.get("BROWSER_CHANNEL") or None,
            executable_path=os.environ.get("CHROMIUM_PATH") or None,
        )
        page = browser.new_page()
        # The site's table script hides columns (price, market value, date) and pages
        # the rows 5 at a time. Blocking it leaves the full table with every row and column.
        page.route(lambda url: "wpdatatables" in url and ".js" in url, lambda route: route.abort())
        page.goto(FUND_URL, wait_until="networkidle", timeout=90_000)

        table = page.locator("h2:has-text('Fund Holdings') >> xpath=following::table[1]")
        headers = table.locator("thead th").evaluate_all("ths => ths.map(th => th.textContent.trim())")
        rows = table.locator("tbody tr").evaluate_all(
            "trs => trs.map(tr => [...tr.cells].map(td => td.textContent.trim()))"
        )
        browser.close()

    def number(s):
        return float(s.replace(",", "").replace("%", "").replace("$", "") or 0)

    holdings, dates = [], []
    for r in rows:
        cell = dict(zip(headers, r))
        if not cell.get("Ticker"):
            continue
        holdings.append({
            "ticker": cell["Ticker"],
            "name": cell.get("Name", ""),
            "cusip": cell.get("CUSIP", ""),
            "shares": int(number(cell["Shares"])),
            "price": number(cell.get("Price", "")),
            "market_value_mm": number(cell.get("Market Value ($mm)", "")),
            "weight": number(cell["% of Net Assets"]),
        })
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", cell.get("EFFECTIVE_DATE", "")):
            dates.append(cell["EFFECTIVE_DATE"])

    if len(holdings) < 5 or not dates:
        sys.exit(f"Only found {len(holdings)} holdings / no date - the site layout may have changed. Columns seen: {headers}")
    as_of = datetime.strptime(max(set(dates), key=dates.count), "%m/%d/%Y").strftime("%Y-%m-%d")
    return as_of, holdings


def save_holdings(as_of, holdings):
    HOLDINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(HOLDINGS_DIR / f"{as_of}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "name", "cusip", "shares", "price", "market_value_mm", "weight"])
        w.writeheader()
        w.writerows(holdings)


def load_holdings(path):
    with open(path, newline="") as f:
        return {
            r["ticker"]: {**r, "shares": int(r["shares"]), "price": float(r["price"]), "weight": float(r["weight"])}
            for r in csv.DictReader(f)
        }


def looks_like_split(ratio, old_price, new_price):
    """Share count jumped by a typical split ratio and the price moved the opposite way."""
    split_ratios = [2, 3, 4, 5, 10, 1.5, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 2 / 3]
    near_split = any(abs(ratio / s - 1) < 0.02 for s in split_ratios)
    price_offsets = old_price > 0 and abs(ratio * new_price / old_price - 1) < 0.15
    return near_split and price_offsets


def compare(prev, cur):
    """Return (flow_factor, list of alert dicts)."""
    # How much did the whole fund grow/shrink from inflows/outflows?
    # Use the median change across holdings held both days.
    ratios = [
        cur[t]["shares"] / prev[t]["shares"]
        for t in cur
        if t in prev and t not in IGNORE and prev[t]["shares"] > 0
    ]
    flow = statistics.median(ratios) if ratios else 1.0

    alerts = []
    for t in sorted(set(prev) | set(cur)):
        if t in IGNORE:
            continue
        old, new = prev.get(t), cur.get(t)
        old_sh = old["shares"] if old else 0
        new_sh = new["shares"] if new else 0
        name = (new or old)["name"]

        if old_sh == 0 and new_sh > 0:
            action, raw, adj, note = "NEW POSITION", None, None, ""
        elif new_sh == 0 and old_sh > 0:
            action, raw, adj, note = "SOLD OUT", -1.0, -1.0, ""
        else:
            raw = new_sh / old_sh - 1
            adj = new_sh / (old_sh * flow) - 1
            if abs(adj) <= THRESHOLD:
                continue
            action = "BUY" if adj > 0 else "SELL"
            note = "possible stock split - check" if looks_like_split(new_sh / old_sh, old["price"], new["price"]) else ""

        alerts.append({
            "ticker": t, "name": name, "action": action,
            "old_shares": old_sh, "new_shares": new_sh,
            "raw_change": raw, "adjusted_change": adj, "note": note,
        })
    return flow, alerts


def pct(x):
    return "" if x is None else f"{x * 100:+.1f}%"


def alert_line(a):
    line = f"{a['action']} {a['ticker']} ({a['name']}): {a['old_shares']:,} -> {a['new_shares']:,} shares"
    if a["action"] in ("BUY", "SELL"):
        line += f" ({pct(a['raw_change'])}, {pct(a['adjusted_change'])} after fund flows)"
    if a["note"]:
        line += f" [{a['note']}]"
    return line


def record_alerts(as_of, prev_date, alerts):
    new_file = not ALERTS_FILE.exists()
    with open(ALERTS_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "compared_to", "ticker", "name", "action", "old_shares",
                        "new_shares", "raw_change_pct", "adjusted_change_pct", "note"])
        for a in alerts:
            w.writerow([as_of, prev_date, a["ticker"], a["name"], a["action"], a["old_shares"],
                        a["new_shares"], pct(a["raw_change"]), pct(a["adjusted_change"]), a["note"]])


def send_push(title, message):
    if not NTFY_TOPIC:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode(),
        headers={"Title": title, "Tags": "chart_with_upwards_trend"},
    )
    urllib.request.urlopen(req, timeout=30)


def build_dashboard(as_of, cur, prev, flow):
    alerts = []
    if ALERTS_FILE.exists():
        with open(ALERTS_FILE, newline="") as f:
            alerts = list(csv.DictReader(f))[::-1][:100]

    def row(cells, cls=""):
        return f"<tr class='{cls}'>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in cells) + "</tr>"

    alert_rows = "".join(
        row([a["date"], a["action"], a["ticker"], a["name"], f"{int(a['old_shares']):,}",
             f"{int(a['new_shares']):,}", a["adjusted_change_pct"], a["note"]],
            "buy" if a["action"] in ("BUY", "NEW POSITION") else "sell")
        for a in alerts
    ) or "<tr><td colspan='8'>No alerts yet.</td></tr>"

    holding_rows = ""
    for h in sorted(cur.values(), key=lambda h: -h["weight"]):
        old = prev.get(h["ticker"]) if prev else None
        chg = h["shares"] / old["shares"] - 1 if old and old["shares"] else None
        holding_rows += row([h["ticker"], h["name"], f"{h['shares']:,}", f"{h['weight']:.2f}%", pct(chg)])

    flow_text = f"Fund-wide flow since previous day: {pct(flow - 1)}" if prev else "First snapshot - changes start tomorrow."
    DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_FILE.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{FUND_NAME} Holdings Tracker</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:0 auto;max-width:960px;padding:16px;color:#1a1a1a;background:#fff}}
 h1{{margin-bottom:4px}} .sub{{color:#666;margin-top:0}}
 table{{border-collapse:collapse;width:100%;margin-bottom:32px;font-size:14px}}
 th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #e5e5e5}} th{{background:#f5f5f5}}
 tr.buy td:nth-child(2){{color:#0a7d32;font-weight:600}} tr.sell td:nth-child(2){{color:#c0262d;font-weight:600}}
 .wrap{{overflow-x:auto}}
</style></head><body>
<h1>{FUND_NAME} Holdings Tracker</h1>
<p class="sub">Holdings as of {as_of} &middot; {flow_text} &middot; Alert threshold {THRESHOLD:.0%}</p>
<h2>Trade alerts</h2>
<div class="wrap"><table><tr><th>Date</th><th>Action</th><th>Ticker</th><th>Name</th><th>Old shares</th><th>New shares</th><th>Change (flow-adj.)</th><th>Note</th></tr>
{alert_rows}</table></div>
<h2>Current holdings</h2>
<div class="wrap"><table><tr><th>Ticker</th><th>Name</th><th>Shares</th><th>% of fund</th><th>Share change vs prior day</th></tr>
{holding_rows}</table></div>
</body></html>
""")


def main():
    as_of, holdings = scrape()
    print(f"Read {len(holdings)} holdings as of {as_of}")

    if (HOLDINGS_DIR / f"{as_of}.csv").exists():
        print("Already have this date - nothing new to compare.")
        return

    earlier = sorted(p for p in HOLDINGS_DIR.glob("*.csv") if p.stem < as_of) if HOLDINGS_DIR.exists() else []
    save_holdings(as_of, holdings)
    cur = load_holdings(HOLDINGS_DIR / f"{as_of}.csv")

    if not earlier:
        print("First snapshot saved. Alerts start from the next update.")
        build_dashboard(as_of, cur, None, 1.0)
        return

    prev_path = earlier[-1]
    prev = load_holdings(prev_path)
    flow, alerts = compare(prev, cur)
    print(f"Compared with {prev_path.stem}. Fund-wide flow: {pct(flow - 1)}. Alerts: {len(alerts)}")

    if alerts:
        record_alerts(as_of, prev_path.stem, alerts)
        lines = [alert_line(a) for a in alerts]
        print("\n".join(lines))
        send_push(f"{FUND_NAME}: {len(alerts)} holding change(s) on {as_of}", "\n".join(lines))
    build_dashboard(as_of, cur, prev, flow)


if __name__ == "__main__":
    main()
