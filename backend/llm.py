import json
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from backend.config import settings

logger = logging.getLogger("delta-bot.llm")


class LLMDecisionService:
    def __init__(self) -> None:
        self._enabled = settings.llm_enabled
        self._news_enabled = settings.llm_news_check_enabled
        self._api_key = settings.llm_api_key
        self._model = settings.llm_model
        self._provider = settings.llm_provider
        self._last_news_check: datetime | None = None
        self._cached_news_sentiment: str = "neutral"

    def is_enabled(self) -> bool:
        return self._enabled and bool(self._api_key)

    async def check_news_sentiment(self, symbol: str) -> dict[str, Any]:
        if not self._news_enabled or not self.is_enabled():
            return {"sentiment": "neutral", "source": "disabled"}

        # Cache for 30 minutes
        if self._last_news_check and datetime.utcnow() - self._last_news_check < timedelta(minutes=30):
            return {"sentiment": self._cached_news_sentiment, "source": "cached"}

        prompt = (
            f"Analyze current market sentiment for {symbol} cryptocurrency. "
            f"Consider recent news, events, and market conditions. "
            f"Respond with JSON: {{\"sentiment\": \"bullish|bearish|neutral\", "
            f"\"confidence\": 0-100, \"summary\": \"brief one-line summary\"}}"
        )

        try:
            result = await self._call_llm(prompt)
            self._last_news_check = datetime.utcnow()
            self._cached_news_sentiment = result.get("sentiment", "neutral")
            return {"sentiment": self._cached_news_sentiment, "source": "live", **result}
        except Exception as exc:
            logger.warning("LLM news check failed: %s", exc)
            return {"sentiment": "neutral", "source": "error", "error": str(exc)}

    async def evaluate_exit(
        self,
        trade: dict[str, Any],
        current_price: float,
        candle_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.is_enabled():
            return {"action": "hold", "reason": "LLM disabled"}

        recent_closes = [str(float(c.get("close", 0))) for c in candle_data[-10:]]
        prompt = (
            f"You are a trading assistant. Analyze whether to hold or close this position.\n\n"
            f"Trade: {json.dumps(trade)}\n"
            f"Current price: {current_price}\n"
            f"Recent closes: {', '.join(recent_closes)}\n\n"
            f"Respond with JSON: {{\"action\": \"hold|close\", "
            f"\"reason\": \"brief explanation\", "
            f"\"confidence\": 0-100}}"
        )

        try:
            result = await self._call_llm(prompt)
            return result
        except Exception as exc:
            logger.warning("LLM exit evaluation failed: %s", exc)
            return {"action": "hold", "reason": f"LLM error: {exc}"}

    async def _call_llm(self, prompt: str) -> dict[str, Any]:
        if self._provider == "openai":
            return await self._call_openai(prompt)
        return {"action": "hold", "reason": f"Unknown provider: {self._provider}"}

    async def _call_openai(self, prompt: str) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a professional crypto trading analyst. Always respond with valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 300,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            # Strip markdown code fences if present
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            return json.loads(content)
