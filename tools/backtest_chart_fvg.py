"""
Interactive FVGChannelV1 backtest chart.

Run:
    python tools/backtest_chart_fvg.py
"""

import asyncio
import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from binance import AsyncClient

from backend.broker.base import OrderSide, Signal as BrokerSignal
from broker.mock import MockBroker
from detection.fair_value_gap import FairValueGapPattern
from strategies.base import FillEvent, MarketData
from strategies.fvg_channel_v1 import FVGChannelV1

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")


SYMBOL = "BTCUSDT"
N_CANDLES = 3000
INITIAL_BALANCE = 10_000.0
LEVERAGE = 1
TREND_WINDOW = 8
PIVOT_K = 2
MIDDLE_RANGE_MULTIPLIER = 1.2
LIQUIDITY_LOOKBACK = 20
SETUP_EXPIRY_BARS = 80

MIN_GAP_PCT_BY_TF = {
    "5m": 0.0010,
    "15m": 0.0012,
    "1h": 0.0015,
    "4h": 0.0020,
    "1d": 0.0030,
}

SCENARIOS = [
    {"label": "5m", "tf": "5m"},
    {"label": "15m", "tf": "15m"},
    {"label": "1h", "tf": "1h"},
    {"label": "4h", "tf": "4h"},
    {"label": "1d", "tf": "1d", "n": 2000},
]

INTERVAL_SECONDS = {
    "1d": 86400,
    "4h": 14400,
    "1h": 3600,
    "15m": 900,
    "5m": 300,
}

ENTRY_REASONS = {
    "fvg_long_entry1",
    "fvg_long_entry2",
    "fvg_long_entry3",
    "fvg_short_entry1",
    "fvg_short_entry2",
    "fvg_short_entry3",
}


async def fetch_candles(client: AsyncClient, symbol: str, interval: str, n: int) -> list[dict]:
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
        for kline in raw:
            candles.append({
                "timestamp": datetime.utcfromtimestamp(kline[0] / 1000),
                "open": float(kline[1]),
                "high": float(kline[2]),
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5]),
            })
        start_ts = raw[-1][0] + 1

    return candles[:n]


