# TBG Holdings Tracker

Checks the [TBG Dividend Growth ETF](https://tbgdividendgrowth.com/) holdings every weekday, and alerts your phone when the fund meaningfully buys or sells a stock.

## How it decides what's a real trade

- It compares each stock's **share count** with the previous day's.
- When money flows into or out of the fund, every holding grows or shrinks by about the same amount. That day's typical (median) change is measured and removed, so only trades that stand out from the flow are flagged.
- You get an alert when the adjusted change is **more than 3%**, or when a stock is **added** or **fully sold**.
- Cash and the money-market fund (`Cash&Other`, `FGXXX`) are ignored.
- If a change looks like a **stock split** (shares doubled and the price halved, for example), the alert says "possible stock split - check".

To change the threshold or the ignore list, edit the `settings` block near the top of `tracker.py`.

## What it saves

| File | What it is |
|---|---|
| `data/holdings/YYYY-MM-DD.csv` | Each day's full holdings: shares, price, market value, % of fund |
| `data/alerts.csv` | Every alert ever sent (opens in Excel or Google Sheets) |
| `docs/index.html` | The dashboard page |

## One-time setup (about 15 minutes)

1. **Create a GitHub account** at github.com if you don't have one.
2. **Create a new repository**: click **+** at the top right, choose **New repository**, name it `etf-tracker`, choose **Public** (needed for the free dashboard page), then click **Create**.
3. **Upload these files**: on the new repo page, click **uploading an existing file** and drag in everything from this folder, including the `.github` folder. Hidden folders can be tricky to drag; if `.github` doesn't upload, create the file by hand with **Add file → Create new file**, name it `.github/workflows/daily.yml`, and paste in the contents.
4. **Set up phone alerts**:
   - Install the free **ntfy** app (iPhone or Android).
   - In the app, tap **+** and subscribe to a topic with a hard-to-guess name, such as `tbg-alerts-x7k29q`. Anyone who knows the name can read the alerts, so make it random.
   - In GitHub: **Settings → Secrets and variables → Actions → New repository secret**. Name it `NTFY_TOPIC` and set the value to your topic name.
5. **Turn on the dashboard**: **Settings → Pages**, set Source to **Deploy from a branch**, set the branch to `main` with folder `/docs`, then click **Save**. After a minute the page will be at `https://<your-username>.github.io/etf-tracker/`.
6. **Test it**: on the **Actions** tab, choose **Daily holdings check**, then **Run workflow**. A green check means it worked.

From then on it runs by itself twice each weekday. Your first alerts can come after the fund publishes its next day of holdings.
