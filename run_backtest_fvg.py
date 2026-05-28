"""
Historical backtest - FVGChannelV1.

Run:
    python run_backtest_fvg.py
"""

import asyncio
import sys
from datetime import datetime

from binance import AsyncClient

from backend.broker.base import OrderSide, Signal as BrokerSignal
from broker.mock import MockBroker
from strategies.base import FillEvent, MarketData
from strategies.fvg_channel_v1 import FVGChannelV1

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")


SYMBOL = "BTCUSDT"
N_CANDLES = 10_000
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
    {"label": "Position", "timeframe": "1d"},
    {"label": "Swing", "timeframe": "4h"},
    {"label": "Scalping", "timeframe": "1h"},
    {"label": "Scalp15m", "timeframe": "15m"},
    {"label": "Scalp5m", "timeframe": "5m"},
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


async def run_single(candles: list[dict], symbol: str, min_gap_pct: float) -> dict:
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

    trade_log: list[dict] = []
    equity_curve: list[float] = []
    trade_base_equity: float | None = None

    for candle in candles:
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
            trade_log.append({
                "date": candle["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "dir": sig.direction,
                "qty": qty,
                "price": fill_price,
                "reason": reason,
                "equity": broker.get_total_equity(),
            })

            if reason in ("stop_loss", "tp2", "sl2"):
                trade_base_equity = None

        equity_curve.append(broker.get_total_equity())

    fills = broker.get_fills()
    final = broker.get_total_equity()
    reasons = [trade["reason"] for trade in trade_log]
    entry_reasons = [reason for reason in reasons if reason in ENTRY_REASONS]

    peak = INITIAL_BALANCE
    mdd = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak * 100
        mdd = max(mdd, drawdown)

    return {
        "final": final,
        "return_pct": (final - INITIAL_BALANCE) / INITIAL_BALANCE * 100,
        "mdd": mdd,
        "fee": sum(fill.fee for fill in fills),
        "entries": len(entry_reasons),
        "long_entries": len([r for r in entry_reasons if r.startswith("fvg_long")]),
        "short_entries": len([r for r in entry_reasons if r.startswith("fvg_short")]),
        "sl": reasons.count("stop_loss"),
        "tp1": reasons.count("tp1"),
        "tp2": reasons.count("tp2"),
        "sl2": reasons.count("sl2"),
        "trade_log": trade_log,
    }


async def main() -> None:
    print("Connecting Binance and fetching data ...\n")
    client = await AsyncClient.create()

    candles_by_label: dict[str, tuple[str, list[dict]]] = {}
    try:
        for scenario in SCENARIOS:
            label = scenario["label"]
            timeframe = scenario["timeframe"]
            print(f"  [{label}] fetching {N_CANDLES} x {timeframe} candles ...", end=" ", flush=True)
            candles = await fetch_candles(client, SYMBOL, timeframe, N_CANDLES)
            print(f"{len(candles)} candles ({candles[0]['timestamp'].date()} ~ {candles[-1]['timestamp'].date()})")
            candles_by_label[label] = (timeframe, candles)
    finally:
        await client.close_connection()

    runs: dict[str, tuple[str, dict]] = {}
    for label, (timeframe, candles) in candles_by_label.items():
        min_gap_pct = MIN_GAP_PCT_BY_TF[timeframe]
        print(f"Running {label} ({timeframe}, min_gap_pct={min_gap_pct:.4f}) ...", end=" ", flush=True)
        result = await run_single(candles, SYMBOL, min_gap_pct)
        runs[label] = (timeframe, result)
        print(f"done ({result['entries']} entries, return={result['return_pct']:+.2f}%)")

    width = 112
    print()
    print("=" * width)
    print(
        f"  {SYMBOL} FVGChannelV1 | trend={TREND_WINDOW} middle_x={MIDDLE_RANGE_MULTIPLIER} "
        f"liq_lookback={LIQUIDITY_LOOKBACK}"
    )
    print("=" * width)
    print(
        f"  {'Type':<10} {'TF':<5} {'Gap%':>7} {'Return':>9} {'MDD':>7} {'Fee':>9} "
        f"{'Entries':>7} {'Long':>5} {'Short':>5} {'SL':>4} {'TP1':>4} {'TP2':>4} {'SL2':>4}"
    )
    print("-" * width)
    for label, (timeframe, result) in runs.items():
        print(
            f"  {label:<10} {timeframe:<5} {MIN_GAP_PCT_BY_TF[timeframe] * 100:>6.2f}% "
            f"{result['return_pct']:>+8.2f}% {result['mdd']:>6.1f}% {result['fee']:>9.2f} "
            f"{result['entries']:>7} {result['long_entries']:>5} {result['short_entries']:>5} "
            f"{result['sl']:>4} {result['tp1']:>4} {result['tp2']:>4} {result['sl2']:>4}"
        )
    print("=" * width)

    for label, (timeframe, result) in runs.items():
        trades = result["trade_log"]
        if not trades:
            print(f"\n  [{label}] No trades.")
            continue
        print(f"\n  [{label} {timeframe}] Trade log ({len(trades)} records):")
        display = trades if len(trades) <= 40 else trades[-40:]
        if len(trades) > 40:
            print(f"    ... showing last 40 of {len(trades)} ...")
        for trade in display:
            print(
                f"    {trade['date']}  {trade['dir']:4} qty={trade['qty']:>10.6f} "
                f"price={trade['price']:>10,.2f} [{trade['reason']:<16}] eq={trade['equity']:>10,.2f}"
            )


if __name__ == "__main__":
    asyncio.run(main())
