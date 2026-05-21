"""
Order Block detection visualization.
python tools/ob_chart.py
"""

import asyncio
import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from binance import AsyncClient

from patterns.order_block import OrderBlockPattern
from strategies.base import MarketData

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# ── CONFIG ────────────────────────────────────────────────────────────────────
SYMBOL  = "BTCUSDT"
WINDOW  = 10
PIVOT_K = 2

INTERVAL_SECONDS = {
    "1d": 86400, "4h": 14400, "1h": 3600, "15m": 900, "5m": 300,
}

SCENARIOS = [
    {"label": "5m",  "tf": "5m",  "n": 3000},
    {"label": "15m", "tf": "15m", "n": 3000},
    {"label": "1h",  "tf": "1h",  "n": 3000},
    {"label": "4h",  "tf": "4h",  "n": 3000},
    {"label": "1d",  "tf": "1d",  "n": 2000},
]
# ─────────────────────────────────────────────────────────────────────────────


async def fetch_candles(client, symbol, interval, n):
    now_ts   = int(datetime.utcnow().timestamp() * 1000)
    start_ts = now_ts - n * INTERVAL_SECONDS[interval] * 1000
    candles: list[dict] = []
    while start_ts < now_ts and len(candles) < n:
        raw = await client.futures_klines(
            symbol=symbol, interval=interval,
            startTime=start_ts, endTime=now_ts, limit=1500,
        )
        if not raw:
            break
        for k in raw:
            candles.append({
                "timestamp": datetime.utcfromtimestamp(k[0] / 1000),
                "open":  float(k[1]), "high": float(k[2]),
                "low":   float(k[3]), "close": float(k[4]),
                "volume": float(k[5]),
            })
        start_ts = raw[-1][0] + 1
    return candles[:n]


