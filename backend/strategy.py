from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Optional


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
class EmaScalpState:
    symbol: str
    candles: deque
    ema9: float
    ema21: float
    ema200: float
    ema200_history: deque
    atr: float
    avg_volume: float
    in_trade: bool
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    tp1_hit: bool
    cooldown_until: Optional[datetime]
    last_status: str


class EmaScalpStrategy:
    name = "ema_scalp_9_21_200"

    def __init__(
        self,
        ema_fast: int = 9,
        ema_slow: int = 21,
        ema_trend: int = 200,
        atr_period: int = 14,
        volume_period: int = 20,
        ema_slope_lookback: int = 20,
        atr_stop_mult: float = 1.2,
        rr_tp1: float = 1.0,
        rr_tp2: float = 2.0,
        min_atr_pct: float = 0.0015,
        max_distance_atr_mult: float = 0.8,
        cooldown_candles: int = 5,
    ) -> None:
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.ema_trend = ema_trend
        self.atr_period = atr_period
        self.volume_period = volume_period
        self.ema_slope_lookback = ema_slope_lookback
        self.atr_stop_mult = atr_stop_mult
        self.rr_tp1 = rr_tp1
        self.rr_tp2 = rr_tp2
        self.min_atr_pct = min_atr_pct
        self.max_distance_atr_mult = max_distance_atr_mult
        self.cooldown_candles = cooldown_candles
        self.states: dict[str, EmaScalpState] = {}

    def _ema(self, prev: float, price: float, period: int) -> float:
        k = 2.0 / (period + 1)
        return price * k + prev * (1 - k)

    def _atr(self, candles: deque, period: int) -> float:
        if len(candles) < 2:
            return 0.0
        trs = []
        prev_close = None
        for c in candles:
            high = float(c["high"])
            low = float(c["low"])
            close = float(c["close"])
            if prev_close is None:
                trs.append(high - low)
            else:
                trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
            prev_close = close
        n = min(period, len(trs))
        return sum(trs[-n:]) / n if n > 0 else 0.0

    def _avg_volume(self, candles: deque, period: int) -> float:
        if not candles:
            return 0.0
        n = min(period, len(candles))
        return sum(float(c["volume"]) for c in list(candles)[-n:]) / n

    def _new_state(self, symbol: str, candle: dict) -> EmaScalpState:
        close = float(candle["close"])
        return EmaScalpState(
            symbol=symbol,
            candles=deque(maxlen=max(self.ema_trend, 250)),
            ema9=close,
            ema21=close,
            ema200=close,
            ema200_history=deque(maxlen=self.ema_slope_lookback + 5),
            atr=0.0,
            avg_volume=float(candle["volume"]),
            in_trade=False,
            side="",
            entry=0.0,
            stop_loss=0.0,
            take_profit=0.0,
            tp1_hit=False,
            cooldown_until=None,
            last_status="warming_up",
        )

    def on_candle(self, candle: dict) -> tuple[StrategySignal | None, dict]:
        symbol = candle["symbol"].upper()
        ts = datetime.fromisoformat(candle["ts"].replace("Z", "+00:00"))
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        vol = float(candle["volume"])

        state = self.states.get(symbol)
        if state is None:
            state = self._new_state(symbol, candle)
            self.states[symbol] = state

        state.candles.append(candle)
        state.ema9 = self._ema(state.ema9, close, self.ema_fast)
        state.ema21 = self._ema(state.ema21, close, self.ema_slow)
        state.ema200 = self._ema(state.ema200, close, self.ema_trend)
        state.ema200_history.append(state.ema200)
        state.atr = self._atr(state.candles, self.atr_period)
        state.avg_volume = self._avg_volume(state.candles, self.volume_period)

        if len(state.candles) < self.ema_trend:
            state.last_status = "warming_up"
            return None, asdict(state)

        # Cooldown check
        if state.cooldown_until and ts < state.cooldown_until:
            state.last_status = "cooldown"
            return None, asdict(state)
        elif state.cooldown_until and ts >= state.cooldown_until:
            state.cooldown_until = None

        # If in trade, manage exits
        if state.in_trade:
            return self._manage_trade(state, candle)

        # --- Filters ---
        # 1. EMA200 slope filter
        if len(state.ema200_history) < self.ema_slope_lookback:
            state.last_status = "slope_warming_up"
            return None, asdict(state)
        old_ema200 = state.ema200_history[-self.ema_slope_lookback]
        slope_up = state.ema200 > old_ema200
        slope_down = state.ema200 < old_ema200

        # 2. ATR volatility filter
        if state.atr <= 0 or close <= 0:
            state.last_status = "no_atr"
            return None, asdict(state)
        atr_pct = state.atr / close
        if atr_pct < self.min_atr_pct:
            state.last_status = "low_volatility"
            return None, asdict(state)

        # 3. Volume filter
        if vol < state.avg_volume:
            state.last_status = "low_volume"
            return None, asdict(state)

        # 4. Overextended entry filter
        distance = abs(close - state.ema9)
        if distance > self.max_distance_atr_mult * state.atr:
            state.last_status = "overextended"
            return None, asdict(state)

        # --- Long setup ---
        if close > state.ema200 and slope_up and state.ema9 > state.ema21:
            pullback_low = min(float(c["low"]) for c in list(state.candles)[-3:])
            if low <= state.ema9 and close > state.ema9:
                entry = close
                stop = min(pullback_low, low) - 0.2 * state.atr
                stop = min(stop, entry - self.atr_stop_mult * state.atr)
                risk = entry - stop
                if risk <= 0:
                    state.last_status = "invalid_long_risk"
                    return None, asdict(state)
                tp = entry + risk * self.rr_tp2
                state.in_trade = True
                state.side = "buy"
                state.entry = entry
                state.stop_loss = stop
                state.take_profit = tp
                state.tp1_hit = False
                state.last_status = "long_entered"
                return (
                    StrategySignal(
                        strategy=self.name,
                        symbol=symbol,
                        action="enter",
                        side="buy",
                        reason="ema_pullback_long",
                        entry=entry,
                        stop_loss=stop,
                        take_profit=tp,
                        rr=self.rr_tp2,
                    ),
                    asdict(state),
                )
            state.last_status = "waiting_long_pullback"
            return None, asdict(state)

        # --- Short setup ---
        if close < state.ema200 and slope_down and state.ema9 < state.ema21:
            pullback_high = max(float(c["high"]) for c in list(state.candles)[-3:])
            if high >= state.ema9 and close < state.ema9:
                entry = close
                stop = max(pullback_high, high) + 0.2 * state.atr
                stop = max(stop, entry + self.atr_stop_mult * state.atr)
                risk = stop - entry
                if risk <= 0:
                    state.last_status = "invalid_short_risk"
                    return None, asdict(state)
                tp = entry - risk * self.rr_tp2
                state.in_trade = True
                state.side = "sell"
                state.entry = entry
                state.stop_loss = stop
                state.take_profit = tp
                state.tp1_hit = False
                state.last_status = "short_entered"
                return (
                    StrategySignal(
                        strategy=self.name,
                        symbol=symbol,
                        action="enter",
                        side="sell",
                        reason="ema_pullback_short",
                        entry=entry,
                        stop_loss=stop,
                        take_profit=tp,
                        rr=self.rr_tp2,
                    ),
                    asdict(state),
                )
            state.last_status = "waiting_short_pullback"
            return None, asdict(state)

        state.last_status = "no_setup"
        return None, asdict(state)

    def _manage_trade(self, state: EmaScalpState, candle: dict) -> tuple[StrategySignal | None, dict]:
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        ts = datetime.fromisoformat(candle["ts"].replace("Z", "+00:00"))

        if state.side == "buy":
            if low <= state.stop_loss:
                state.in_trade = False
                state.cooldown_until = ts + timedelta(minutes=self.cooldown_candles * 5)
                state.last_status = "long_stopped"
                return None, asdict(state)
            if not state.tp1_hit and high >= state.entry + (state.entry - state.stop_loss) * self.rr_tp1:
                state.tp1_hit = True
                state.stop_loss = state.entry
                state.last_status = "long_tp1_breakeven"
                return None, asdict(state)
            if high >= state.take_profit:
                state.in_trade = False
                state.last_status = "long_tp2_hit"
                return None, asdict(state)
            if state.tp1_hit and close < state.ema21:
                state.in_trade = False
                state.last_status = "long_trailed_out"
                return None, asdict(state)
            state.last_status = "long_running"
            return None, asdict(state)

        if state.side == "sell":
            if high >= state.stop_loss:
                state.in_trade = False
                state.cooldown_until = ts + timedelta(minutes=self.cooldown_candles * 5)
                state.last_status = "short_stopped"
                return None, asdict(state)
            if not state.tp1_hit and low <= state.entry - (state.stop_loss - state.entry) * self.rr_tp1:
                state.tp1_hit = True
                state.stop_loss = state.entry
                state.last_status = "short_tp1_breakeven"
                return None, asdict(state)
            if low <= state.take_profit:
                state.in_trade = False
                state.last_status = "short_tp2_hit"
                return None, asdict(state)
            if state.tp1_hit and close > state.ema21:
                state.in_trade = False
                state.last_status = "short_trailed_out"
                return None, asdict(state)
            state.last_status = "short_running"
            return None, asdict(state)

        state.last_status = "unknown_trade"
        return None, asdict(state)

    def _state_to_dict(self, state: EmaScalpState) -> dict:
        return {
            "symbol": state.symbol,
            "ema9": round(state.ema9, 4),
            "ema21": round(state.ema21, 4),
            "ema200": round(state.ema200, 4),
            "atr": round(state.atr, 4),
            "avg_volume": round(state.avg_volume, 2),
            "in_trade": state.in_trade,
            "side": state.side,
            "entry": state.entry,
            "stop_loss": state.stop_loss,
            "take_profit": state.take_profit,
            "tp1_hit": state.tp1_hit,
            "cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until else None,
            "last_status": state.last_status,
            "candles_collected": len(state.candles),
        }

    def status(self, symbol: str | None = None) -> dict:
        if symbol:
            state = self.states.get(symbol.upper())
            return self._state_to_dict(state) if state else {"symbol": symbol.upper(), "state": "idle"}
        return {k: self._state_to_dict(v) for k, v in self.states.items()}


