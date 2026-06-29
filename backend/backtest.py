from dataclasses import asdict

from backend.strategy import RangeReentryStrategy


class BacktestService:
    def run(self, candles: list[dict], *, rr: float, reentry_buffer_pct: float) -> dict:
        strategy = RangeReentryStrategy(rr=rr, reentry_buffer_pct=reentry_buffer_pct)
        trades: list[dict] = []

        open_trade: dict | None = None

        for candle in candles:
            signal, state = strategy.on_candle(candle)

            if open_trade is not None:
                side = open_trade["side"]
                high = float(candle["high"])
                low = float(candle["low"])
                hit_sl = low <= open_trade["stop_loss"] if side == "buy" else high >= open_trade["stop_loss"]
                hit_tp = high >= open_trade["take_profit"] if side == "buy" else low <= open_trade["take_profit"]

                if hit_sl or hit_tp:
                    exit_price = open_trade["stop_loss"] if hit_sl else open_trade["take_profit"]
                    sign = 1 if side == "buy" else -1
                    pnl = sign * (exit_price - open_trade["entry"])
                    open_trade["exit"] = exit_price
                    open_trade["exit_ts"] = candle["ts"]
                    open_trade["result"] = "SL" if hit_sl else "TP"
                    open_trade["pnl"] = round(pnl, 6)
                    trades.append(open_trade)
                    open_trade = None

            if signal and open_trade is None:
                open_trade = {
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "entry": signal.entry,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "entry_ts": candle["ts"],
                    "reason": signal.reason,
                }

        wins = sum(1 for item in trades if item["pnl"] > 0)
        losses = len(trades) - wins
        net = round(sum(item["pnl"] for item in trades), 6)

        return {
            "params": {"rr": rr, "reentry_buffer_pct": reentry_buffer_pct},
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / max(len(trades), 1)) * 100, 2),
            "net_points": net,
            "trades": trades,
            "final_state": state,
        }

    def optimize(self, candles: list[dict], rr_values: list[float], reentry_buffer_pct: float) -> dict:
        results = []
        for rr in rr_values:
            outcome = self.run(candles, rr=rr, reentry_buffer_pct=reentry_buffer_pct)
            results.append(
                {
                    "rr": rr,
                    "net_points": outcome["net_points"],
                    "win_rate": outcome["win_rate"],
                    "trades": outcome["total_trades"],
                }
            )

        best = max(results, key=lambda item: item["net_points"]) if results else None
        return {"results": results, "best": best}
