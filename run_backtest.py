"""
Historical backtest — TrendChannelV1 x 3 timeframes
Position (1d) / Swing (4h) / Scalping (1h)  — each 10,000 candles
"""

import asyncio
import sys
from datetime import datetime

from binance import AsyncClient

from backend.broker.base import OrderSide, Signal as BrokerSignal
from broker.mock import MockBroker
from strategies.base import FillEvent, MarketData
from strategies.trend_channel_v1 import TrendChannelV1

# Windows console UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# ── CONFIG ────────────────────────────────────────────────────────────────────
SYMBOL          = "BTCUSDT"
N_CANDLES       = 10_000
INITIAL_BALANCE = 10_000.0   # USDT
LEVERAGE        = 1
RISK_PCT        = 0.95       # entry 시 가용 잔고 비율
WINDOW          = 50
PIVOT_K         = 2
MIN_RR          = 2.0        # 최소 손익비 (reward / risk)
COOLDOWN        = 5          # SL 후 재진입 금지 캔들 수

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


async def fetch_candles(client: AsyncClient, symbol: str, interval: str, n: int) -> list[dict]:
    """최근 n개 캔들을 페이징해서 가져온다."""
    now_ts    = int(datetime.utcnow().timestamp() * 1000)
    start_ts  = now_ts - n * INTERVAL_SECONDS[interval] * 1000
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


async def run_single(
    candles: list[dict], symbol: str,
    zero_fee: bool = False,
    lev_sizing: bool = False,   # True → qty = balance * leverage / price
) -> dict:
    """단일 시나리오 백테스트."""
    strategy = TrendChannelV1(leverage=LEVERAGE, window=WINDOW, pivot_k=PIVOT_K, min_rr=MIN_RR, cooldown=COOLDOWN)
    broker   = MockBroker(
        initial_balance=INITIAL_BALANCE, leverage=LEVERAGE,
        **({} if not zero_fee else {"taker_fee": 0.0, "maker_fee": 0.0}),
    )

    def on_fill(fill) -> None:
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

    trade_log: list[dict] = []
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
            if sig.direction == "BUY" and qty == 0.0:
                sizing = LEVERAGE if lev_sizing else 1
                qty = (broker._balance * RISK_PCT * sizing) / candle["close"]
            if qty <= 0:
                continue

            # SL: 진입봉 꼬리 끝(sl_price)에서 시장가 체결
            sl_price = sig.metadata.get("sl_price") if sig.metadata.get("reason") == "stop_loss" else None
            if sl_price is not None:
                broker._current_prices[sig.symbol] = sl_price

            await broker.place_order(BrokerSignal(
                symbol        = sig.symbol,
                direction     = OrderSide(sig.direction),
                quantity      = qty,
                price         = None,
                strategy_name = sig.strategy_name,
                timestamp     = sig.timestamp,
            ))

            if sl_price is not None:
                broker._current_prices[sig.symbol] = candle["close"]

            trade_log.append({
                "date":   candle["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "dir":    sig.direction,
                "qty":    qty,
                "price":  candle["close"],
                "reason": sig.metadata.get("reason", ""),
                "equity": broker.get_total_equity(),
            })

        equity_curve.append(broker.get_total_equity())

    fills     = broker.get_fills()
    buy_fills = [f for f in fills if f.side == OrderSide.BUY]
    reasons   = [t["reason"] for t in trade_log if t["dir"] == "SELL"]

    final  = broker.get_total_equity()
    ret    = (final - INITIAL_BALANCE) / INITIAL_BALANCE * 100
    total_fee = sum(f.fee for f in fills)

    # 최대 낙폭 계산
    peak = INITIAL_BALANCE
    mdd  = 0.0
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
        "n_tp1":      reasons.count("tp1_upper"),
        "n_tp2":      reasons.count("tp2_h2"),
        "total_fee":  total_fee,
        "mdd":        mdd,
        "trade_log":  trade_log,
        "n_candles":  len(candles),
        "date_start": candles[0]["timestamp"].strftime("%Y-%m-%d") if candles else "-",
        "date_end":   candles[-1]["timestamp"].strftime("%Y-%m-%d") if candles else "-",
    }


