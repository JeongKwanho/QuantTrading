"""
Historical backtest — OBChannelV1 (Bullish Order Block)
"""

import asyncio
import sys
from datetime import datetime

from binance import AsyncClient

from backend.broker.base import OrderSide, Signal as BrokerSignal
from broker.mock import MockBroker
from strategies.base import FillEvent, MarketData
from strategies.ob_channel_v1 import OBChannelV1

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# ── CONFIG ────────────────────────────────────────────────────────────────────
SYMBOL          = "BTCUSDT"
N_CANDLES       = 10_000
INITIAL_BALANCE = 10_000.0
LEVERAGE        = 1
WINDOW          = 10
PIVOT_K         = 2
TREND_WINDOW    = 30
MIN_RR          = 2.0
TP2_LOOKBACK    = 7

SCENARIOS = [
    {"label": "Position", "timeframe": "1d"},
    {"label": "Swing",    "timeframe": "4h"},
    {"label": "Scalping", "timeframe": "1h"},
    {"label": "Scalp15m", "timeframe": "15m"},
    {"label": "Scalp5m",  "timeframe": "5m"},
]

INTERVAL_SECONDS = {
    "1d": 86400, "4h": 14400, "1h": 3600, "15m": 900, "5m": 300,
}
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


async def run_single(candles, symbol, zero_fee=False):
    strategy = OBChannelV1(
        leverage=LEVERAGE,
        window=WINDOW, pivot_k=PIVOT_K,
        trend_window=TREND_WINDOW,
        min_rr=MIN_RR, tp2_lookback=TP2_LOOKBACK,
    )
    broker = MockBroker(
        initial_balance=INITIAL_BALANCE, leverage=LEVERAGE,
        **({} if not zero_fee else {"taker_fee": 0.0, "maker_fee": 0.0}),
    )

    def on_fill(fill):
        strategy.on_fill(FillEvent(
            order_id  = fill.order_id,
            symbol    = fill.symbol,
            direction = fill.side.value,
            price     = fill.price,
            quantity  = fill.quantity,
            fee       = fill.fee,
            timestamp = fill.timestamp,
        ))
    broker.set_on_fill(on_fill)

    trade_log:    list[dict]  = []
    equity_curve: list[float] = []

    for candle in candles:
        sym = symbol
        broker.update_price(sym, candle["close"])

        data = MarketData(
            symbol=sym, timestamp=candle["timestamp"],
            open=candle["open"], high=candle["high"],
            low=candle["low"],   close=candle["close"],
            volume=candle["volume"],
        )

        for sig in strategy.on_data(data):
            qty = sig.quantity

            # 분할 진입: fraction 메타데이터로 수량 계산
            if sig.direction == "BUY" and qty == 0.0:
                fraction = sig.metadata.get("fraction", 1.0)
                qty = (broker._balance * fraction) / candle["close"]

            if qty <= 0:
                continue

            # 터치 가격에서 시장가 체결 (진입·청산 모두 오버라이드)
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
                symbol        = sig.symbol,
                direction     = OrderSide(sig.direction),
                quantity      = qty,
                price         = None,
                strategy_name = sig.strategy_name,
                timestamp     = sig.timestamp,
            ))

            if override_price is not None:
                broker._current_prices[sig.symbol] = candle["close"]

            fill_price = override_price if override_price is not None else candle["close"]
            trade_log.append({
                "date":   candle["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "dir":    sig.direction,
                "qty":    qty,
                "price":  fill_price,
                "reason": reason,
                "equity": broker.get_total_equity(),
            })

        equity_curve.append(broker.get_total_equity())

    fills     = broker.get_fills()
    buy_fills = [f for f in fills if f.side == OrderSide.BUY]
    reasons   = [t["reason"] for t in trade_log if t["dir"] == "SELL"]

    final     = broker.get_total_equity()
    ret       = (final - INITIAL_BALANCE) / INITIAL_BALANCE * 100
    total_fee = sum(f.fee for f in fills)

    peak, mdd = INITIAL_BALANCE, 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > mdd:
            mdd = dd

    return {
        "final":      final,
        "return_pct": ret,
        "n_entry":    len(buy_fills),
        "n_sl":       reasons.count("stop_loss"),
        "n_tp1":      reasons.count("tp1"),
        "n_tp2":      reasons.count("tp2"),
        "n_sl2":      reasons.count("sl2"),
        "total_fee":  total_fee,
        "mdd":        mdd,
        "trade_log":  trade_log,
        "date_start": candles[0]["timestamp"].strftime("%Y-%m-%d") if candles else "-",
        "date_end":   candles[-1]["timestamp"].strftime("%Y-%m-%d") if candles else "-",
    }


async def main():
    print("Connecting Binance and fetching data ...\n")
    client = await AsyncClient.create()

    results = {}
    try:
        for sc in SCENARIOS:
            label, tf = sc["label"], sc["timeframe"]
            print(f"  [{label}] fetching {N_CANDLES} x {tf} candles ...", end=" ", flush=True)
            candles = await fetch_candles(client, SYMBOL, tf, N_CANDLES)
            print(f"got {len(candles)} candles  ({candles[0]['timestamp'].date()} ~ {candles[-1]['timestamp'].date()})")
            results[label] = (tf, candles)
    finally:
        await client.close_connection()

    print()
    W = 90

    runs: dict[str, tuple[str, dict]] = {}
    for label, (tf, candles) in results.items():
        print(f"  Running backtest: {label} ({tf}) ...", end=" ", flush=True)
        r = await run_single(candles, SYMBOL)
        runs[label] = (tf, r)
        print(f"done  ({r['n_entry']} entries)")

    print()
    print("=" * W)
    print(f"  BTCUSDT  OBChannelV1  |  window={WINDOW}  trend_window={TREND_WINDOW}  pivot_k={PIVOT_K}  min_rr={MIN_RR}")
    print(f"  진입 분할: 1차 25% / 2차 25% / 3차 50%  |  leverage={LEVERAGE}x")
    print("=" * W)
    print(f"  {'Type':<10} {'TF':<5}  {'Return':>9}  {'MDD':>7}  {'Fee':>8}  {'Entries':>7}  SL  TP1  TP2  SL2")
    print("-" * W)

    for label, (tf, r) in runs.items():
        print(f"  {label:<10} {tf:<5}  {r['return_pct']:>+8.2f}%  {r['mdd']:>6.1f}%  "
              f"{r['total_fee']:>8.2f}  {r['n_entry']:>7}  "
              f"{r['n_sl']:>3}  {r['n_tp1']:>3}  {r['n_tp2']:>3}  {r['n_sl2']:>3}")

    print("=" * W)
    print()

    # 거래 내역
    for label, (tf, r) in runs.items():
        tl = r["trade_log"]
        if not tl:
            print(f"  [{label}] No trades.")
            continue
        print(f"\n  [{label} {tf}] Trade log ({len(tl)} records):")
        display = tl if len(tl) <= 40 else tl[-40:]
        if len(tl) > 40:
            print(f"    ... showing last 40 of {len(tl)} ...")
        for t in display:
            print(f"    {t['date']}  {t['dir']:4}  qty={t['qty']:>10.6f}  "
                  f"price={t['price']:>10,.2f}  [{t['reason']:<12}]  eq={t['equity']:>10,.2f}")


if __name__ == "__main__":
    asyncio.run(main())
