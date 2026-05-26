"""
Visual comparison: TrendChannelV1 vs DecreaseTrendChannerV1.

Run:
    python tools/decrease_compare_chart.py
"""

import asyncio
import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Type

sys.path.insert(0, str(Path(__file__).parent.parent))

from binance import AsyncClient

from backend.broker.base import OrderSide, Signal as BrokerSignal
from broker.mock import MockBroker
from strategies.base import BaseStrategy, FillEvent, MarketData
from strategies.decrease_trend_channer_v1 import DecreaseTrendChannerV1
from strategies.trend_channel_v1 import TrendChannelV1

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")


SYMBOL = "BTCUSDT"
INITIAL_BALANCE = 10_000.0
LEVERAGE = 1
RISK_PCT = 0.95
WINDOW = 50
PIVOT_K = 2
MIN_RR = 2.0
COOLDOWN = 5

INTERVAL_SECONDS = {
    "1d": 86400,
    "4h": 14400,
    "1h": 3600,
    "15m": 900,
    "5m": 300,
}

SCENARIOS = [
    {"label": "5m", "tf": "5m", "n": 10000},
    {"label": "15m", "tf": "15m", "n": 10000},
    {"label": "1h", "tf": "1h", "n": 10000},
    {"label": "4h", "tf": "4h", "n": 10000},
    {"label": "1d", "tf": "1d", "n": 2500},
]


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