def run_detection(candles: list[dict]) -> tuple[list, list, list]:
    """
    Returns:
      candle_records : OHLC list
      ob_zones       : detected OB zones (box from ob_close to ob_open)
      pivot_markers  : pivot high markers used for downtrend confirmation
    """
    pattern = OrderBlockPattern(window=WINDOW, pivot_k=PIVOT_K)

    candle_records: list[dict] = []
    ob_zones:       list[dict] = []
    pivot_markers:  list[dict] = []

    prev_ob_ts = None

    for candle in candles:
        ts_str = candle["timestamp"].strftime("%Y-%m-%dT%H:%M:%S")
        data = MarketData(
            symbol="BTCUSDT", timestamp=candle["timestamp"],
            open=candle["open"], high=candle["high"],
            low=candle["low"],   close=candle["close"],
            volume=candle["volume"],
        )
        pattern.evaluate(data)
        ob = pattern.bullish_ob

        candle_records.append({
            "ts": ts_str,
            "o":  candle["open"],
            "h":  candle["high"],
            "l":  candle["low"],
            "c":  candle["close"],
        })

        if ob is not None:
            # 새로운 OB 감지
            if ob.timestamp != prev_ob_ts:
                # 이전 OB 존 닫기
                if ob_zones and ob_zones[-1]["ts_end"] is None:
                    ob_zones[-1]["ts_end"] = ts_str

                ob_zones.append({
                    "ts_start": ob.timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
                    "ts_end":   None,
                    "ob_open":  ob.ob_open,
                    "ob_close": ob.ob_close,
                    "ob_high":  ob.ob_high,
                    "ob_low":   ob.ob_low,
                    "invalidated": False,
                })
                prev_ob_ts = ob.timestamp

                # 이 시점의 피벗 고점 2개 기록 (하락 구조 확인용)
                ph = pattern._find_pivots(is_low=False, use_trend_window=True)
                if len(ph) >= 2:
                    sp = sorted(ph)[-2:]
                    hist = pattern._history
                    for idx in sp:
                        pivot_markers.append({
                            "ts":    hist[idx].timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
                            "price": hist[idx].high,
                        })

            # OB 무효화 감지
            if not ob.valid and ob_zones and not ob_zones[-1]["invalidated"]:
                ob_zones[-1]["ts_end"]     = ts_str
                ob_zones[-1]["invalidated"] = True
                prev_ob_ts = None

        else:
            # 패턴이 OB 없음 → 열린 존 닫기
            if ob_zones and ob_zones[-1]["ts_end"] is None:
                ob_zones[-1]["ts_end"] = ts_str
            prev_ob_ts = None

    # 마지막 열린 존 닫기
    if ob_zones and ob_zones[-1]["ts_end"] is None:
        ob_zones[-1]["ts_end"] = candle_records[-1]["ts"]

    return candle_records, ob_zones, pivot_markers


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Order Block Detection — BTCUSDT</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #1e1e2e; color: #cdd6f4; font-family: 'Segoe UI', sans-serif; height: 100vh; display: flex; flex-direction: column; }
#header { padding: 8px 12px; background: #181825; border-bottom: 1px solid #313244; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
#header h2 { font-size: 13px; color: #a6e3a1; white-space: nowrap; }
.tabs { display: flex; gap: 4px; }
.tab { padding: 5px 14px; background: #313244; border: none; color: #cdd6f4; cursor: pointer; border-radius: 4px; font-size: 13px; transition: background .15s; }
.tab:hover { background: #45475a; }
.tab.active { background: #a6e3a1; color: #1e1e2e; font-weight: 600; }
#stats { margin-left: auto; font-size: 12px; color: #a6adc8; white-space: nowrap; }
#chart { flex: 1; min-height: 0; }
</style>
</head>
<body>
<div id="header">
  <h2>BTCUSDT · Order Block Detection · window=__WINDOW__ pivot_k=__PIVOT_K__</h2>
  <div class="tabs" id="tabs"></div>
  <div id="stats"></div>
</div>
<div id="chart"></div>

<script>
const ALL = __ALL_DATA__;

function renderChart(key) {
  const d = ALL[key];
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tf === key));
  const nValid   = d.ob_zones.filter(z => !z.invalidated).length;
  const nInvalid = d.ob_zones.filter(z => z.invalidated).length;
  document.getElementById('stats').textContent =
    `OB 감지 ${d.ob_zones.length}건  유효 ${nValid}  무효화 ${nInvalid}`;

  const traces = [];

  /* ── 캔들스틱 ── */
  traces.push({
    type: 'candlestick', name: 'Price',
    x: d.ts, open: d.o, high: d.h, low: d.l, close: d.c,
    increasing: { line: { color: '#26a69a', width: 1 }, fillcolor: '#26a69a' },
    decreasing: { line: { color: '#ef5350', width: 1 }, fillcolor: '#ef5350' },
    whiskerwidth: 0.3,
  });

  /* ── 피벗 고점 마커 ── */
  if (d.pivot_markers.length) {
    traces.push({
      type: 'scatter', mode: 'markers', name: '피벗 고점',
      x: d.pivot_markers.map(p => p.ts),
      y: d.pivot_markers.map(p => p.price),
      marker: { symbol: 'triangle-down', size: 10, color: '#f38ba8',
                line: { color: '#1e1e2e', width: 1 } },
    });
  }

  /* ── OB 존 사각형 (유효한 것만) ── */
  const shapes = [];
  for (const z of d.ob_zones) {
    if (z.invalidated) continue;
    const color = 'rgba(166,227,161,0.20)';
    const border = 'rgba(166,227,161,0.7)';

    shapes.push({
      type: 'rect', xref: 'x', yref: 'y',
      x0: z.ts_start, x1: z.ts_end,
      y0: z.ob_close, y1: z.ob_open,
      fillcolor: color,
      line: { color: border, width: 1 },
    });
  }

  /* ── 기본 표시 범위: 마지막 500봉 ── */
  const xEnd   = d.ts[d.ts.length - 1];
  const xStart = d.ts[Math.max(0, d.ts.length - 500)];

  const layout = {
    paper_bgcolor: '#1e1e2e', plot_bgcolor: '#181825',
    font: { color: '#cdd6f4', size: 11 },
    margin: { l: 60, r: 20, t: 10, b: 10 },
    xaxis: {
      type: 'date',
      range: [xStart, xEnd],
      rangeslider: { visible: true, bgcolor: '#181825', thickness: 0.04 },
      showgrid: true, gridcolor: '#313244',
    },
    yaxis: {
      showgrid: true, gridcolor: '#313244',
      autorange: true, fixedrange: false, side: 'right',
    },
    shapes,
    legend: {
      bgcolor: 'rgba(30,30,46,0.85)', bordercolor: '#45475a', borderwidth: 1,
      font: { size: 11 }, orientation: 'h', y: 1.02, x: 0,
    },
    hovermode: 'x unified',
    hoverlabel: { bgcolor: '#313244', bordercolor: '#45475a', font: { color: '#cdd6f4', size: 11 } },
  };

  Plotly.react('chart', traces, layout, { responsive: true, displaylogo: false,
    modeBarButtonsToRemove: ['lasso2d','select2d'] });
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
            print(f"  [{label}] fetching {n} x {tf} candles ...", end=" ", flush=True)
            candles = await fetch_candles(client, SYMBOL, tf, n)
            records, ob_zones, pivot_markers = run_detection(candles)
            print(f"{len(candles)} candles  |  OB {len(ob_zones)}건 감지")
            all_data[label] = {
                "ts": [r["ts"] for r in records],
                "o":  [r["o"]  for r in records],
                "h":  [r["h"]  for r in records],
                "l":  [r["l"]  for r in records],
                "c":  [r["c"]  for r in records],
                "ob_zones":      ob_zones,
                "pivot_markers": pivot_markers,
            }
    finally:
        await client.close_connection()

    html = (
        HTML_TEMPLATE
        .replace("__ALL_DATA__", json.dumps(all_data, ensure_ascii=False, separators=(",", ":")))
        .replace("__WINDOW__",   str(WINDOW))
        .replace("__PIVOT_K__",  str(PIVOT_K))
    )

    out = Path(__file__).parent / "ob_result.html"
    out.write_text(html, encoding="utf-8")
    print(f"\nSaved → {out}")
    webbrowser.open(out.as_uri())


if __name__ == "__main__":
    asyncio.run(main())
