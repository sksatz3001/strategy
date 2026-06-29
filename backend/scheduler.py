import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler


class SchedulerService:
    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler()
        self.logger = logging.getLogger("delta-bot.scheduler")

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.add_job(self._heartbeat, "interval", minutes=30, id="heartbeat")
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _heartbeat(self) -> None:
        self.logger.info("scheduler-heartbeat")
