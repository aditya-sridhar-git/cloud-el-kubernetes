"""Scheduled pre-scaling: raise the HPA's minReplicas shortly before a known peak, then restore it."""
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlmodel import select

from app.events import Hub
from app.store import db
from app.store.models import App, PrescaleSchedule

log = logging.getLogger(__name__)

JOB_PREFIX = "prescale-"


class PrescaleManager:
    def __init__(self, scheduler: BaseScheduler, k8s_ops, hub: Hub, tz: str) -> None:
        self.scheduler = scheduler
        self.k8s = k8s_ops
        self.hub = hub
        self.tz = tz

    def sync(self) -> None:
        """(Re)register a cron job for every enabled schedule."""
        for job in self.scheduler.get_jobs():
            if job.id.startswith(JOB_PREFIX) and not job.id.endswith("-end"):
                job.remove()
        with db.session() as s:
            schedules = s.exec(select(PrescaleSchedule).where(PrescaleSchedule.enabled == True)).all()  # noqa: E712
        for sched in schedules:
            self.scheduler.add_job(
                self.start,
                CronTrigger(day_of_week=sched.day_of_week, hour=sched.hour, minute=sched.minute, timezone=self.tz),
                args=[sched.id],
                id=f"{JOB_PREFIX}{sched.id}",
                replace_existing=True,
            )

    def start(self, schedule_id: int) -> None:
        with db.session() as s:
            sched = s.get(PrescaleSchedule, schedule_id)
            app = s.get(App, sched.app_name) if sched else None
        if not sched or not app:
            return
        floor = max(app.hpa_min, sched.min_replicas)
        self.k8s.set_hpa_range(app.name, floor, max(app.hpa_max, floor))
        self._event(app.name, f"pre-scale: minReplicas raised to {floor} for {sched.duration_min} min")
        self.scheduler.add_job(
            self.end,
            DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(minutes=sched.duration_min)),
            args=[app.name],
            id=f"{JOB_PREFIX}{schedule_id}-end",
            replace_existing=True,
        )

    def end(self, app_name: str) -> None:
        with db.session() as s:
            app = s.get(App, app_name)
        if not app:
            return
        self.k8s.set_hpa_range(app.name, app.hpa_min, app.hpa_max)
        self._event(app.name, f"pre-scale window over: minReplicas restored to {app.hpa_min}")

    def _event(self, app_name: str, message: str) -> None:
        log.info("%s: %s", app_name, message)
        self.hub.publish(app_name, {"type": "event", "deployment_id": None, "message": message})
