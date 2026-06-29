from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


NY_TZ = ZoneInfo("America/New_York")


@dataclass
class StrategySignal:
    strategy: str
    symbol: str
    action: str
    side: str
    reason: str
    entry: float
    stop_loss: float
    take_profit: float
    rr: float


@dataclass
class RangeState:
    symbol: str
    session_day: str
    range_high: float
    range_low: float
    range_finalized: bool
    breakout_side: str
    breakout_price: float
    waiting_reentry: bool
    entered: bool
    last_status: str


class RangeReentryStrategy:
    name = "range_reentry_4h"

    def __init__(self, rr: float = 2.0, reentry_buffer_pct: float = 0.10) -> None:
        self.rr = rr
        self.reentry_buffer_pct = reentry_buffer_pct / 100.0
        self.states: dict[str, RangeState] = {}

    def _session_info(self, ts: datetime) -> tuple[str, datetime, datetime]:
        ny_time = ts.astimezone(NY_TZ)
        day_key = ny_time.strftime("%Y-%m-%d")
        session_start = ny_time.replace(hour=0, minute=0, second=0, microsecond=0)
        session_end = session_start + timedelta(hours=4)
        return day_key, session_start.astimezone(UTC), session_end.astimezone(UTC)

    def _new_state(self, symbol: str, day_key: str, price: float) -> RangeState:
        return RangeState(
            symbol=symbol,
            session_day=day_key,
            range_high=price,
            range_low=price,
            range_finalized=False,
            breakout_side="",
            breakout_price=0.0,
            waiting_reentry=False,
            entered=False,
            last_status="collecting_4h_range",
        )

    def on_candle(self, candle: dict) -> tuple[StrategySignal | None, dict]:
        symbol = candle["symbol"].upper()
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        ts = datetime.fromisoformat(candle["ts"].replace("Z", "+00:00"))

        day_key, session_start_utc, session_end_utc = self._session_info(ts)
        state = self.states.get(symbol)
        if state is None or state.session_day != day_key:
            state = self._new_state(symbol, day_key, close)
            self.states[symbol] = state

        if session_start_utc <= ts < session_end_utc and not state.range_finalized:
            state.range_high = max(state.range_high, high)
            state.range_low = min(state.range_low, low)
            state.last_status = "collecting_4h_range"
            return None, asdict(state)

        if not state.range_finalized:
            state.range_finalized = True
            state.last_status = "watching_breakout"

        if not state.waiting_reentry and not state.entered:
            if close > state.range_high:
                state.breakout_side = "buy"
                state.breakout_price = close
                state.waiting_reentry = True
                state.last_status = "breakout_found_waiting_reentry"
                return None, asdict(state)
            if close < state.range_low:
                state.breakout_side = "sell"
                state.breakout_price = close
                state.waiting_reentry = True
                state.last_status = "breakout_found_waiting_reentry"
                return None, asdict(state)
            state.last_status = "watching_breakout"
            return None, asdict(state)

        if state.waiting_reentry and not state.entered:
            if state.breakout_side == "buy":
                reentry_floor = state.range_high * (1 - self.reentry_buffer_pct)
                if reentry_floor <= close <= state.range_high:
                    entry = close
                    stop = state.range_low
                    tp = entry + (entry - stop) * self.rr
                    state.entered = True
                    state.waiting_reentry = False
                    state.last_status = "trade_running"
                    return (
                        StrategySignal(
                            strategy=self.name,
                            symbol=symbol,
                            action="enter",
                            side="buy",
                            reason="bull_breakout_reentry",
                            entry=entry,
                            stop_loss=stop,
                            take_profit=tp,
                            rr=self.rr,
                        ),
                        asdict(state),
                    )

            if state.breakout_side == "sell":
                reentry_ceiling = state.range_low * (1 + self.reentry_buffer_pct)
                if state.range_low <= close <= reentry_ceiling:
                    entry = close
                    stop = state.range_high
                    tp = entry - (stop - entry) * self.rr
                    state.entered = True
                    state.waiting_reentry = False
                    state.last_status = "trade_running"
                    return (
                        StrategySignal(
                            strategy=self.name,
                            symbol=symbol,
                            action="enter",
                            side="sell",
                            reason="bear_breakout_reentry",
                            entry=entry,
                            stop_loss=stop,
                            take_profit=tp,
                            rr=self.rr,
                        ),
                        asdict(state),
                    )

            state.last_status = "waiting_reentry"
            return None, asdict(state)

        state.last_status = "trade_running"
        return None, asdict(state)

    def status(self, symbol: str | None = None) -> dict:
        if symbol:
            state = self.states.get(symbol.upper())
            return asdict(state) if state else {"symbol": symbol.upper(), "state": "idle"}
        return {k: asdict(v) for k, v in self.states.items()}


class StrategyRegistry:
    def __init__(self, primary: RangeReentryStrategy) -> None:
        self._enabled: dict[str, bool] = {
            "range_reentry_4h": True,
            "liquidity_sweep": False,
            "london_open": False,
            "smc": False,
            "ai_strategy": False,
        }
        self.primary = primary

    def list(self) -> list[dict]:
        items: list[dict] = []
        for name, enabled in self._enabled.items():
            items.append(
                {
                    "name": name,
                    "enabled": enabled,
                    "implemented": name == self.primary.name,
                }
            )
        return items

    def set_enabled(self, name: str, enabled: bool) -> dict:
        if name not in self._enabled:
            raise KeyError(f"Unknown strategy: {name}")
        self._enabled[name] = enabled
        return {"name": name, "enabled": enabled}

    def is_enabled(self, name: str) -> bool:
        return self._enabled.get(name, False)


class StrategyEngine:
    def __init__(self, rr: float = 2.0, reentry_buffer_pct: float = 0.10) -> None:
        self.range_reentry = RangeReentryStrategy(rr=rr, reentry_buffer_pct=reentry_buffer_pct)
        self.registry = StrategyRegistry(self.range_reentry)

    def on_candle(self, candle: dict) -> tuple[StrategySignal | None, dict]:
        if not self.registry.is_enabled(self.range_reentry.name):
            return None, {"state": "disabled"}
        return self.range_reentry.on_candle(candle)

    def status(self, symbol: str | None = None) -> dict:
        return {
            "active": self.registry.is_enabled(self.range_reentry.name),
            "primary": self.range_reentry.name,
            "state": self.range_reentry.status(symbol=symbol),
        }
