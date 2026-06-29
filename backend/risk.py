from dataclasses import dataclass
from datetime import datetime


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 1.0
    daily_loss_limit_pct: float = 3.0
    max_trades_per_day: int = 6
    max_leverage: int = 5


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    quantity: float = 0.0
    risk_amount: float = 0.0


class RiskManager:
    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()

    def _day_key(self, now: datetime) -> str:
        return now.strftime("%Y-%m-%d")

    def evaluate_entry(
        self,
        *,
        equity: float,
        day_stats: dict,
        entry_price: float,
        stop_price: float,
        leverage: int,
        now: datetime,
    ) -> RiskDecision:
        if leverage > self.config.max_leverage:
            return RiskDecision(False, f"Leverage exceeds max ({self.config.max_leverage})")

        current_day = self._day_key(now)
        trades_today = int(day_stats.get("trades", {}).get(current_day, 0))
        if trades_today >= self.config.max_trades_per_day:
            return RiskDecision(False, "Max trades per day reached")

        daily_loss = float(day_stats.get("loss", {}).get(current_day, 0.0))
        daily_loss_limit = equity * (self.config.daily_loss_limit_pct / 100.0)
        if daily_loss >= daily_loss_limit:
            return RiskDecision(False, "Daily loss limit reached")

        per_unit_risk = abs(entry_price - stop_price)
        if per_unit_risk <= 0:
            return RiskDecision(False, "Invalid risk distance (entry equals stop)")

        risk_amount = equity * (self.config.risk_per_trade_pct / 100.0)
        quantity = risk_amount / per_unit_risk
        notional = quantity * entry_price
        margin_required = notional / max(leverage, 1)

        if margin_required > equity:
            return RiskDecision(False, "Insufficient equity for required margin")

        return RiskDecision(
            approved=True,
            reason="ok",
            quantity=round(quantity, 6),
            risk_amount=round(risk_amount, 2),
        )

    def compute_r_multiple(self, *, side: str, entry: float, stop: float, exit_price: float) -> float:
        risk_distance = abs(entry - stop)
        if risk_distance == 0:
            return 0.0
        if side.lower() == "buy":
            return (exit_price - entry) / risk_distance
        return (entry - exit_price) / risk_distance
