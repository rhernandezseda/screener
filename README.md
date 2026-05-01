# Farseer — US Growth Stock Screener

A stock screening and analysis tool powered by Playwright. No API keys, no subscriptions.
Data is scraped directly from stockanalysis.com.

---

## What it does

- **Screener** — applies 7 growth filters and returns matching US stocks
- **Analysis** — deep-dives a single ticker: financials, technicals, news, analyst ratings

### Screener filters

| Filter | Condition |
|--------|-----------|
| Market Cap | Over $2B |
| Stock Price | Over $9 |
| Dividend Yield | Zero (no dividend) |
| Revenue Growth YoY | Over 20% |
| Average Volume | Over 200,000 |
| EPS Growth Next Year | Over 0% |
| Distance from 52W High | Within 20% |

---

## Project structure

```
screener/
├── backend/
│   ├── server.py         — API server (analyze, status, refresh endpoints)
│   ├── analyze.py        — Scrapes a single ticker and writes JSON
│   ├── screener.py       — Runs the screener and generates screener.html
│   ├── html_templates.py — HTML renderer for screener.html
│   ├── start.py          — Local launcher (server + screener + browser)
│   └── requirements.txt
├── frontend/
│   ├── screener.html     — Source for the screener page (copied to output/ on run)
│   └── analysis.html     — Analysis page (copied to output/ on run)
└── output/               — Generated at runtime, not committed to git
    ├── screener.html
    ├── analysis.html
    └── data/
        ├── screener.json
        └── tickers/
            └── *.json
```

---

## Running fully locally (no Railway / Vercel)

This is the simplest way to run the app — everything on your own machine.
If the online deployment ever stops working, these steps will get you back to a fully working local setup.

**1. Install dependencies (one time only)**

```bash
pip install -r backend/requirements.txt
playwright install chromium
```

**2. Run**

```bash
python backend/start.py
```

That's it. The script will:
- Start the local API server on port 8765
- Run the screener (takes ~60 seconds, opens stockanalysis.com headlessly)
- Open `output/screener.html` in your browser automatically

**3. Analyze a stock**

Click any card in the screener to open the analysis page.
Or run directly:

```bash
python backend/analyze.py AAPL
```

Then open `output/analysis.html?ticker=AAPL` in your browser.

**Notes**
- The frontend HTML files (`analysis.html`, `screener.html`) automatically talk to `localhost:8765` when no online backend URL is configured — no changes needed.
- Re-run `python backend/start.py` anytime to get fresh screener data.
- Re-click a card (or run `analyze.py TICKER`) to refresh an individual stock.

---

## Deployment (Railway + Vercel)

The app is split so the Python backend runs on Railway and the static HTML frontend is served by Vercel.

### Backend — Railway

1. Connect this repo to a new Railway project
2. Set the root directory to `backend/`
3. Railway will detect Python and install `requirements.txt` automatically
4. Add a `playwright install chromium` step in the build command
5. Railway injects a `PORT` env var automatically — the server reads it

### Frontend — Vercel

1. Connect this repo to a new Vercel project
2. Set the root directory to `frontend/`
3. Add an environment variable: `BACKEND_URL=https://your-railway-app.railway.app`
4. Vercel serves the static HTML files

The frontend reads `window.BACKEND_URL` if set, otherwise falls back to `http://localhost:8765` — so the same code works both online and locally without any changes.

---

## Tech stack

- Python 3.11+
- Playwright (headless Chromium)
- Vanilla HTML/CSS/JS (no framework)
- TradingView embedded charts
- Data source: stockanalysis.com
