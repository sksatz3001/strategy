import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select

from backend.database import CandleRecord, JournalEvent, Trade, db_session


class AnalyticsService:
    def _closed_trades(self) -> list[Trade]:
        with db_session() as session:
            return session.execute(select(Trade).where(Trade.status == "CLOSED")).scalars().all()

    def _performance_summary(self, trades: list[Trade]) -> dict:
        if not trades:
            return {
                "count": 0,
                "win_rate": 0.0,
                "average_win": 0.0,
                "average_loss": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0,
                "average_r": 0.0,
                "sharpe_ratio": 0.0,
                "current_streak": 0,
                "best_streak": 0,
                "worst_streak": 0,
                "total_return_pct": 0.0,
            }

        pnls = [float(t.pnl or 0.0) for t in trades]
        rs = [float(t.pnl_r or 0.0) for t in trades if t.pnl_r is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        win_rate = len(wins) / max(len(pnls), 1)
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

        avg = sum(pnls) / len(pnls)
        variance = sum((value - avg) ** 2 for value in pnls) / max(len(pnls) - 1, 1)
        stdev = math.sqrt(variance)
        sharpe = (avg / stdev) * math.sqrt(len(pnls)) if stdev > 0 else 0.0

        best_streak = 0
        worst_streak = 0
        current_streak = 0
        for pnl in pnls:
            if pnl > 0:
                current_streak = current_streak + 1 if current_streak >= 0 else 1
            else:
                current_streak = current_streak - 1 if current_streak <= 0 else -1
            best_streak = max(best_streak, current_streak)
            worst_streak = min(worst_streak, current_streak)

        return {
            "count": len(trades),
            "win_rate": round(win_rate * 100, 2),
            "average_win": round(avg_win, 2),
            "average_loss": round(avg_loss, 2),
            "largest_win": round(max(pnls), 2),
            "largest_loss": round(min(pnls), 2),
            "profit_factor": 999.0 if math.isinf(profit_factor) else round(profit_factor, 3),
            "expectancy": round(expectancy, 3),
            "average_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
            "sharpe_ratio": round(sharpe, 3),
            "current_streak": current_streak,
            "best_streak": best_streak,
            "worst_streak": worst_streak,
            "total_return_pct": round((sum(pnls) / max(1.0, abs(sum(losses)) + 1.0)) * 100, 2),
        }

    def dashboard_metrics(self) -> dict:
        now = datetime.now(UTC)
        start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_week = start_today - timedelta(days=start_today.weekday())
        start_month = start_today.replace(day=1)

        with db_session() as session:
            open_trades = session.execute(select(Trade).where(Trade.status == "OPEN")).scalars().all()
            all_closed = session.execute(select(Trade).where(Trade.status == "CLOSED")).scalars().all()
            today_closed = [t for t in all_closed if t.closed_at and t.closed_at >= start_today.replace(tzinfo=None)]
            week_closed = [t for t in all_closed if t.closed_at and t.closed_at >= start_week.replace(tzinfo=None)]
            month_closed = [t for t in all_closed if t.closed_at and t.closed_at >= start_month.replace(tzinfo=None)]

        def pnl(items: list[Trade]) -> float:
            return round(sum(float(t.pnl or 0.0) for t in items), 2)

        wins = sum(1 for t in today_closed if float(t.pnl or 0.0) > 0)
        losses = sum(1 for t in today_closed if float(t.pnl or 0.0) <= 0)
        total = max(len(today_closed), 1)

        perf = self._performance_summary(all_closed)
        return {
            "today_pnl": pnl(today_closed),
            "weekly_pnl": pnl(week_closed),
            "monthly_pnl": pnl(month_closed),
            "today_trades": len(today_closed),
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / total) * 100, 2),
            "profit_factor": perf["profit_factor"],
            "sharpe_ratio": perf["sharpe_ratio"],
            "expectancy": perf["expectancy"],
            "average_r": perf["average_r"],
            "current_streak": perf["current_streak"],
            "best_streak": perf["best_streak"],
            "worst_streak": perf["worst_streak"],
            "open_positions": [
                {
                    "id": t.id,
                    "symbol": t.symbol,
                    "side": t.side,
                    "entry": t.entry_price,
                    "sl": t.stop_loss,
                    "tp": t.take_profit,
                    "status": t.status,
                    "strategy": t.strategy,
                }
                for t in open_trades
            ],
        }

    def strategy_statistics(self) -> dict:
        now = datetime.utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week = today - timedelta(days=today.weekday())
        month = today.replace(day=1)

        closed = self._closed_trades()
        trades_today = [t for t in closed if t.closed_at and t.closed_at >= today]
        trades_week = [t for t in closed if t.closed_at and t.closed_at >= week]
        trades_month = [t for t in closed if t.closed_at and t.closed_at >= month]
        perf = self._performance_summary(closed)

        return {
            "trades_today": len(trades_today),
            "trades_week": len(trades_week),
            "trades_month": len(trades_month),
            "win_rate": perf["win_rate"],
            "average_win": perf["average_win"],
            "average_loss": perf["average_loss"],
            "largest_win": perf["largest_win"],
            "largest_loss": perf["largest_loss"],
            "profit_factor": perf["profit_factor"],
            "average_r": perf["average_r"],
            "current_streak": perf["current_streak"],
            "best_streak": perf["best_streak"],
            "worst_streak": perf["worst_streak"],
            "expectancy": perf["expectancy"],
            "sharpe_ratio": perf["sharpe_ratio"],
        }

    def daily_calendar(self, days: int = 60) -> list[dict]:
        start = datetime.utcnow() - timedelta(days=max(days, 1))
        closed = [t for t in self._closed_trades() if t.closed_at and t.closed_at >= start]

        by_day: dict[str, float] = defaultdict(float)
        for trade in closed:
            key = trade.closed_at.strftime("%Y-%m-%d")
            by_day[key] += float(trade.pnl or 0.0)

        return [{"day": day, "pnl": round(value, 2)} for day, value in sorted(by_day.items())]

    def equity_curve(self, limit: int = 200) -> list[dict]:
        with db_session() as session:
            rows = (
                session.execute(
                    select(Trade)
                    .where(Trade.status == "CLOSED")
                    .order_by(Trade.closed_at.asc())
                    .limit(max(limit, 1))
                )
                .scalars()
                .all()
            )

        equity = 0.0
        points: list[dict] = []
        for item in rows:
            equity += float(item.pnl or 0.0)
            points.append(
                {
                    "trade_id": item.id,
                    "ts": item.closed_at.isoformat() if item.closed_at else item.opened_at.isoformat(),
                    "equity": round(equity, 2),
                }
            )
        return points

    def trade_history(self, limit: int = 200) -> list[dict]:
        with db_session() as session:
            rows = (
                session.execute(select(Trade).order_by(Trade.opened_at.desc()).limit(max(limit, 1)))
                .scalars()
                .all()
            )
        return [
            {
                "id": t.id,
                "strategy": t.strategy,
                "symbol": t.symbol,
                "side": t.side,
                "entry": t.entry_price,
                "exit": t.exit_price,
                "pnl": t.pnl,
                "pnl_r": t.pnl_r,
                "status": t.status,
                "opened_at": t.opened_at.isoformat(),
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                "entry_reason": t.entry_reason,
                "exit_reason": t.exit_reason,
                "screenshot_entry": t.screenshot_entry,
                "screenshot_exit": t.screenshot_exit,
            }
            for t in rows
        ]

    def heatmap(self) -> list[dict]:
        with db_session() as session:
            rows = session.execute(select(Trade).where(Trade.status == "CLOSED")).scalars().all()

        heat: dict[tuple[str, int], float] = {}
        for trade in rows:
            if not trade.closed_at:
                continue
            day = trade.closed_at.strftime("%a")
            hour = trade.closed_at.hour
            key = (day, hour)
            heat[key] = heat.get(key, 0.0) + float(trade.pnl or 0.0)

        output = []
        for (day, hour), value in sorted(heat.items(), key=lambda item: (item[0][0], item[0][1])):
            output.append({"day": day, "hour": hour, "pnl": round(value, 2)})
        return output

    def replay(self, trade_id: int) -> dict:
        with db_session() as session:
            trade = session.get(Trade, trade_id)
            if trade is None:
                raise KeyError(f"Trade {trade_id} not found")
            candles = (
                session.execute(
                    select(CandleRecord)
                    .where(
                        and_(
                            CandleRecord.symbol == trade.symbol,
                            CandleRecord.ts >= trade.opened_at - timedelta(hours=2),
                            CandleRecord.ts <= (trade.closed_at or datetime.utcnow()) + timedelta(hours=1),
                        )
                    )
                    .order_by(CandleRecord.ts.asc())
                )
                .scalars()
                .all()
            )

        return {
            "trade": {
                "id": trade.id,
                "symbol": trade.symbol,
                "side": trade.side,
                "entry": trade.entry_price,
                "stop_loss": trade.stop_loss,
                "take_profit": trade.take_profit,
                "exit": trade.exit_price,
                "opened_at": trade.opened_at.isoformat(),
                "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
            },
            "candles": [
                {
                    "ts": c.ts.isoformat(),
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume,
                }
                for c in candles
            ],
        }

    def weekly_ai_summary(self) -> dict:
        now = datetime.utcnow()
        start = now - timedelta(days=7)
        with db_session() as session:
            trades = (
                session.execute(
                    select(Trade).where(and_(Trade.status == "CLOSED", Trade.closed_at >= start))
                )
                .scalars()
                .all()
            )

        if not trades:
            return {
                "message": "No closed trades in the last 7 days.",
                "insights": [],
            }

        total = len(trades)
        wins = [t for t in trades if float(t.pnl or 0.0) > 0]
        losses = [t for t in trades if float(t.pnl or 0.0) <= 0]

        by_symbol: dict[str, float] = {}
        by_hour: dict[int, list[float]] = {}
        for t in trades:
            pnl = float(t.pnl or 0.0)
            by_symbol[t.symbol] = by_symbol.get(t.symbol, 0.0) + pnl
            if t.closed_at:
                by_hour.setdefault(t.closed_at.hour, []).append(pnl)

        best_symbol = max(by_symbol.items(), key=lambda item: item[1])[0]
        best_hour = max(by_hour.items(), key=lambda item: sum(item[1]) / len(item[1]))[0] if by_hour else 0

        insights = [
            f"This week you traded {total} positions.",
            f"Win rate: {round((len(wins) / max(total, 1)) * 100, 2)}% ({len(wins)} wins / {len(losses)} losses).",
            f"Your strongest symbol by net PnL was {best_symbol}.",
            f"Best average performance occurred around {best_hour:02d}:00 UTC.",
        ]

        if losses and len(losses) / total > 0.6:
            insights.append("Loss density is high. Consider reducing leverage or tightening max trades/day.")

        return {
            "message": "Weekly AI-style journal summary",
            "insights": insights,
        }

    def event_timeline(self, limit: int = 300) -> list[dict]:
        with db_session() as session:
            rows = (
                session.execute(select(JournalEvent).order_by(JournalEvent.created_at.desc()).limit(max(limit, 1)))
                .scalars()
                .all()
            )

        return [
            {
                "id": row.id,
                "event": row.event,
                "strategy": row.strategy,
                "symbol": row.symbol,
                "payload": row.payload,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
