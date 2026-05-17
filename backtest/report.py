from dataclasses import dataclass
from datetime import datetime

from backend.broker.base import Fill


@dataclass
class BacktestReport:
    equity_curve: list[dict]      # [{timestamp, equity, price}]
    fills: list[Fill]
    initial_balance: float
    symbol: str
    interval: str
    start: str
    end: str
    leverage: int

    def generate(self) -> dict:
        """모든 성과 지표를 계산해서 dict로 반환."""
        if not self.equity_curve:
            return {"error": "No data"}

        equities = [e["equity"] for e in self.equity_curve]
        final_equity = equities[-1]

        return {
            "summary": {
                "symbol":          self.symbol,
                "interval":        self.interval,
                "period":          f"{self.start} ~ {self.end}",
                "leverage":        f"{self.leverage}x",
                "initial_balance": round(self.initial_balance, 2),
                "final_equity":    round(final_equity, 2),
            },
            "performance": {
                "total_return_pct":  round(self._total_return(final_equity), 2),
                "max_drawdown_pct":  round(self._max_drawdown(equities), 2),
                "sharpe_ratio":      round(self._sharpe(equities), 3),
                "win_rate_pct":      round(self._win_rate(), 2),
                "profit_factor":     round(self._profit_factor(), 3),
                "total_trades":      self._total_trades(),
                "total_fee_paid":    round(sum(f.fee for f in self.fills), 4),
            },
            "equity_curve": self.equity_curve,
        }

    # ── 지표 계산 ─────────────────────────────────────────────────────────

    def _total_return(self, final_equity: float) -> float:
        return (final_equity - self.initial_balance) / self.initial_balance * 100

    def _max_drawdown(self, equities: list[float]) -> float:
        peak = equities[0]
        max_dd = 0.0
        for e in equities:
            if e > peak:
                peak = e
            dd = (peak - e) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def _sharpe(self, equities: list[float], risk_free: float = 0.0) -> float:
        if len(equities) < 2:
            return 0.0
        returns = [
            (equities[i] - equities[i - 1]) / equities[i - 1]
            for i in range(1, len(equities))
        ]
        avg = sum(returns) / len(returns)
        variance = sum((r - avg) ** 2 for r in returns) / len(returns)
        std = variance ** 0.5
        if std == 0:
            return 0.0
        return (avg - risk_free) / std * (len(returns) ** 0.5)

    def _trade_pnls(self) -> list[float]:
        """체결 내역에서 거래별 손익 추출 (매수/매도 쌍으로 계산)."""
        buys: list[Fill] = []
        pnls: list[float] = []
        for fill in self.fills:
            from backend.broker.base import OrderSide
            if fill.side == OrderSide.BUY:
                buys.append(fill)
            else:
                if buys:
                    entry = buys.pop(0)
                    pnl = (fill.price - entry.price) * fill.quantity - fill.fee - entry.fee
                    pnls.append(pnl)
        return pnls

    def _win_rate(self) -> float:
        pnls = self._trade_pnls()
        if not pnls:
            return 0.0
        wins = sum(1 for p in pnls if p > 0)
        return wins / len(pnls) * 100

    def _profit_factor(self) -> float:
        pnls = self._trade_pnls()
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss   = abs(sum(p for p in pnls if p < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    def _total_trades(self) -> int:
        return len(self._trade_pnls())

    def print_summary(self) -> None:
        """콘솔에 결과 출력."""
        result = self.generate()
        s = result["summary"]
        p = result["performance"]
        print("\n" + "=" * 50)
        print(f"  Backtest Result — {s['symbol']} {s['interval']} {s['leverage']}")
        print(f"  Period  : {s['period']}")
        print(f"  Balance : {s['initial_balance']} → {s['final_equity']} USDT")
        print("-" * 50)
        print(f"  Total Return : {p['total_return_pct']:+.2f}%")
        print(f"  Max Drawdown : -{p['max_drawdown_pct']:.2f}%")
        print(f"  Sharpe Ratio : {p['sharpe_ratio']:.3f}")
        print(f"  Win Rate     : {p['win_rate_pct']:.1f}%")
        print(f"  Profit Factor: {p['profit_factor']:.3f}")
        print(f"  Total Trades : {p['total_trades']}")
        print(f"  Total Fees   : {p['total_fee_paid']:.4f} USDT")
        print("=" * 50 + "\n")
