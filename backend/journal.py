import json
import logging
from typing import Any

from backend.database import JournalEvent, db_session


class JournalService:
    def __init__(self) -> None:
        self.logger = logging.getLogger("delta-bot.journal")

    def log_event(
        self,
        event: str,
        payload: dict[str, Any] | None = None,
        *,
        level: str = "INFO",
        symbol: str = "",
        strategy: str = "",
    ) -> None:
        data = payload or {}
        self.logger.info("event=%s symbol=%s strategy=%s payload=%s", event, symbol, strategy, data)
        with db_session() as session:
            session.add(
                JournalEvent(
                    level=level,
                    event=event,
                    symbol=symbol,
                    strategy=strategy,
                    payload=json.dumps(data),
                )
            )
