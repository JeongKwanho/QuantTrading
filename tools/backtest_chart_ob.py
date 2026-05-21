"""
Backtest visualization — OBChannelV1 (Bullish Order Block)
python tools/backtest_chart_ob.py
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
from strategies.base import FillEvent, MarketData
from strategies.ob_channel_v1 import OBChannelV1

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# ── CONFIG ────────────────────────────────────────────────────────────────────
SYMBOL          = "BTCUSDT"
INITIAL_BALANCE = 10_000.0
LEVERAGE        = 1
WINDOW          = 10
PIVOT_K         = 2
TREND_WINDOW    = 30
MIN_RR          = 2.0
TP2_LOOKBACK    = 7

INTERVAL_SECONDS = {
    "1d": 86400, "4h": 14400, "1h": 3600, "15m": 900, "5m": 300,
}

SCENARIOS = [
    {"label": "5m",  "tf": "5m",  "n": 3000},
    {"label": "15m", "tf": "15m", "n": 3000},
    {"label": "1h",  "tf": "1h",  "n": 3000},
    {"label": "4h",  "tf": "4h",  "n": 3000},
    {"label": "1d",  "tf": "1d",  "n": 2500},
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


async def run_and_record(candles, symbol):
    strategy = OBChannelV1(
        leverage=LEVERAGE,
        window=WINDOW, pivot_k=PIVOT_K,
        trend_window=TREND_WINDOW,
        min_rr=MIN_RR, tp2_lookback=TP2_LOOKBACK,
    )
    broker = MockBroker(initial_balance=INITIAL_BALANCE, leverage=LEVERAGE)

    def on_fill(fill):
        strategy.on_fill(FillEvent(
            order_id=fill.order_id, symbol=fill.symbol,
            direction=fill.side.value,
            price=fill.price, quantity=fill.quantity,
            fee=fill.fee, timestamp=fill.timestamp,
        ))
    broker.set_on_fill(on_fill)

    candle_records: list[dict] = []
    trades:         list[dict] = []
    open_trade:     dict | None = None
    equity_curve:   list[float] = []

    for candle in candles:
        broker.update_price(symbol, candle["close"])
        ts_str = candle["timestamp"].strftime("%Y-%m-%dT%H:%M:%S")

        data = MarketData(
            symbol=symbol, timestamp=candle["timestamp"],
            open=candle["open"], high=candle["high"],
            low=candle["low"],   close=candle["close"],
            volume=candle["volume"],
        )

        signals = strategy.on_data(data)

        candle_records.append({
            "ts": ts_str,
            "o":  candle["open"],  "h": candle["high"],
            "l":  candle["low"],   "c": candle["close"],
        })

        for sig in signals:
            qty = sig.quantity
            if sig.direction == "BUY" and qty == 0.0:
                fraction = sig.metadata.get("fraction", 1.0)
                qty = (broker._balance * fraction) / candle["close"]
            if qty <= 0:
                continue

            reason = sig.metadata.get("reason", "")
            override_price = None
            if reason in ("ob_entry1", "ob_entry2", "ob_entry3"):
                override_price = sig.metadata.get("entry_price")
            elif reason == "stop_loss":
                override_price = sig.metadata.get("sl_price")
            elif reason == "tp1":
                override_price = sig.metadata.get("tp1_price")
            elif reason == "tp2":
                override_price = sig.metadata.get("tp2_price")
            elif reason == "sl2":
                override_price = sig.metadata.get("sl2_price")

            if override_price is not None:
                broker._current_prices[sig.symbol] = override_price

            await broker.place_order(BrokerSignal(
                symbol=sig.symbol,
                direction=OrderSide(sig.direction),
                quantity=qty, price=None,
                strategy_name=sig.strategy_name,
                timestamp=sig.timestamp,
            ))

            if override_price is not None:
                broker._current_prices[sig.symbol] = candle["close"]

            fill_price = override_price if override_price is not None else candle["close"]

            if sig.direction == "BUY":
                if open_trade is None:
                    open_trade = {
                        "entries": [],
                        "sl_price":  sig.metadata.get("sl", 0.0),
                        "tp1_price": sig.metadata.get("tp1", 0.0),
                        "tp2_price": sig.metadata.get("tp2", 0.0),
                        "ob_open":   strategy._ob_open,
                        "ob_close":  strategy._ob_close,
                        "ob_low":    strategy._ob_low,
                        "ob_ts":     strategy._ob_ts.strftime("%Y-%m-%dT%H:%M:%S") if strategy._ob_ts else ts_str,
                        "exits":     [],
                    }
                open_trade["entries"].append({
                    "ts": ts_str, "price": fill_price, "reason": reason,
                })

            elif sig.direction == "SELL" and open_trade is not None:
                open_trade["exits"].append({
                    "ts": ts_str, "price": fill_price, "reason": reason,
                })
                if not strategy._in_position:
                    trades.append(open_trade)
                    open_trade = None

        equity_curve.append(broker.get_total_equity())

    if open_trade and open_trade["exits"]:
        trades.append(open_trade)

    final  = broker.get_total_equity()
    ret    = (final - INITIAL_BALANCE) / INITIAL_BALANCE * 100

    return candle_records, trades, equity_curve, ret


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Backtest — BTCUSDT OBChannelV1</title>
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
  <h2>BTCUSDT · OBChannelV1 · window=__WINDOW__ trend_window=__TREND_WINDOW__ min_rr=__MIN_RR__</h2>
  <div class="tabs" id="tabs"></div>
  <div id="stats"></div>
</div>
<div id="chart"></div>

<script>
const ALL = __ALL_DATA__;

function renderChart(key) {
  const d = ALL[key];
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tf === key));

  const nSL  = d.trades.filter(t => t.exits.some(e => e.reason === 'stop_loss')).length;
  const nTP1 = d.trades.filter(t => t.exits.some(e => e.reason === 'tp1')).length;
  const nTP2 = d.trades.filter(t => t.exits.some(e => e.reason === 'tp2')).length;
  const nSL2 = d.trades.filter(t => t.exits.some(e => e.reason === 'sl2')).length;
  document.getElementById('stats').textContent =
    `거래 ${d.trades.length}건  SL ${nSL}  TP1 ${nTP1}  TP2 ${nTP2}  SL2 ${nSL2}  수익 ${d.ret.toFixed(2)}%`;

  const traces = [];

  /* ── 캔들스틱 ── */
  traces.push({
    type: 'candlestick', name: 'Price',
    x: d.ts, open: d.o, high: d.h, low: d.l, close: d.c,
    increasing: { line: { color: '#26a69a', width: 1 }, fillcolor: '#26a69a' },
    decreasing: { line: { color: '#ef5350', width: 1 }, fillcolor: '#ef5350' },
    whiskerwidth: 0.3,
  });

  /* ── 진입 마커 (1·2·3차) ── */
  const e1x=[], e1y=[], e2x=[], e2y=[], e3x=[], e3y=[];
  for (const t of d.trades) {
    for (const e of t.entries) {
      if (e.reason === 'ob_entry1') { e1x.push(e.ts); e1y.push(e.price); }
      if (e.reason === 'ob_entry2') { e2x.push(e.ts); e2y.push(e.price); }
      if (e.reason === 'ob_entry3') { e3x.push(e.ts); e3y.push(e.price); }
    }
  }
  const mk = { type: 'scatter', mode: 'markers' };
  if (e1x.length) traces.push({ ...mk, name: '1차 매수',
    x: e1x, y: e1y,
    marker: { symbol: 'triangle-up', size: 13, color: '#a6e3a1', line: { color: '#1e1e2e', width: 1.5 } } });
  if (e2x.length) traces.push({ ...mk, name: '2차 매수',
    x: e2x, y: e2y,
    marker: { symbol: 'triangle-up', size: 11, color: '#89dceb', line: { color: '#1e1e2e', width: 1.5 } } });
  if (e3x.length) traces.push({ ...mk, name: '3차 매수',
    x: e3x, y: e3y,
    marker: { symbol: 'triangle-up', size: 9, color: '#74c7ec', line: { color: '#1e1e2e', width: 1.5 } } });

  /* ── 청산 마커 ── */
  const slx=[], sly=[], tp1x=[], tp1y=[], tp2x=[], tp2y=[], sl2x=[], sl2y=[];
  for (const t of d.trades) {
    for (const e of t.exits) {
      if (e.reason === 'stop_loss') { slx.push(e.ts);  sly.push(e.price);  }
      if (e.reason === 'tp1')       { tp1x.push(e.ts); tp1y.push(e.price); }
      if (e.reason === 'tp2')       { tp2x.push(e.ts); tp2y.push(e.price); }
      if (e.reason === 'sl2')       { sl2x.push(e.ts); sl2y.push(e.price); }
    }
  }
  if (slx.length)  traces.push({ ...mk, name: 'SL',
    x: slx,  y: sly,
    marker: { symbol: 'x-thin-open', size: 13, color: '#f38ba8', line: { color: '#f38ba8', width: 2.5 } } });
  if (tp1x.length) traces.push({ ...mk, name: 'TP1',
    x: tp1x, y: tp1y,
    marker: { symbol: 'circle-open', size: 11, color: '#f9e2af', line: { color: '#f9e2af', width: 2 } } });
  if (tp2x.length) traces.push({ ...mk, name: 'TP2',
    x: tp2x, y: tp2y,
    marker: { symbol: 'star', size: 13, color: '#89b4fa', line: { color: '#1e1e2e', width: 1 } } });
  if (sl2x.length) traces.push({ ...mk, name: 'SL2',
    x: sl2x, y: sl2y,
    marker: { symbol: 'triangle-down', size: 11, color: '#fab387', line: { color: '#1e1e2e', width: 1 } } });

  /* ── OB 존 사각형 + SL/TP 수평선 ── */
  const shapes = [];
  for (const t of d.trades) {
    if (!t.exits.length) continue;
    const x0 = t.entries[0]?.ts || t.ob_ts;
    const x1 = t.exits[t.exits.length - 1].ts;

    // OB 존 (ob_close ~ ob_open)
    shapes.push({
      type: 'rect', xref: 'x', yref: 'y',
      x0: t.ob_ts, x1,
      y0: t.ob_close, y1: t.ob_open,
      fillcolor: 'rgba(166,227,161,0.12)',
      line: { color: 'rgba(166,227,161,0.5)', width: 1 },
    });

    // SL선 (ob_low)
    if (t.sl_price > 0) shapes.push({
      type: 'line', xref: 'x', yref: 'y',
      x0, x1, y0: t.sl_price, y1: t.sl_price,
      line: { color: 'rgba(243,139,168,0.5)', dash: 'dash', width: 1 },
    });
    // TP1선
    if (t.tp1_price > 0) shapes.push({
      type: 'line', xref: 'x', yref: 'y',
      x0, x1, y0: t.tp1_price, y1: t.tp1_price,
      line: { color: 'rgba(249,226,175,0.5)', dash: 'dash', width: 1 },
    });
    // TP2선
    if (t.tp2_price > 0) shapes.push({
      type: 'line', xref: 'x', yref: 'y',
      x0, x1, y0: t.tp2_price, y1: t.tp2_price,
      line: { color: 'rgba(137,180,250,0.5)', dash: 'dash', width: 1 },
    });
  }

  /* ── 에쿼티 커브 (y2) ── */
  traces.push({
    type: 'scatter', name: 'Equity', x: d.ts, y: d.equity,
    mode: 'lines', yaxis: 'y2',
    line: { color: 'rgba(203,166,247,0.6)', width: 1.5 },
    hoverinfo: 'skip',
  });

  /* ── 기본 표시 범위: 마지막 500봉 ── */
  const xEnd   = d.ts[d.ts.length - 1];
  const xStart = d.ts[Math.max(0, d.ts.length - 500)];

  const layout = {
    paper_bgcolor: '#1e1e2e', plot_bgcolor: '#181825',
    font: { color: '#cdd6f4', size: 11 },
    margin: { l: 60, r: 60, t: 10, b: 10 },
    xaxis: {
      type: 'date', range: [xStart, xEnd],
      rangeslider: { visible: true, bgcolor: '#181825', thickness: 0.04 },
      showgrid: true, gridcolor: '#313244',
    },
    yaxis: {
      showgrid: true, gridcolor: '#313244',
      autorange: true, fixedrange: false, side: 'right',
    },
    yaxis2: {
      overlaying: 'y', side: 'left',
      showgrid: false, autorange: true, fixedrange: false,
      tickfont: { color: 'rgba(203,166,247,0.6)', size: 10 },
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


async def main():
    print("Connecting Binance ...")
    client = await AsyncClient.create()

    all_data: dict[str, dict] = {}
    try:
        for sc in SCENARIOS:
            label, tf, n = sc["label"], sc["tf"], sc["n"]
            print(f"  [{label}] fetching {n} x {tf} candles ...", end=" ", flush=True)
            candles = await fetch_candles(client, SYMBOL, tf, n)
            print(f"{len(candles)} candles  |  backtesting ...", end=" ", flush=True)
            records, trades, equity, ret = await run_and_record(candles, SYMBOL)
            reasons = [e["reason"] for t in trades for e in t["exits"]]
            print(f"done  {len(trades)} trades  SL:{reasons.count('stop_loss')} "
                  f"TP1:{reasons.count('tp1')} TP2:{reasons.count('tp2')} "
                  f"SL2:{reasons.count('sl2')}  ret={ret:+.2f}%")
            all_data[label] = {
                "ts": [r["ts"] for r in records],
                "o":  [r["o"]  for r in records],
                "h":  [r["h"]  for r in records],
                "l":  [r["l"]  for r in records],
                "c":  [r["c"]  for r in records],
                "equity": equity,
                "trades": trades,
                "ret":    ret,
            }
    finally:
        await client.close_connection()

    html = (
        HTML_TEMPLATE
        .replace("__ALL_DATA__",    json.dumps(all_data, ensure_ascii=False, separators=(",", ":")))
        .replace("__WINDOW__",      str(WINDOW))
        .replace("__TREND_WINDOW__", str(TREND_WINDOW))
        .replace("__MIN_RR__",      str(MIN_RR))
    )

    out = Path(__file__).parent / "backtest_ob_result.html"
    out.write_text(html, encoding="utf-8")
    print(f"\nSaved → {out}")
    webbrowser.open(out.as_uri())


if __name__ == "__main__":
    asyncio.run(main())