async def main() -> None:
    print(f"Connecting Binance and fetching data ...\n")
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

    # ── 4가지 조합 실행 ───────────────────────────────────────────────────────
    # (1) 고정 수량, fee O  (2) 고정 수량, fee X
    # (3) 레버 수량, fee O  (4) 레버 수량, fee X
    runs: dict[str, dict] = {k: {} for k in ["fix_fee", "fix_0", "lev_fee", "lev_0"]}
    for label, (tf, candles) in results.items():
        print(f"Running backtest: {label} ({tf}) ...", end=" ", flush=True)
        runs["fix_fee"][label] = (tf, await run_single(candles, SYMBOL, zero_fee=False, lev_sizing=False))
        runs["fix_0"][label]   = (tf, await run_single(candles, SYMBOL, zero_fee=True,  lev_sizing=False))
        runs["lev_fee"][label] = (tf, await run_single(candles, SYMBOL, zero_fee=False, lev_sizing=True))
        runs["lev_0"][label]   = (tf, await run_single(candles, SYMBOL, zero_fee=True,  lev_sizing=True))
        print(f"done  ({runs['fix_fee'][label][1]['n_entry']} entries)")

    # ── 비교 테이블 ───────────────────────────────────────────────────────────
    W = 100
    print()
    print("=" * W)
    print(f"  BTCUSDT  |  window={WINDOW}  pivot_k={PIVOT_K}  leverage={LEVERAGE}x  min_rr={MIN_RR}  cooldown={COOLDOWN}")
    print(f"  sizing=fix: qty = balance/price  |  sizing=lev: qty = balance×{LEVERAGE}/price")
    print("=" * W)
    print(f"  {'Type':<10} {'TF':<5}  {'fix+fee':>9}  {'fix+0%':>8}  {'lev+fee':>9}  {'lev+0%':>8}  {'MDD(lev)':>9}  {'Fee(lev)':>9}")
    print("-" * W)
    for label in runs["fix_fee"]:
        tf = runs["fix_fee"][label][0]
        rf  = runs["fix_fee"][label][1]
        rn  = runs["fix_0"][label][1]
        lf  = runs["lev_fee"][label][1]
        ln  = runs["lev_0"][label][1]
        print(f"  {label:<10} {tf:<5}  {rf['return_pct']:>+8.2f}%  {rn['return_pct']:>+7.2f}%  {lf['return_pct']:>+8.2f}%  {ln['return_pct']:>+7.2f}%  {lf['mdd']:>8.1f}%  {lf['total_fee']:>9.2f}")
    print("=" * W)

    print()
    print(f"  {'Type':<10} {'TF':<5}  {'Entries':>7} {'SL':>5} {'TP1':>5} {'TP2':>5}  {'fix+fee':>12}  {'fix+0%':>12}  {'lev+fee':>12}  {'lev+0%':>12}")
    print("-" * W)
    for label in runs["fix_fee"]:
        tf = runs["fix_fee"][label][0]
        rf  = runs["fix_fee"][label][1]
        rn  = runs["fix_0"][label][1]
        lf  = runs["lev_fee"][label][1]
        ln  = runs["lev_0"][label][1]
        print(f"  {label:<10} {tf:<5}  {rf['n_entry']:>7} {rf['n_sl']:>5} {rf['n_tp1']:>5} {rf['n_tp2']:>5}  {rf['final']:>12,.2f}  {rn['final']:>12,.2f}  {lf['final']:>12,.2f}  {ln['final']:>12,.2f}")
    print("=" * W)

    # ── 거래 내역 (fix+fee 기준, 최근 30개) ─────────────────────────────────
    for label, (tf, r) in runs["fix_fee"].items():
        tl = r["trade_log"]
        if not tl:
            print(f"\n  [{label}] No trades.")
            continue
        print(f"\n  [{label} {tf}] Trade log ({len(tl)} records):")
        display = tl if len(tl) <= 30 else tl[-30:]
        if len(tl) > 30:
            print(f"    ... showing last 30 of {len(tl)} ...")
        for t in display:
            print(f"    {t['date']}  {t['dir']:4}  qty={t['qty']:>10.6f}  "
                  f"price={t['price']:>10,.2f}  [{t['reason']:<14}]  eq={t['equity']:>10,.2f}")


if __name__ == "__main__":
    asyncio.run(main())