class StrategyRegistry:
    def __init__(self, primary: EmaScalpStrategy) -> None:
        self._enabled: dict[str, bool] = {
            "ema_scalp_9_21_200": True,
            "range_reentry_4h": False,
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
    def __init__(
        self,
        ema_fast: int = 9,
        ema_slow: int = 21,
        ema_trend: int = 200,
        atr_period: int = 14,
        volume_period: int = 20,
        ema_slope_lookback: int = 20,
        atr_stop_mult: float = 1.2,
        rr_tp1: float = 1.0,
        rr_tp2: float = 2.0,
        min_atr_pct: float = 0.0015,
        max_distance_atr_mult: float = 0.8,
        cooldown_candles: int = 5,
    ) -> None:
        self.ema_scalp = EmaScalpStrategy(
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            ema_trend=ema_trend,
            atr_period=atr_period,
            volume_period=volume_period,
            ema_slope_lookback=ema_slope_lookback,
            atr_stop_mult=atr_stop_mult,
            rr_tp1=rr_tp1,
            rr_tp2=rr_tp2,
            min_atr_pct=min_atr_pct,
            max_distance_atr_mult=max_distance_atr_mult,
            cooldown_candles=cooldown_candles,
        )
        self.registry = StrategyRegistry(self.ema_scalp)

    def on_candle(self, candle: dict) -> tuple[StrategySignal | None, dict]:
        if not self.registry.is_enabled(self.ema_scalp.name):
            return None, {"state": "disabled"}
        return self.ema_scalp.on_candle(candle)

    def status(self, symbol: str | None = None) -> dict:
        return {
            "active": self.registry.is_enabled(self.ema_scalp.name),
            "primary": self.ema_scalp.name,
            "state": self.ema_scalp.status(symbol=symbol),
        }
