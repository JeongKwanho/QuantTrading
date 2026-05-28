"""
Fair Value Gap detection visualization.

Run:
    python tools/fvg_chart.py
"""

import asyncio
import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from binance import AsyncClient

from detection.fair_value_gap import FairValueGapPattern
from strategies.base import MarketData

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")


SYMBOL = "BTCUSDT"
TREND_WINDOW = 8
PIVOT_K = 2
MIN_GAP_SIZE = 0.0
MIDDLE_RANGE_MULTIPLIER = 1.2
MIN_GAP_PCT_BY_TF = {
    "5m": 0.0010,
    "15m": 0.0012,
    "1h": 0.0015,
    "4h": 0.0020,
    "1d": 0.0030,
}

INTERVAL_SECONDS = {
    "1d": 86400,
    "4h": 14400,
    "1h": 3600,
    "15m": 900,
    "5m": 300,
}

SCENARIOS = [
    {"label": "5m", "tf": "5m", "n": 3000},
    {"label": "15m", "tf": "15m", "n": 3000},
    {"label": "1h", "tf": "1h", "n": 3000},
    {"label": "4h", "tf": "4h", "n": 3000},
    {"label": "1d", "tf": "1d", "n": 2000},
]


async def fetch_candles(client, symbol, interval, n):
    now_ts = int(datetime.utcnow().timestamp() * 1000)
    start_ts = now_ts - n * INTERVAL_SECONDS[interval] * 1000
    candles: list[dict] = []

    while start_ts < now_ts and len(candles) < n:
        raw = await client.futures_klines(
            symbol=symbol,
            interval=interval,
            startTime=start_ts,
            endTime=now_ts,
            limit=1500,
        )
        if not raw:
            break
        for k in raw:
            candles.append({
                "timestamp": datetime.utcfromtimestamp(k[0] / 1000),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        start_ts = raw[-1][0] + 1

    return candles[:n]


def run_detection(candles: list[dict], min_gap_pct: float) -> tuple[list[dict], list[dict]]:
    pattern = FairValueGapPattern(
        trend_window=TREND_WINDOW,
        pivot_k=PIVOT_K,
        min_gap_size=MIN_GAP_SIZE,
        min_gap_pct=min_gap_pct,
        middle_range_multiplier=MIDDLE_RANGE_MULTIPLIER,
    )

    candle_records: list[dict] = []
    fvg_zones: list[dict] = []

    for candle in candles:
        data = MarketData(
            symbol=SYMBOL,
            timestamp=candle["timestamp"],
            open=candle["open"],
            high=candle["high"],
            low=candle["low"],
            close=candle["close"],
            volume=candle["volume"],
        )
        pattern.evaluate(data)

        candle_records.append({
            "ts": candle["timestamp"].strftime("%Y-%m-%dT%H:%M:%S"),
            "o": candle["open"],
            "h": candle["high"],
            "l": candle["low"],
            "c": candle["close"],
        })

        if pattern.last_fvg is not None:
            fvg = pattern.last_fvg
            fvg_zones.append({
                "direction": fvg.direction,
                "trend": fvg.trend,
                "ts_start": fvg.start_timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
                "ts_mid": fvg.middle_timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
                "ts_end": fvg.end_timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
                "lower": fvg.lower,
                "upper": fvg.upper,
                "gap_size": fvg.gap_size,
                "gap_pct": fvg.gap_pct,
                "middle_range": fvg.middle_range,
                "side_range_max": fvg.side_range_max,
            })

    return candle_records, fvg_zones


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Fair Value Gap Detection - BTCUSDT</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #17181c; color: #e7e9ee; font-family: "Segoe UI", sans-serif; height: 100vh; display: flex; flex-direction: column; }
#header { padding: 8px 12px; background: #111217; border-bottom: 1px solid #2d3038; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
#header h2 { font-size: 13px; color: #e7e9ee; white-space: nowrap; font-weight: 600; }
.tabs, .filters { display: flex; gap: 4px; flex-wrap: wrap; }
.tab, .filter { padding: 5px 12px; background: #2d3038; border: none; color: #e7e9ee; cursor: pointer; border-radius: 4px; font-size: 12px; }
.tab:hover, .filter:hover { background: #3f434e; }
.tab.active, .filter.active { background: #55b7a8; color: #101318; font-weight: 600; }
#stats { margin-left: auto; font-size: 12px; color: #aeb6c2; white-space: nowrap; }
#chart { flex: 1; min-height: 0; }
</style>
</head>
<body>
<div id="header">
  <h2>BTCUSDT FVG Detection | TF-specific gap pct | trend=__TREND_WINDOW__ | middle_x=__MIDDLE_RANGE_MULTIPLIER__</h2>
  <div class="tabs" id="tabs"></div>
  <div class="filters">
    <button class="filter active" data-filter="all">All</button>
    <button class="filter" data-filter="bullish">Bullish</button>
    <button class="filter" data-filter="bearish">Bearish</button>
  </div>
  <div id="stats"></div>
</div>
<div id="chart"></div>

<script>
const ALL = __ALL_DATA__;
const VIEW = { key: null, filter: 'all' };

function selectedZones(d) {
  if (VIEW.filter === 'all') return d.fvg_zones;
  return d.fvg_zones.filter(z => z.direction === VIEW.filter);
}

function renderChart(key) {
  VIEW.key = key;
  const d = ALL[key];
  const zones = selectedZones(d);

  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tf === key));
  document.querySelectorAll('.filter').forEach(t => t.classList.toggle('active', t.dataset.filter === VIEW.filter));

  const bullish = d.fvg_zones.filter(z => z.direction === 'bullish').length;
  const bearish = d.fvg_zones.filter(z => z.direction === 'bearish').length;
  document.getElementById('stats').textContent =
    `min_gap_pct ${(d.min_gap_pct * 100).toFixed(2)}% | FVG ${d.fvg_zones.length} | bullish ${bullish} | bearish ${bearish} | shown ${zones.length}`;

  const traces = [{
    type: 'candlestick',
    name: 'Price',
    x: d.ts,
    open: d.o,
    high: d.h,
    low: d.l,
    close: d.c,
    increasing: { line: { color: '#2fb39f', width: 1 }, fillcolor: '#2fb39f' },
    decreasing: { line: { color: '#e05d5d', width: 1 }, fillcolor: '#e05d5d' },
    whiskerwidth: 0.3,
  }];

  const bx = [], by = [], bText = [];
  const sx = [], sy = [], sText = [];

  for (const z of zones) {
    const mid = (z.lower + z.upper) / 2;
    const label = `${z.direction} FVG<br>prior trend=${z.trend}<br>gap=${z.gap_size.toFixed(2)}<br>gap_pct=${(z.gap_pct * 100).toFixed(3)}%<br>middle_range=${z.middle_range.toFixed(2)}<br>side_max=${z.side_range_max.toFixed(2)}`;
    if (z.direction === 'bullish') {
      bx.push(z.ts_mid);
      by.push(mid);
      bText.push(label);
    } else {
      sx.push(z.ts_mid);
      sy.push(mid);
      sText.push(label);
    }
  }

  if (bx.length) traces.push({
    type: 'scatter',
    mode: 'markers',
    name: 'Bullish FVG',
    x: bx,
    y: by,
    text: bText,
    hovertemplate: '%{text}<extra></extra>',
    marker: { symbol: 'triangle-up', size: 9, color: '#55b7a8', line: { color: '#111217', width: 1 } },
  });

  if (sx.length) traces.push({
    type: 'scatter',
    mode: 'markers',
    name: 'Bearish FVG',
    x: sx,
    y: sy,
    text: sText,
    hovertemplate: '%{text}<extra></extra>',
    marker: { symbol: 'triangle-down', size: 9, color: '#d98b54', line: { color: '#111217', width: 1 } },
  });

  const shapes = [];
  for (const z of zones) {
    const bullishZone = z.direction === 'bullish';
    shapes.push({
      type: 'rect',
      xref: 'x',
      yref: 'y',
      x0: z.ts_start,
      x1: z.ts_end,
      y0: z.lower,
      y1: z.upper,
      fillcolor: bullishZone ? 'rgba(85,183,168,0.24)' : 'rgba(217,139,84,0.24)',
      line: {
        color: bullishZone ? 'rgba(85,183,168,0.8)' : 'rgba(217,139,84,0.8)',
        width: 1,
      },
    });
  }

  const xEnd = d.ts[d.ts.length - 1];
  const xStart = d.ts[Math.max(0, d.ts.length - 500)];

  const layout = {
    paper_bgcolor: '#17181c',
    plot_bgcolor: '#111217',
    font: { color: '#e7e9ee', size: 11 },
    margin: { l: 58, r: 22, t: 10, b: 10 },
    xaxis: {
      type: 'date',
      range: [xStart, xEnd],
      rangeslider: { visible: true, bgcolor: '#111217', thickness: 0.04 },
      showgrid: true,
      gridcolor: '#2d3038',
    },
    yaxis: {
      showgrid: true,
      gridcolor: '#2d3038',
      autorange: true,
      fixedrange: false,
      side: 'right',
    },
    shapes,
    legend: {
      bgcolor: 'rgba(23,24,28,0.88)',
      bordercolor: '#3f434e',
      borderwidth: 1,
      font: { size: 11 },
      orientation: 'h',
      y: 1.02,
      x: 0,
    },
    hovermode: 'x unified',
    hoverlabel: { bgcolor: '#2d3038', bordercolor: '#3f434e', font: { color: '#e7e9ee', size: 11 } },
  };

  Plotly.react('chart', traces, layout, {
    responsive: true,
    displaylogo: false,
    modeBarButtonsToRemove: ['lasso2d', 'select2d'],
  });
}

const tabsEl = document.getElementById('tabs');
Object.keys(ALL).forEach((tf, i) => {
  const btn = document.createElement('button');
  btn.className = 'tab' + (i === 0 ? ' active' : '');
  btn.dataset.tf = tf;
  btn.textContent = tf;
  btn.onclick = () => renderChart(tf);
  tabsEl.appendChild(btn);
});

document.querySelectorAll('.filter').forEach(btn => {
  btn.onclick = () => {
    VIEW.filter = btn.dataset.filter;
    renderChart(VIEW.key);
  };
});

renderChart(Object.keys(ALL)[0]);
</script>
</body>
</html>
"""


async def main() -> None:
    print("Connecting Binance ...")
    client = await AsyncClient.create()

    all_data: dict[str, dict] = {}
    try:
        for sc in SCENARIOS:
            label, tf, n = sc["label"], sc["tf"], sc["n"]
            min_gap_pct = MIN_GAP_PCT_BY_TF[tf]
            print(f"  [{label}] fetching {n} x {tf} candles (min_gap_pct={min_gap_pct:.4f}) ...", end=" ", flush=True)
            candles = await fetch_candles(client, SYMBOL, tf, n)
            records, fvg_zones = run_detection(candles, min_gap_pct)
            bullish = len([z for z in fvg_zones if z["direction"] == "bullish"])
            bearish = len([z for z in fvg_zones if z["direction"] == "bearish"])
            print(f"{len(candles)} candles | bullish {bullish} | bearish {bearish}")
            all_data[label] = {
                "ts": [r["ts"] for r in records],
                "o": [r["o"] for r in records],
                "h": [r["h"] for r in records],
                "l": [r["l"] for r in records],
                "c": [r["c"] for r in records],
                "fvg_zones": fvg_zones,
                "min_gap_pct": min_gap_pct,
            }
    finally:
        await client.close_connection()

    html = (
        HTML_TEMPLATE
        .replace("__ALL_DATA__", json.dumps(all_data, ensure_ascii=False, separators=(",", ":")))
        .replace("__TREND_WINDOW__", str(TREND_WINDOW))
        .replace("__MIDDLE_RANGE_MULTIPLIER__", str(MIDDLE_RANGE_MULTIPLIER))
    )

    out = Path(__file__).parent / "fvg_result.html"
    out.write_text(html, encoding="utf-8")
    print(f"\nSaved - {out}")
    webbrowser.open(out.as_uri())


if __name__ == "__main__":
    asyncio.run(main())
