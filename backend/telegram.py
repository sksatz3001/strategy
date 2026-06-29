import logging

import httpx

from backend.config import settings


class TelegramService:
    def __init__(self) -> None:
        self.logger = logging.getLogger("delta-bot.telegram")
        self.enabled = settings.telegram_enabled
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id

    async def send(self, message: str) -> None:
        if not self.enabled:
            return
        if not self.token or not self.chat_id:
            self.logger.warning("telegram is enabled but token/chat id is missing")
            return

        endpoint = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(endpoint, json=payload)
                response.raise_for_status()
        except Exception as exc:
            self.logger.warning("telegram send failed: %s", exc)

    def format_open(self, payload: dict) -> str:
        return (
            f"🟢 <b>{payload.get('side', '').upper()} {payload.get('symbol', '')}</b>\n"
            f"Entry: {payload.get('entry')}\n"
            f"SL: {payload.get('sl')}\n"
            f"TP: {payload.get('tp')}\n"
            f"Risk: {payload.get('risk_pct', 1)}%\n"
            f"Reason: {payload.get('reason', '')}"
        )

    def format_close(self, payload: dict) -> str:
        result_icon = "✅" if float(payload.get("pnl", 0)) >= 0 else "❌"
        return (
            f"{result_icon} <b>{payload.get('symbol', '')} {payload.get('result', '').upper()}</b>\n"
            f"PnL: {payload.get('pnl')}\n"
            f"R: {payload.get('pnl_r')}\n"
            f"Balance: {payload.get('balance')}"
        )