async def run_chart_data(candles: list[dict], min_gap_pct: float) -> dict:
    detector = FairValueGapPattern(
        trend_window=TREND_WINDOW,
        pivot_k=PIVOT_K,
        min_gap_pct=min_gap_pct,
        middle_range_multiplier=MIDDLE_RANGE_MULTIPLIER,
    )
    strategy = FVGChannelV1(
        leverage=LEVERAGE,
        trend_window=TREND_WINDOW,
        pivot_k=PIVOT_K,
        min_gap_pct=min_gap_pct,
        middle_range_multiplier=MIDDLE_RANGE_MULTIPLIER,
        liquidity_lookback=LIQUIDITY_LOOKBACK,
        setup_expiry_bars=SETUP_EXPIRY_BARS,
    )
    broker = MockBroker(initial_balance=INITIAL_BALANCE, leverage=LEVERAGE)

    def on_fill(fill) -> None:
        strategy.on_fill(FillEvent(
            order_id=fill.order_id,
            symbol=fill.symbol,
            direction=fill.side.value,
            price=fill.price,
            quantity=fill.quantity,
            fee=fill.fee,
            timestamp=fill.timestamp,
        ))

    broker.set_on_fill(on_fill)

    records: list[dict] = []
    zones: list[dict] = []
    trades: list[dict] = []
    equity: list[dict] = []
    trade_base_equity: float | None = None

    for candle in candles:
        ts = candle["timestamp"].strftime("%Y-%m-%dT%H:%M:%S")
        data = MarketData(
            symbol=SYMBOL,
            timestamp=candle["timestamp"],
            open=candle["open"],
            high=candle["high"],
            low=candle["low"],
            close=candle["close"],
            volume=candle["volume"],
        )

        detector.evaluate(data)
        if detector.last_fvg is not None:
            fvg = detector.last_fvg
            zones.append({
                "direction": fvg.direction,
                "x0": fvg.start_timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
                "x1": fvg.end_timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
                "lower": fvg.lower,
                "upper": fvg.upper,
                "gap_size": fvg.gap_size,
                "gap_pct": fvg.gap_pct,
            })

        broker.update_price(SYMBOL, candle["close"])
        for sig in strategy.on_data(data):
            reason = sig.metadata.get("reason", "")
            qty = sig.quantity
            if reason in ENTRY_REASONS and qty == 0.0:
                if reason.endswith("entry1") or trade_base_equity is None:
                    trade_base_equity = broker.get_total_equity()
                fraction = sig.metadata.get("fraction", 1.0)
                entry_price = sig.metadata.get("entry_price", candle["close"])
                qty = (trade_base_equity * fraction) / entry_price
            if qty <= 0.0:
                continue

            override_price = None
            if reason in ENTRY_REASONS:
                override_price = sig.metadata.get("entry_price")
            elif reason in ("stop_loss", "tp1", "tp2", "sl2"):
                override_price = sig.metadata.get("exit_price")

            if override_price is not None:
                broker._current_prices[sig.symbol] = override_price

            order = await broker.place_order(BrokerSignal(
                symbol=sig.symbol,
                direction=OrderSide(sig.direction),
                quantity=qty,
                price=None,
                strategy_name=sig.strategy_name,
                timestamp=sig.timestamp,
            ))

            if override_price is not None:
                broker._current_prices[sig.symbol] = candle["close"]

            fill_price = order.avg_fill_price if order.avg_fill_price is not None else candle["close"]
            trades.append({
                "ts": ts,
                "direction": sig.direction,
                "price": fill_price,
                "qty": qty,
                "reason": reason,
                "equity": broker.get_total_equity(),
            })

            if reason in ("stop_loss", "tp2", "sl2"):
                trade_base_equity = None

        records.append({
            "ts": ts,
            "o": candle["open"],
            "h": candle["high"],
            "l": candle["low"],
            "c": candle["close"],
        })
        equity.append({"ts": ts, "value": broker.get_total_equity()})

    final = broker.get_total_equity()
    return {
        "ts": [r["ts"] for r in records],
        "o": [r["o"] for r in records],
        "h": [r["h"] for r in records],
        "l": [r["l"] for r in records],
        "c": [r["c"] for r in records],
        "zones": zones,
        "trades": trades,
        "equity_ts": [e["ts"] for e in equity],
        "equity": [e["value"] for e in equity],
        "return_pct": (final - INITIAL_BALANCE) / INITIAL_BALANCE * 100,
        "min_gap_pct": min_gap_pct,
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>FVGChannelV1 Backtest - BTCUSDT</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #17181c; color: #e7e9ee; font-family: "Segoe UI", sans-serif; height: 100vh; display: flex; flex-direction: column; }
#header { padding: 8px 12px; background: #111217; border-bottom: 1px solid #2d3038; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
#header h2 { font-size: 13px; font-weight: 600; white-space: nowrap; }
.tabs, .filters { display: flex; gap: 4px; flex-wrap: wrap; }
.tab, .filter { padding: 5px 11px; background: #2d3038; border: none; color: #e7e9ee; cursor: pointer; border-radius: 4px; font-size: 12px; }
.tab:hover, .filter:hover { background: #3f434e; }
.tab.active, .filter.active { background: #55b7a8; color: #101318; font-weight: 600; }
#stats { margin-left: auto; font-size: 12px; color: #aeb6c2; white-space: nowrap; }
#chart { flex: 1; min-height: 0; }
</style>
</head>
<body>
<div id="header">
  <h2>BTCUSDT FVGChannelV1</h2>
  <div class="tabs" id="tabs"></div>
  <div class="filters">
    <button class="filter active" data-filter="all">All</button>
    <button class="filter" data-filter="entry">Entries</button>
    <button class="filter" data-filter="exit">Exits</button>
  </div>
  <div id="stats"></div>
</div>
<div id="chart"></div>
<script>
const ALL = __ALL_DATA__;
const VIEW = { key: null, filter: 'all' };
const ENTRY_REASONS = new Set([
  'fvg_long_entry1', 'fvg_long_entry2', 'fvg_long_entry3',
  'fvg_short_entry1', 'fvg_short_entry2', 'fvg_short_entry3'
]);

function visibleTrades(d) {
  if (VIEW.filter === 'all') return d.trades;
  if (VIEW.filter === 'entry') return d.trades.filter(t => ENTRY_REASONS.has(t.reason));
  return d.trades.filter(t => !ENTRY_REASONS.has(t.reason));
}

function renderChart(key) {
  VIEW.key = key;
  const d = ALL[key];
  const trades = visibleTrades(d);
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tf === key));
  document.querySelectorAll('.filter').forEach(t => t.classList.toggle('active', t.dataset.filter === VIEW.filter));

  const entries = d.trades.filter(t => ENTRY_REASONS.has(t.reason)).length;
  const exits = d.trades.length - entries;
  document.getElementById('stats').textContent =
    `gap ${(d.min_gap_pct * 100).toFixed(2)}% | return ${d.return_pct.toFixed(2)}% | trades ${d.trades.length} | entries ${entries} | exits ${exits}`;

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
    yaxis: 'y',
  }, {
    type: 'scatter',
    mode: 'lines',
    name: 'Equity',
    x: d.equity_ts,
    y: d.equity,
    line: { color: '#8db3ff', width: 1.4 },
    yaxis: 'y2',
  }];

  const groups = [
    { name: 'Long Entries', test: t => t.reason.startsWith('fvg_long'), color: '#55b7a8', symbol: 'triangle-up' },
    { name: 'Short Entries', test: t => t.reason.startsWith('fvg_short'), color: '#d98b54', symbol: 'triangle-down' },
    { name: 'TP1', test: t => t.reason === 'tp1', color: '#8db3ff', symbol: 'circle' },
    { name: 'TP2', test: t => t.reason === 'tp2', color: '#b7e07a', symbol: 'star' },
    { name: 'SL1', test: t => t.reason === 'stop_loss', color: '#e05d5d', symbol: 'x' },
    { name: 'SL2', test: t => t.reason === 'sl2', color: '#ff9f6e', symbol: 'diamond' },
  ];

  for (const g of groups) {
    const rows = trades.filter(g.test);
    if (!rows.length) continue;
    traces.push({
      type: 'scatter',
      mode: 'markers',
      name: g.name,
      x: rows.map(t => t.ts),
      y: rows.map(t => t.price),
      text: rows.map(t => `${t.reason}<br>price=${t.price.toFixed(2)}<br>qty=${t.qty.toFixed(6)}<br>equity=${t.equity.toFixed(2)}`),
      hovertemplate: '%{text}<extra></extra>',
      marker: { color: g.color, symbol: g.symbol, size: 9, line: { color: '#111217', width: 1 } },
      yaxis: 'y',
    });
  }

  const shapes = d.zones.map(z => {
    const bullish = z.direction === 'bullish';
    return {
      type: 'rect',
      xref: 'x',
      yref: 'y',
      x0: z.x0,
      x1: z.x1,
      y0: z.lower,
      y1: z.upper,
      fillcolor: bullish ? 'rgba(85,183,168,0.18)' : 'rgba(217,139,84,0.18)',
      line: { color: bullish ? 'rgba(85,183,168,0.65)' : 'rgba(217,139,84,0.65)', width: 1 },
    };
  });

  const xEnd = d.ts[d.ts.length - 1];
  const xStart = d.ts[Math.max(0, d.ts.length - 500)];
    const layout = {
    paper_bgcolor: '#17181c',
    plot_bgcolor: '#111217',
    font: { color: '#e7e9ee', size: 11 },
    margin: { l: 58, r: 58, t: 10, b: 10 },
    dragmode: 'zoom',
    xaxis: {
      type: 'date',
      range: [xStart, xEnd],
      rangeslider: { visible: true, bgcolor: '#111217', thickness: 0.04 },
      showgrid: true,
      gridcolor: '#2d3038',
      fixedrange: false,
    },
    yaxis: {
      side: 'right',
      autorange: true,
      fixedrange: false,
      showgrid: true,
      gridcolor: '#2d3038',
    },
    yaxis2: {
      overlaying: 'y',
      side: 'left',
      autorange: true,
      fixedrange: false,
      showgrid: false,
      tickfont: { color: 'rgba(141,179,255,0.65)', size: 10 },
    },
    shapes,
    legend: { orientation: 'h', y: 1.02, x: 0, bgcolor: 'rgba(23,24,28,0.88)' },
    hovermode: 'x unified',
  };

  Plotly.react('chart', traces, layout, {
    responsive: true,
    displaylogo: false,
    scrollZoom: true,
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
        for scenario in SCENARIOS:
            label = scenario["label"]
            timeframe = scenario["tf"]
            n = scenario.get("n", N_CANDLES)
            min_gap_pct = MIN_GAP_PCT_BY_TF[timeframe]
            print(f"  [{label}] fetching {n} x {timeframe} candles ...", end=" ", flush=True)
            candles = await fetch_candles(client, SYMBOL, timeframe, n)
            data = await run_chart_data(candles, min_gap_pct)
            all_data[label] = data
            print(
                f"{len(candles)} candles | trades {len(data['trades'])} | "
                f"return {data['return_pct']:+.2f}%"
            )
    finally:
        await client.close_connection()

    html = HTML_TEMPLATE.replace(
        "__ALL_DATA__",
        json.dumps(all_data, ensure_ascii=False, separators=(",", ":")),
    )
    out = Path(__file__).parent / "backtest_fvg_result.html"
    out.write_text(html, encoding="utf-8")
    print(f"\nSaved - {out}")
    webbrowser.open(out.as_uri())


if __name__ == "__main__":
    asyncio.run(main())
