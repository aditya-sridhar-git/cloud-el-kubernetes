"""Periodic metric collection for every deployed app.

Each tick records replicas, total CPU/memory (Metrics Server), HPA utilisation and the request
rate. The request rate is derived from each pod's http_requests_total counter: the delta since the
previous tick divided by the elapsed time, summed over pods.
"""
import logging
import time
from typing import Optional

from sqlmodel import select

from app.config import Settings
from app.events import Hub
from app.k8s import observe
from app.store import db
from app.store.models import App, MetricSample

log = logging.getLogger(__name__)


class Collector:
    def __init__(self, settings: Settings, hub: Hub) -> None:
        self.settings = settings
        self.hub = hub
        # app -> (timestamp of previous tick, {pod: counter value})
        self._prev: dict[str, tuple[float, dict[str, float]]] = {}

    def collect_once(self) -> None:
        with db.session() as s:
            apps = s.exec(select(App)).all()
        for app in apps:
            try:
                self.sample_app(app)
            except Exception:
                log.exception("metric collection failed for %s", app.name)

    def sample_app(self, app: App) -> Optional[MetricSample]:
        ns = self.settings.apps_namespace
        dep = observe.deployment_status(app.name, ns)
        if dep is None:
            return None
        pods = observe.list_pods(app.name, ns)
        usage = observe.pod_usage(app.name, ns)
        hpa = observe.hpa_status(app.name, ns)

        now = time.time()
        counters = {}
        for pod in pods:
            if pod["ready"]:
                value = observe.requests_total(pod["name"], app.port, ns)
                if value is not None:
                    counters[pod["name"]] = value

        sample = MetricSample(
            app_name=app.name,
            ts=now,
            desired_replicas=dep["desired"],
            ready_replicas=dep["ready"],
            cpu_m=round(sum(u["cpu_m"] for u in usage.values()), 1),
            mem_mi=round(sum(u["mem_mi"] for u in usage.values()), 1),
            hpa_util=hpa["current_util"] if hpa else None,
            hpa_target=hpa["target"] if hpa else None,
            rps=self._rate(app.name, now, counters),
        )
        with db.session() as s:
            s.add(sample)
            s.commit()
            s.refresh(sample)

        self.hub.publish(
            app.name,
            {"type": "metrics", "sample": sample.model_dump(mode="json"), "pods": pods, "hpa": hpa, "deployment": dep},
        )
        return sample

    def _rate(self, app_name: str, now: float, counters: dict[str, float]) -> Optional[float]:
        prev = self._prev.get(app_name)
        self._prev[app_name] = (now, counters)
        if prev is None or not counters:
            return None
        prev_ts, prev_counts = prev
        elapsed = now - prev_ts
        if elapsed <= 0:
            return None
        total = 0.0
        for pod, value in counters.items():
            # A pod that appeared since the last tick started its counter at 0 within this interval.
            delta = value - prev_counts.get(pod, 0.0)
            total += max(delta, 0.0)  # negative = container restarted; ignore
        return round(total / elapsed, 2)