async def run_and_record(
    candles: list[dict],
    symbol: str,
    strategy_cls: Type[BaseStrategy],
) -> dict:
    strategy = strategy_cls(
        leverage=LEVERAGE,
        window=WINDOW,
        pivot_k=PIVOT_K,
        min_rr=MIN_RR,
        cooldown=COOLDOWN,
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

    trades: list[dict] = []
    open_trade: dict | None = None
    equity: list[float] = []

    for candle in candles:
        ts = candle["timestamp"].strftime("%Y-%m-%dT%H:%M:%S")
        broker.update_price(symbol, candle["close"])
        data = MarketData(
            symbol=symbol,
            timestamp=candle["timestamp"],
            open=candle["open"],
            high=candle["high"],
            low=candle["low"],
            close=candle["close"],
            volume=candle["volume"],
        )

        for sig in strategy.on_data(data):
            qty = sig.quantity
            if sig.direction == "BUY" and qty == 0.0:
                qty = (broker._balance * RISK_PCT) / candle["close"]
            if qty <= 0.0:
                continue

            reason = sig.metadata.get("reason", "")
            override_price = None
            if reason == "stop_loss":
                override_price = sig.metadata.get("sl_price")
            elif reason == "tp1_upper":
                override_price = sig.metadata.get("tp1_price")
            elif reason == "tp2_h2":
                override_price = sig.metadata.get("h2_price")

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

            if sig.direction == "BUY":
                open_trade = {
                    "entry_ts": ts,
                    "entry_price": fill_price,
                    "sl": sig.metadata.get("sl", 0.0),
                    "tp1": sig.metadata.get("tp1", 0.0),
                    "tp2": sig.metadata.get("tp2", 0.0),
                    "exits": [],
                }
            elif sig.direction == "SELL" and open_trade is not None:
                open_trade["exits"].append({
                    "ts": ts,
                    "price": fill_price,
                    "reason": reason,
                })
                if not strategy._in_position:
                    trades.append(open_trade)
                    open_trade = None

        equity.append(broker.get_total_equity())

    final = broker.get_total_equity()
    ret = (final - INITIAL_BALANCE) / INITIAL_BALANCE * 100
    reasons = [exit_["reason"] for trade in trades for exit_ in trade["exits"]]

    return {
        "trades": trades,
        "equity": equity,
        "return_pct": ret,
        "entries": len(trades),
        "sl": reasons.count("stop_loss"),
        "tp1": reasons.count("tp1_upper"),
        "tp2": reasons.count("tp2_h2"),
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Decrease Trend Channel Compare</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #17181c; color: #e7e9ee; font-family: "Segoe UI", sans-serif; height: 100vh; display: flex; flex-direction: column; }
#header { padding: 8px 12px; background: #111217; border-bottom: 1px solid #2d3038; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
#header h2 { font-size: 13px; color: #e7e9ee; white-space: nowrap; }
.tabs, .toggles { display: flex; gap: 4px; flex-wrap: wrap; }
.tab, .toggle { padding: 5px 12px; background: #2d3038; border: none; color: #e7e9ee; cursor: pointer; border-radius: 4px; font-size: 12px; }
.tab:hover, .toggle:hover { background: #3f434e; }
.tab.active { background: #55b7a8; color: #101318; font-weight: 600; }
.toggle.active { background: #d7b56d; color: #101318; font-weight: 600; }
#stats { margin-left: auto; font-size: 12px; color: #aeb6c2; white-space: nowrap; }
#chart { flex: 1; min-height: 0; }
</style>
</head>
<body>
<div id="header">
  <h2>BTCUSDT · TrendChannelV1 vs DecreaseTrendChannerV1</h2>
  <div class="tabs" id="tabs"></div>
  <div class="toggles">
    <button class="toggle active" data-key="base">Base</button>
    <button class="toggle active" data-key="new">New</button>
    <button class="toggle active" data-key="equity">Equity</button>
  </div>
  <div id="stats"></div>
</div>
<div id="chart"></div>

<script>
const ALL = __ALL_DATA__;
const VIEW = { key: null, base: true, new: true, equity: true };

function markerTrace(name, x, y, color, symbol, size = 11) {
  return {
    type: 'scatter', mode: 'markers', name, x, y,
    marker: { symbol, size, color, line: { color: '#111217', width: 1.2 } },
  };
}

function addStrategyTraces(traces, d, key, label, color) {
  const trades = d[key].trades;
  const buys = trades.map(t => ({ ts: t.entry_ts, price: t.entry_price }));
  const sl = [], tp1 = [], tp2 = [];
  for (const t of trades) {
    for (const e of t.exits) {
      if (e.reason === 'stop_loss') sl.push(e);
      if (e.reason === 'tp1_upper') tp1.push(e);
      if (e.reason === 'tp2_h2') tp2.push(e);
    }
  }
  if (buys.length) traces.push(markerTrace(`${label} BUY`, buys.map(p => p.ts), buys.map(p => p.price), color, 'triangle-up', 13));
  if (sl.length) traces.push(markerTrace(`${label} SL`, sl.map(p => p.ts), sl.map(p => p.price), '#e05d5d', 'x-thin-open', 13));
  if (tp1.length) traces.push(markerTrace(`${label} TP1`, tp1.map(p => p.ts), tp1.map(p => p.price), '#d7b56d', 'circle-open', 11));
  if (tp2.length) traces.push(markerTrace(`${label} TP2`, tp2.map(p => p.ts), tp2.map(p => p.price), '#74a7ff', 'star', 13));
}

function renderChart(key) {
  VIEW.key = key;
  const d = ALL[key];
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tf === key));
  document.querySelectorAll('.toggle').forEach(t => t.classList.toggle('active', VIEW[t.dataset.key]));

  document.getElementById('stats').textContent =
    `Base ${d.base.return_pct.toFixed(2)}% / ${d.base.entries} trades | New ${d.new.return_pct.toFixed(2)}% / ${d.new.entries} trades`;

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

  if (VIEW.base) addStrategyTraces(traces, d, 'base', 'Base', '#b7a7ff');
  if (VIEW.new) addStrategyTraces(traces, d, 'new', 'New', '#55b7a8');

  if (VIEW.equity) {
    traces.push({
      type: 'scatter', mode: 'lines', name: 'Base Equity',
      x: d.ts, y: d.base.equity, yaxis: 'y2',
      line: { color: 'rgba(183,167,255,0.75)', width: 1.5 },
      hoverinfo: 'skip',
    });
    traces.push({
      type: 'scatter', mode: 'lines', name: 'New Equity',
      x: d.ts, y: d.new.equity, yaxis: 'y2',
      line: { color: 'rgba(85,183,168,0.8)', width: 1.5 },
      hoverinfo: 'skip',
    });
  }

  const xEnd = d.ts[d.ts.length - 1];
  const xStart = d.ts[Math.max(0, d.ts.length - 1000)];

  const layout = {
    paper_bgcolor: '#17181c',
    plot_bgcolor: '#111217',
    font: { color: '#e7e9ee', size: 11 },
    margin: { l: 58, r: 64, t: 10, b: 10 },
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
    yaxis2: {
      overlaying: 'y',
      side: 'left',
      showgrid: false,
      autorange: true,
      fixedrange: false,
      tickfont: { color: '#aeb6c2', size: 10 },
    },
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

document.querySelectorAll('.toggle').forEach(btn => {
  btn.onclick = () => {
    VIEW[btn.dataset.key] = !VIEW[btn.dataset.key];
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
            n_candles = scenario["n"]
            print(f"  [{label}] fetching {n_candles} x {timeframe} candles ...", end=" ", flush=True)
            candles = await fetch_candles(client, SYMBOL, timeframe, n_candles)
            print(f"{len(candles)} candles | running ...", end=" ", flush=True)

            base = await run_and_record(candles, SYMBOL, TrendChannelV1)
            new = await run_and_record(candles, SYMBOL, DecreaseTrendChannerV1)

            all_data[label] = {
                "ts": [c["timestamp"].strftime("%Y-%m-%dT%H:%M:%S") for c in candles],
                "o": [c["open"] for c in candles],
                "h": [c["high"] for c in candles],
                "l": [c["low"] for c in candles],
                "c": [c["close"] for c in candles],
                "base": base,
                "new": new,
            }
            print(
                f"base {base['return_pct']:+.2f}%/{base['entries']} trades | "
                f"new {new['return_pct']:+.2f}%/{new['entries']} trades"
            )
    finally:
        await client.close_connection()

    html = HTML_TEMPLATE.replace(
        "__ALL_DATA__",
        json.dumps(all_data, ensure_ascii=False, separators=(",", ":")),
    )

    out = Path(__file__).parent / "decrease_compare_result.html"
    out.write_text(html, encoding="utf-8")
    print(f"\nSaved - {out}")
    webbrowser.open(out.as_uri())


if __name__ == "__main__":
    asyncio.run(main())
