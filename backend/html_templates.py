"""
html_templates.py — HTML generation for screener.html
"""

from datetime import datetime
import json
import re
from config import SITE_FILTERS


def _build_chips():
    """Generate filter chip labels from SITE_FILTERS."""
    chips = []
    for cb_id, label, action in SITE_FILTERS:
        if action is None:
            continue
        if isinstance(action, tuple):
            op, val = action
            if cb_id == "marketCap":
                v = int(val)
                chips.append(f"Market Cap > ${v // 1000}B" if v >= 1000 else f"Market Cap > ${v}M")
            elif cb_id == "price":
                chips.append(f"Price > ${val}")
            elif cb_id == "averageVolume":
                v = int(val)
                chips.append(f"Avg Volume > {v // 1000}K" if v >= 1000 else f"Avg Volume > {v}")
            elif cb_id == "high52ch":
                chips.append(f"Within {abs(int(val))}% of 52W High")
            else:
                chips.append(f"{label} {op} {val}")
        else:
            if cb_id == "dividendYield" and action == "No Dividend":
                chips.append("No Dividend")
            else:
                chips.append(f"{label}: {action}")
    return chips


def parse_market_cap_sort(val):
    """Convert market cap string to float for sorting."""
    if not val or val == "N/A":
        return 0
    m = re.search(r"([\d.,]+)([BTM]?)", val.replace(",", ""))
    if not m:
        return 0
    num = float(m.group(1))
    suffix = m.group(2)
    if suffix == "T": return num * 1_000_000
    if suffix == "B": return num * 1_000
    if suffix == "M": return num
    return num


