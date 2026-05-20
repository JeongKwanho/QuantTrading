"""
Backtest visualization — candlestick + channel lines + H1/H2/L1 pivot markers + trade markers.
python tools/backtest_chart.py
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
from strategies.trend_channel_v1 import TrendChannelV1

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# ── CONFIG ────────────────────────────────────────────────────────────────────
SYMBOL          = "BTCUSDT"
INITIAL_BALANCE = 10_000.0
LEVERAGE        = 1
RISK_PCT        = 0.95
WINDOW          = 50
PIVOT_K         = 2
MIN_RR          = 2.0
COOLDOWN        = 5

INTERVAL_SECONDS = {
    "1d": 86400, "4h": 14400, "1h": 3600, "15m": 900, "5m": 300,
}

SCENARIOS = [
    {"label": "5m",  "tf": "5m",  "n": 10000},
    {"label": "15m", "tf": "15m", "n": 10000},
    {"label": "1h",  "tf": "1h",  "n": 10000},
    {"label": "4h",  "tf": "4h",  "n": 10000},
    {"label": "1d",  "tf": "1d",  "n": 2500},
]
# ─────────────────────────────────────────────────────────────────────────────


async def fetch_candles(
    client: AsyncClient, symbol: str, interval: str, n: int
) -> list[dict]:
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


async def run_and_record(candles: list[dict], symbol: str) -> tuple[list, list, list]:
    """백테스트 실행 + 캔들별 채널 상태 / 거래 내역 / 채널 피벗 기록."""
    strategy = TrendChannelV1(
        leverage=LEVERAGE, window=WINDOW, pivot_k=PIVOT_K, min_rr=MIN_RR, cooldown=COOLDOWN
    )
    broker = MockBroker(initial_balance=INITIAL_BALANCE, leverage=LEVERAGE)

    def on_fill(fill) -> None:
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
    channel_pivots: list[dict] = []
    prev_locked:    bool = False

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

        ch          = strategy._pattern.downtrend_channel
        curr_locked = strategy._pattern._locked

        # 현재 캔들 기록 (일단 ch 값 그대로)
        candle_records.append({
            "ts":    ts_str,
            "o":     candle["open"],
            "h":     candle["high"],
            "l":     candle["low"],
            "c":     candle["close"],
            "lower": ch.lower_now if ch else None,
            "upper": ch.upper_now if ch else None,
        })

        # 채널이 새로 lock된 순간 → H1부터 현재까지 소급 채우기
        if curr_locked and not prev_locked and ch is not None:
            hist = strategy._pattern._history
            try:
                # downtrend: l1=H1(첫 고점), l2=H2(두번째 고점), h1=L1(저점)
                h1_ts_str = hist[ch.l1_idx].timestamp.strftime("%Y-%m-%dT%H:%M:%S")
                h2_ts_str = hist[ch.l2_idx].timestamp.strftime("%Y-%m-%dT%H:%M:%S")
                l1_ts_str = hist[ch.h1_idx].timestamp.strftime("%Y-%m-%dT%H:%M:%S")
                channel_pivots.append({
                    "H1_ts": h1_ts_str, "H1_price": ch.l1_price,
                    "H2_ts": h2_ts_str, "H2_price": ch.l2_price,
                    "L1_ts": l1_ts_str, "L1_price": ch.h1_price,
                })

                # H1의 candle_records 인덱스를 타임스탬프로 역검색
                h1_rec_idx = None
                for ri in range(len(candle_records) - 1, -1, -1):
                    if candle_records[ri]["ts"] == h1_ts_str:
                        h1_rec_idx = ri
                        break

                # H1 ~ 현재까지 upper/lower 소급 계산
                # upper_i = h1_price + slope * steps  → H1/H2/L1 모두 통과
                if h1_rec_idx is not None:
                    for ri in range(h1_rec_idx, len(candle_records)):
                        steps   = ri - h1_rec_idx
                        upper_i = ch.l1_price + ch.slope * steps
                        lower_i = upper_i - ch.channel_gap
                        candle_records[ri]["upper"] = upper_i
                        candle_records[ri]["lower"] = lower_i
            except (IndexError, AttributeError):
                pass
        prev_locked = curr_locked

        for sig in signals:
            qty = sig.quantity
            if sig.direction == "BUY" and qty == 0.0:
                qty = (broker._balance * RISK_PCT) / candle["close"]
            if qty <= 0:
                continue

            # SL: 진입봉 꼬리 끝(sl_price)에서 시장가 체결
            sl_price = sig.metadata.get("sl_price") if sig.metadata.get("reason") == "stop_loss" else None
            if sl_price is not None:
                broker._current_prices[sig.symbol] = sl_price

            await broker.place_order(BrokerSignal(
                symbol=sig.symbol,
                direction=OrderSide(sig.direction),
                quantity=qty,
                price=None,
                strategy_name=sig.strategy_name,
                timestamp=sig.timestamp,
            ))

            if sl_price is not None:
                broker._current_prices[sig.symbol] = candle["close"]

            reason = sig.metadata.get("reason", "")

            if sig.direction == "BUY":
                open_trade = {
                    "entry_ts":    ts_str,
                    "entry_price": candle["close"],
                    "sl_price":    sig.metadata.get("sl", candle["low"]),
                    "tp1_price":   sig.metadata.get("tp1", 0.0),
                    "tp2_price":   sig.metadata.get("tp2", 0.0),
                    "exits": [],
                }
            elif sig.direction == "SELL" and open_trade is not None:
                open_trade["exits"].append({
                    "ts":     ts_str,
                    "price":  candle["close"],
                    "reason": reason,
                    "qty":    qty,
                })
                if not strategy._in_position:
                    trades.append(open_trade)
                    open_trade = None

    if open_trade and open_trade["exits"]:
        trades.append(open_trade)

    return candle_records, trades, channel_pivots


def build_chart_data(
    label: str, candle_records: list, trades: list, channel_pivots: list
) -> dict:
    ts    = [r["ts"]    for r in candle_records]
    o     = [r["o"]     for r in candle_records]
    h     = [r["h"]     for r in candle_records]
    l     = [r["l"]     for r in candle_records]
    c     = [r["c"]     for r in candle_records]
    lower = [r["lower"] for r in candle_records]
    upper = [r["upper"] for r in candle_records]
    n_tr  = len(trades)
    wins  = sum(
        1 for t in trades
        if t["exits"] and t["exits"][-1]["reason"] != "stop_loss"
    )

    return {
        "label":           label,
        "ts": ts, "o": o, "h": h, "l": l, "c": c,
        "lower":           lower,
        "upper":           upper,
        "trades":          trades,
        "channel_pivots":  channel_pivots,
        "n_tr":            n_tr,
        "wins":            wins,
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Backtest — BTCUSDT TrendChannelV1</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #1e1e2e; color: #cdd6f4; font-family: 'Segoe UI', sans-serif; height: 100vh; display: flex; flex-direction: column; }
#header { padding: 8px 12px; background: #181825; border-bottom: 1px solid #313244; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
#header h2 { font-size: 13px; color: #89b4fa; white-space: nowrap; }
.tabs { display: flex; gap: 4px; }
.tab { padding: 5px 14px; background: #313244; border: none; color: #cdd6f4; cursor: pointer; border-radius: 4px; font-size: 13px; transition: background .15s; }
.tab:hover { background: #45475a; }
.tab.active { background: #89b4fa; color: #1e1e2e; font-weight: 600; }
#stats { margin-left: auto; font-size: 12px; color: #a6adc8; white-space: nowrap; }
#chart { flex: 1; min-height: 0; }
</style>
</head>
<body>
<div id="header">
  <h2>BTCUSDT · TrendChannelV1 · window=__WINDOW__ pivot_k=__PIVOT_K__ min_rr=__MIN_RR__ leverage=__LEVERAGE__x</h2>
  <div class="tabs" id="tabs"></div>
  <div id="stats"></div>
</div>
<div id="chart"></div>

<script>
const ALL = __ALL_DATA__;

function renderChart(key) {
  const d = ALL[key];
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tf === key));
  document.getElementById('stats').textContent =
    `거래 ${d.n_tr}건  승 ${d.wins}건  패 ${d.n_tr - d.wins}건  승률 ${d.n_tr ? Math.round(d.wins/d.n_tr*100) : 0}%`;

  const traces = [];

  /* ── 캔들스틱 ── */
  traces.push({
    type: 'candlestick', name: 'Price',
    x: d.ts, open: d.o, high: d.h, low: d.l, close: d.c,
    increasing: { line: { color: '#26a69a', width: 1 }, fillcolor: '#26a69a' },
    decreasing: { line: { color: '#ef5350', width: 1 }, fillcolor: '#ef5350' },
    whiskerwidth: 0.3,
  });

  /* ── 채널 하단선 ── */
  traces.push({
    type: 'scatter', name: '채널 하단', x: d.ts, y: d.lower,
    mode: 'lines', connectgaps: false,
    line: { color: 'rgba(239,83,80,0.6)', dash: 'dot', width: 1.5 },
    hoverinfo: 'skip',
  });

  /* ── 채널 상단선 (하단 채우기) ── */
  traces.push({
    type: 'scatter', name: '채널 상단', x: d.ts, y: d.upper,
    mode: 'lines', connectgaps: false,
    fill: 'tonexty', fillcolor: 'rgba(239,83,80,0.06)',
    line: { color: 'rgba(239,83,80,0.4)', dash: 'dot', width: 1.5 },
    hoverinfo: 'skip',
  });

  /* ── 채널 피벗 마커: H1, H2, L1 ── */
  const h1x = [], h1y = [], h2x = [], h2y = [], l1x = [], l1y = [];
  for (const p of d.channel_pivots) {
    h1x.push(p.H1_ts); h1y.push(p.H1_price);
    h2x.push(p.H2_ts); h2y.push(p.H2_price);
    l1x.push(p.L1_ts); l1y.push(p.L1_price);
  }

  if (h1x.length) traces.push({
    type: 'scatter', mode: 'markers+text', name: 'H1',
    x: h1x, y: h1y,
    text: h1y.map(() => 'H1'), textposition: 'top center',
    marker: { symbol: 'diamond', size: 12, color: '#fab387',
              line: { color: '#1e1e2e', width: 1.5 } },
    textfont: { size: 11, color: '#fab387' },
  });

  if (h2x.length) traces.push({
    type: 'scatter', mode: 'markers+text', name: 'H2',
    x: h2x, y: h2y,
    text: h2y.map(() => 'H2'), textposition: 'top center',
    marker: { symbol: 'diamond', size: 12, color: '#cba6f7',
              line: { color: '#1e1e2e', width: 1.5 } },
    textfont: { size: 11, color: '#cba6f7' },
  });

  if (l1x.length) traces.push({
    type: 'scatter', mode: 'markers+text', name: 'L1',
    x: l1x, y: l1y,
    text: l1y.map(() => 'L1'), textposition: 'bottom center',
    marker: { symbol: 'diamond', size: 12, color: '#94e2d5',
              line: { color: '#1e1e2e', width: 1.5 } },
    textfont: { size: 11, color: '#94e2d5' },
  });

  /* ── 거래 마커 ── */
  const buys = [], sl = [], tp1 = [], tp2 = [];
  for (const t of d.trades) {
    buys.push({ ts: t.entry_ts, price: t.entry_price });
    for (const e of t.exits) {
      if (e.reason === 'stop_loss')    sl.push(e);
      else if (e.reason === 'tp1_upper') tp1.push(e);
      else if (e.reason === 'tp2_h2')   tp2.push(e);
    }
  }

  const mk = { type: 'scatter', mode: 'markers' };

  if (buys.length) traces.push({ ...mk, name: '매수',
    x: buys.map(b=>b.ts), y: buys.map(b=>b.price),
    marker: { symbol: 'triangle-up', size: 13, color: '#a6e3a1',
              line: { color: '#1e1e2e', width: 1.5 } } });

  if (sl.length) traces.push({ ...mk, name: '손절(SL)',
    x: sl.map(s=>s.ts), y: sl.map(s=>s.price),
    marker: { symbol: 'x-thin-open', size: 13, color: '#f38ba8',
              line: { color: '#f38ba8', width: 2.5 } } });

  if (tp1.length) traces.push({ ...mk, name: 'TP1',
    x: tp1.map(s=>s.ts), y: tp1.map(s=>s.price),
    marker: { symbol: 'circle-open', size: 11, color: '#f9e2af',
              line: { color: '#f9e2af', width: 2 } } });

  if (tp2.length) traces.push({ ...mk, name: 'TP2',
    x: tp2.map(s=>s.ts), y: tp2.map(s=>s.price),
    marker: { symbol: 'star', size: 13, color: '#89b4fa',
              line: { color: '#1e1e2e', width: 1 } } });

  /* ── SL / TP 수평선 (거래별) ── */
  const shapes = [];
  for (const t of d.trades) {
    if (!t.exits.length) continue;
    const x0 = t.entry_ts;
    const x1 = t.exits[t.exits.length - 1].ts;

    if (t.sl_price > 0) shapes.push({
      type:'line', xref:'x', yref:'y',
      x0, x1, y0: t.sl_price, y1: t.sl_price,
      line: { color: 'rgba(243,139,168,0.5)', dash:'dash', width:1 },
    });
    if (t.tp1_price > 0) shapes.push({
      type:'line', xref:'x', yref:'y',
      x0, x1, y0: t.tp1_price, y1: t.tp1_price,
      line: { color: 'rgba(249,226,175,0.5)', dash:'dash', width:1 },
    });
    if (t.tp2_price > 0 && Math.abs(t.tp2_price - t.tp1_price) > 1) shapes.push({
      type:'line', xref:'x', yref:'y',
      x0, x1, y0: t.tp2_price, y1: t.tp2_price,
      line: { color: 'rgba(137,180,250,0.5)', dash:'dash', width:1 },
    });
  }

  /* ── 기본 표시 범위: 마지막 1000봉 ── */
  const xEnd   = d.ts[d.ts.length - 1];
  const xStart = d.ts[Math.max(0, d.ts.length - 1000)];

  const layout = {
    paper_bgcolor: '#1e1e2e', plot_bgcolor: '#181825',
    font: { color: '#cdd6f4', size: 11 },
    margin: { l: 60, r: 20, t: 10, b: 10 },
    xaxis: {
      type: 'date',
      range: [xStart, xEnd],
      rangeslider: { visible: true, bgcolor: '#181825', thickness: 0.04 },
      showgrid: true, gridcolor: '#313244', gridwidth: 1,
    },
    yaxis: {
      showgrid: true, gridcolor: '#313244', gridwidth: 1,
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

/* ── 탭 생성 ── */
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
            print(f"{len(candles)} candles  |  running backtest ...", end=" ", flush=True)
            records, trades, pivots = await run_and_record(candles, SYMBOL)
            data = build_chart_data(label, records, trades, pivots)
            all_data[label] = data
            wins = data["wins"]
            n_tr = data["n_tr"]
            print(f"done  ({n_tr} trades, {wins}W/{n_tr-wins}L, {len(pivots)} channels)")
    finally:
        await client.close_connection()

    html = (
        HTML_TEMPLATE
        .replace("__ALL_DATA__", json.dumps(all_data, ensure_ascii=False, separators=(",", ":")))
        .replace("__WINDOW__",   str(WINDOW))
        .replace("__PIVOT_K__",  str(PIVOT_K))
        .replace("__MIN_RR__",   str(MIN_RR))
        .replace("__LEVERAGE__", str(LEVERAGE))
    )

    out = Path(__file__).parent / "backtest_result.html"
    out.write_text(html, encoding="utf-8")
    print(f"\nSaved → {out}")
    webbrowser.open(out.as_uri())


if __name__ == "__main__":
    asyncio.run(main())