def render_screener(stocks, timestamp):
    ts_human = datetime.now().strftime("%B %d, %Y · %H:%M")
    count = len(stocks)
    stocks_json = json.dumps(stocks)
    chips_html = "\n    ".join(f'<span class="chip">{c}</span>' for c in _build_chips())

    return f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Farseer · US Growth Screener</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --surface:        #0f1117;
    --surface-card:   #1a1d27;
    --surface-border: #2a2d3a;
    --accent:         #6366f1;
    --gain:           #22c55e;
    --gain-dim:       #15803d;
    --loss:           #ef4444;
    --warn:           #f59e0b;
    --text:           #e5e7eb;
    --muted:          #9ca3af;
    --dim:            #6b7280;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
    background: var(--surface);
    color: var(--text);
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }}

  #root-wrap {{
    position: relative;
    min-height: 100vh;
    background-color: var(--surface);
    background-image: radial-gradient(rgba(255,255,255,0.08) 1px, transparent 1px);
    background-size: 28px 28px;
  }}
  #root-wrap::before {{
    content: '';
    position: fixed;
    width: 600px; height: 600px;
    top: -180px; right: -180px;
    border-radius: 50%;
    filter: blur(120px);
    pointer-events: none;
    z-index: 0;
    background: radial-gradient(circle, rgba(99,102,241,0.35) 0%, transparent 70%);
  }}
  #root-wrap::after {{
    content: '';
    position: fixed;
    width: 500px; height: 500px;
    bottom: -150px; left: -150px;
    border-radius: 50%;
    filter: blur(120px);
    pointer-events: none;
    z-index: 0;
    background: radial-gradient(circle, rgba(20,184,166,0.28) 0%, transparent 70%);
  }}

  * {{
    scrollbar-width: thin;
    scrollbar-color: var(--surface-border) transparent;
  }}
  *::-webkit-scrollbar {{ width: 6px; height: 6px; }}
  *::-webkit-scrollbar-track {{ background: transparent; }}
  *::-webkit-scrollbar-thumb {{ background-color: var(--surface-border); border-radius: 3px; }}

  /* ── HERO ── */
  .hero {{
    position: relative;
    z-index: 1;
    padding: 48px 48px 40px;
    border-bottom: 1px solid var(--surface-border);
  }}
  .hero h1 {{
    font-size: 28px;
    font-weight: 600;
    color: #fff;
    letter-spacing: -0.5px;
    margin-bottom: 6px;
  }}
  .hero-sub {{
    color: var(--dim);
    font-size: 12px;
    margin-bottom: 28px;
  }}
  .chips {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 32px;
  }}
  .chip {{
    border: 1px solid rgba(99,102,241,0.4);
    color: var(--muted);
    background: rgba(99,102,241,0.08);
    padding: 4px 12px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.3px;
    white-space: nowrap;
  }}
  .counter {{
    display: flex;
    align-items: baseline;
    gap: 10px;
  }}
  .counter-num {{
    font-size: 56px;
    font-weight: 600;
    color: var(--accent);
    line-height: 1;
  }}
  .counter-label {{
    color: var(--muted);
    font-size: 14px;
  }}

  /* ── TOOLBAR ── */
  .toolbar {{
    position: relative;
    z-index: 10;
    background: rgba(26,29,39,0.9);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--surface-border);
    padding: 14px 48px;
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
    position: sticky;
    top: 0;
    z-index: 100;
  }}
  .toolbar input {{
    background: var(--surface);
    border: 1px solid var(--surface-border);
    color: var(--text);
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 13px;
    font-family: inherit;
    width: 220px;
    outline: none;
    transition: border-color 0.2s;
  }}
  .toolbar input::placeholder {{ color: var(--dim); }}
  .toolbar input:focus {{ border-color: var(--accent); }}
  .toolbar select {{
    background: var(--surface);
    border: 1px solid var(--surface-border);
    color: var(--text);
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 13px;
    font-family: inherit;
    cursor: pointer;
    outline: none;
  }}
  .toolbar-count {{
    margin-left: auto;
    font-size: 12px;
    color: var(--dim);
  }}

  /* ── GRID ── */
  .grid {{
    position: relative;
    z-index: 1;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 16px;
    padding: 28px 48px;
    align-items: start;
  }}

  /* ── CARD ── */
  .card {{
    background: var(--surface-card);
    border: 1px solid var(--surface-border);
    border-radius: 8px;
    overflow: hidden;
    transition: transform 0.18s, border-color 0.18s;
    cursor: pointer;
    text-decoration: none;
    color: inherit;
    display: flex;
    flex-direction: column;
  }}
  .card:hover {{
    transform: translateY(-2px);
    border-color: rgba(99,102,241,0.4);
  }}

  /* chart */
  .card-chart {{
    width: 100%;
    height: 220px;
    border-bottom: 1px solid var(--surface-border);
    overflow: hidden;
    background: #131722;
  }}
  .card-chart iframe {{
    width: 100%;
    height: 100%;
    border: none;
    display: block;
  }}

  /* stats */
  .stats-row {{
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    padding: 12px 16px 16px;
    gap: 0;
    border-bottom: 1px solid var(--surface-border);
  }}
  .stat {{
    padding: 6px 8px;
    border-left: 2px solid transparent;
  }}
  .stat:first-child {{ border-left: none; }}
  .stat-label {{
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--dim);
    margin-bottom: 3px;
  }}
  .stat-val {{
    font-size: 14px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }}
  .stat-green  {{ border-left-color: var(--gain);   }} .stat-green  .stat-val {{ color: var(--gain);   }}
  .stat-purple {{ border-left-color: var(--accent);  }} .stat-purple .stat-val {{ color: var(--accent);  }}
  .stat-blue   {{ border-left-color: #06b6d4;        }} .stat-blue   .stat-val {{ color: #06b6d4;        }}

  /* analyze btn */
  .card-footer {{
    margin-top: auto;
    padding: 12px 16px;
  }}
  .btn-analyze {{
    display: block;
    width: 100%;
    text-align: center;
    background: rgba(99,102,241,0.1);
    border: 1px solid rgba(99,102,241,0.3);
    color: var(--accent);
    padding: 9px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 500;
    font-family: inherit;
    letter-spacing: 0.3px;
    text-decoration: none;
    transition: background 0.15s, border-color 0.15s;
  }}
  .btn-analyze:hover {{ background: rgba(99,102,241,0.2); border-color: var(--accent); }}
  .btn-analyze.ready {{ background: rgba(34,197,94,0.1); border-color: rgba(34,197,94,0.3); color: var(--gain); }}
  .btn-analyze.ready:hover {{ background: rgba(34,197,94,0.2); border-color: var(--gain); }}

  .btn-analyze.loading {{ background: rgba(245,158,11,0.1); border-color: rgba(245,158,11,0.3); color: var(--warn); cursor: wait; }}

  /* ── FOOTER ── */
  footer {{
    position: relative;
    z-index: 1;
    border-top: 1px solid var(--surface-border);
    padding: 20px 48px;
    font-size: 11px;
    color: var(--dim);
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
  }}

  /* ── EMPTY STATE ── */
  .empty {{
    grid-column: 1/-1;
    text-align: center;
    padding: 80px 20px;
    color: var(--dim);
  }}
  .empty h2 {{ font-size: 18px; margin-bottom: 8px; color: var(--muted); }}

  .btn-refresh {{
    background: rgba(99,102,241,0.1);
    border: 1px solid rgba(99,102,241,0.3);
    color: var(--accent);
    padding: 8px 14px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
    letter-spacing: 0.3px;
    white-space: nowrap;
  }}
  .btn-refresh:hover {{ background: rgba(99,102,241,0.2); border-color: var(--accent); }}
  .btn-refresh:disabled {{ opacity: 0.5; cursor: wait; }}

  @media (max-width: 640px) {{
    .hero {{ padding: 28px 16px; }}
    .hero h1 {{ font-size: 20px; }}
    .counter-num {{ font-size: 40px; }}
    .toolbar {{ padding: 12px 16px; }}
    .toolbar input {{ width: 100%; }}
    .grid {{ padding: 16px; gap: 12px; }}
    footer {{ padding: 16px; flex-direction: column; }}
  }}
</style>
</head>
<body>
<div id="root-wrap">

<!-- HERO -->
<header class="hero">
  <h1>Screener · US Growth</h1>
  <p class="hero-sub">{ts_human} · via stockanalysis.com</p>
  <div class="chips">
    {chips_html}
  </div>
  <div class="counter">
    <span class="counter-num" id="matchCount">{count}</span>
    <span class="counter-label">stocks pass all 7 filters</span>
  </div>
</header>

<!-- TOOLBAR -->
<div class="toolbar">
  <input type="text" id="searchInput" placeholder="Search ticker or name…" oninput="filterAndSort()">
  <select id="sortSelect" onchange="filterAndSort()">
    <option value="market_cap">Market Cap ↓</option>
    <option value="revenue_growth">Revenue YoY ↓</option>
    <option value="eps_next_year">EPS Next Year ↓</option>
    <option value="avg_volume">Avg Volume ↓</option>
    <option value="high_52w_chg">Closest to 52W High</option>
    <option value="price">Price ↓</option>
    <option value="ticker">Ticker A–Z</option>
  </select>
  <button class="btn-refresh" id="btnRefresh" onclick="refreshScreener()">↺ Refresh Screener</button>
  <span class="toolbar-count" id="toolbarCount">Showing {count} of {count}</span>
</div>

<!-- GRID -->
<main class="grid" id="grid"></main>

<!-- FOOTER -->
<footer>
  <span>US Growth Screener · Generated {ts_human}</span>
  <span>Source: stockanalysis.com · Click a card to analyze</span>
</footer>

<script>
const RAW = {stocks_json};
const ANALYSIS_PAGE = 'analysis.html';
const SERVER = window.BACKEND_URL || 'http://localhost:8765';

function refreshScreener() {{
  const btn = document.getElementById('btnRefresh');
  if (btn) {{ btn.disabled = true; btn.textContent = '↺ Refreshing…'; }}

  fetch(`${{SERVER}}/refresh-screener`)
    .then(r => r.json())
    .then(() => pollScreener())
    .catch(() => {{
      if (btn) {{ btn.disabled = false; btn.textContent = '↺ Refresh Screener'; }}
      alert('Server not reachable — make sure start.py is running.');
    }});
}}

function pollScreener() {{
  const btn = document.getElementById('btnRefresh');
  const interval = setInterval(() => {{
    fetch(`${{SERVER}}/screener-status`)
      .then(r => r.json())
      .then(data => {{
        if (!data.running) {{
          clearInterval(interval);
          window.location.reload();
        }}
      }})
      .catch(() => clearInterval(interval));
  }}, 3000);
}}

function parsePct(s) {{
  if (!s || s === 'N/A') return -999;
  return parseFloat(s.replace(/[^\\d.\\-]/g, '')) || 0;
}}

function parseNum(s) {{
  if (!s || s === 'N/A') return 0;
  const m = s.match(/([\\.\\d]+)([BTM]?)/);
  if (!m) return 0;
  const n = parseFloat(m[1]);
  const u = m[2];
  if (u === 'T') return n * 1e12;
  if (u === 'B') return n * 1e9;
  if (u === 'M') return n * 1e6;
  return n;
}}

function badge52w(val) {{
  const n = parsePct(val);
  if (n >= -5)  return ['badge-green',  val];
  if (n >= -12) return ['badge-orange', val];
  return ['badge-red', val];
}}

function renderCard(s) {{
  const exchange = s.exchange || 'NASDAQ';
  const tvSymbol = `${{exchange}}:${{s.ticker}}`;
  const chartParams = encodeURIComponent(JSON.stringify({{
    symbol: tvSymbol,
    dateRange: "1M",
    colorTheme: "dark",
    isTransparent: true,
    autosize: true,
    chartType: "candlesticks",
    largeChartUrl: "",
  }}));

  return `<div class="card" onclick="handleCardClick('${{s.ticker}}')">
    <div class="card-chart">
      <iframe
        data-src="https://s.tradingview.com/embed-widget/mini-symbol-overview/?locale=en#${{chartParams}}"
        title="Chart ${{s.ticker}}"
      ></iframe>
    </div>
    <div class="stats-row">
      <div class="stat stat-green">
        <div class="stat-label">Revenue YoY</div>
        <div class="stat-val">${{s.revenue_growth}}</div>
      </div>
      <div class="stat stat-purple">
        <div class="stat-label">EPS Next Yr</div>
        <div class="stat-val">${{s.eps_next_year}}</div>
      </div>
      <div class="stat stat-blue">
        <div class="stat-label">Avg Volume</div>
        <div class="stat-val">${{s.avg_volume}}</div>
      </div>
    </div>
    <div class="card-footer">
      <span class="btn-analyze" id="btn-${{s.ticker}}">&#x23F3; Analyze ${{s.ticker}}</span>
    </div>
  </div>`;
}}

function handleCardClick(ticker) {{
  window.open(`${{ANALYSIS_PAGE}}?ticker=${{ticker}}`, '_blank');
}}

function filterAndSort() {{
  const q    = document.getElementById('searchInput').value.toLowerCase();
  const sort = document.getElementById('sortSelect').value;

  let filtered = RAW.filter(s =>
    s.ticker.toLowerCase().includes(q) ||
    s.name.toLowerCase().includes(q)
  );

  filtered.sort((a, b) => {{
    if (sort === 'ticker')        return a.ticker.localeCompare(b.ticker);
    if (sort === 'market_cap')    return parseNum(b.market_cap) - parseNum(a.market_cap);
    if (sort === 'price')         return parsePct(b.price) - parsePct(a.price);
    if (sort === 'revenue_growth')return parsePct(b.revenue_growth) - parsePct(a.revenue_growth);
    if (sort === 'eps_next_year') return parsePct(b.eps_next_year) - parsePct(a.eps_next_year);
    if (sort === 'avg_volume')    return parseNum(b.avg_volume) - parseNum(a.avg_volume);
    if (sort === 'high_52w_chg')  return parsePct(b.high_52w_chg) - parsePct(a.high_52w_chg);
    return 0;
  }});

  const grid = document.getElementById('grid');
  document.getElementById('toolbarCount').textContent =
    `Showing ${{filtered.length}} of ${{RAW.length}}`;
  document.getElementById('matchCount').textContent = RAW.length;

  if (filtered.length === 0) {{
    grid.innerHTML = `<div class="empty"><h2>No results</h2><p>Try a different search.</p></div>`;
    return;
  }}
  grid.innerHTML = filtered.map(s => renderCard(s)).join('');
  observeCharts();
}}

// Load charts sequentially as cards scroll into view, with a small gap
// between each to avoid saturating TradingView with simultaneous requests.
let chartQueue = [];
let chartLoading = false;

function drainChartQueue() {{
  if (chartLoading || chartQueue.length === 0) return;
  chartLoading = true;
  const iframe = chartQueue.shift();
  if (iframe && iframe.dataset.src) {{
    iframe.src = iframe.dataset.src;
    delete iframe.dataset.src;
  }}
  setTimeout(() => {{
    chartLoading = false;
    drainChartQueue();
  }}, 300);
}}

const chartObserver = new IntersectionObserver((entries) => {{
  entries.forEach(entry => {{
    if (entry.isIntersecting) {{
      const iframe = entry.target;
      chartObserver.unobserve(iframe);
      chartQueue.push(iframe);
      drainChartQueue();
    }}
  }});
}}, {{ rootMargin: '200px' }});

function observeCharts() {{
  document.querySelectorAll('iframe[data-src]').forEach(el => chartObserver.observe(el));
}}

// Init
filterAndSort();
</script>
</div>
</body>
</html>"""
