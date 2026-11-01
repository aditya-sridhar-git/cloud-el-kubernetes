"""Run the real backend against an in-memory fake cluster: no AWS, Docker or Kubernetes needed.

The API, orchestrator, collector, recommender and database are the real code. Only the edges are
simulated: git clone, the image build, Kubernetes writes and reads. All metrics are made up, so
numbers from this mode must never be used as experimental results.

    .venv\\Scripts\\python simulate.py          # API on http://localhost:8077, token: dev-token

Controls (local only):
    POST /sim/fail-next          the next rollout crashes, which triggers automatic rollback
    POST /sim/load?rps=120       fix total requests per second for every app
    POST /sim/load               back to the default repeating wave
"""
import hashlib
import math
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import uvicorn

from app.config import Settings
from app.k8s import observe
from app.main import create_app

PORT = 8077
DB_PATH = Path("data/sim.db")
WAVE_PERIOD_S = 300
POD_START_S = 5
SCALE_UP_EVERY_S = 15
SCALE_DOWN_EVERY_S = 60
BASE_POD_CPU_M = 5
CPU_M_PER_RPS = 3


@dataclass
class Pod:
    name: str
    version: str
    started: float
    ready_at: float
    requests: float = 0.0
    restarts: int = 0
    reason: Optional[str] = None


@dataclass
class SimApp:
    spec: object
    hpa_min: int
    hpa_max: int
    replicas: int
    created: float
    pods: list = field(default_factory=list)
    last_tick: float = 0.0
    last_scale: float = 0.0
    events: list = field(default_factory=list)


class Cluster:
    """Holds the fake cluster state and moves it forward whenever it is observed."""

    def __init__(self) -> None:
        self.apps: dict[str, SimApp] = {}
        self.lock = threading.RLock()
        self.fail_next = False
        self.fixed_rps: Optional[float] = None

    def event(self, app: SimApp, kind: str, reason: str, obj: str, message: str) -> None:
        app.events.insert(0, {
            "time": datetime.now(timezone.utc).isoformat(),
            "type": kind, "reason": reason, "object": obj, "message": message,
        })
        del app.events[50:]

    def new_pod(self, app: SimApp, now: float, ready_delay: float = POD_START_S) -> Pod:
        version = app.spec.version
        pod = Pod(f"{app.spec.name}-{version[:5]}-{uuid.uuid4().hex[:5]}", version, now, now + ready_delay)
        app.pods.append(pod)
        self.event(app, "Normal", "Started", f"Pod/{pod.name}", f"Started container {app.spec.name}")
        return pod

    def rps(self, app: SimApp, now: float) -> float:
        if self.fixed_rps is not None:
            return self.fixed_rps
        wave = math.sin(2 * math.pi * (now - app.created) / WAVE_PERIOD_S)
        return max(0.0, 40 + 30 * wave + random.uniform(-3, 3))

    def ready_pods(self, app: SimApp, now: float) -> list:
        return [p for p in app.pods if p.reason is None and p.ready_at <= now]

    def per_pod_cpu_m(self, app: SimApp, now: float) -> float:
        ready = self.ready_pods(app, now)
        if not ready:
            return 0.0
        cpu = BASE_POD_CPU_M + CPU_M_PER_RPS * self.rps(app, now) / len(ready)
        return min(cpu, app.spec.cpu_limit_m)

    def tick(self, app: SimApp) -> None:
        now = time.time()
        elapsed = now - app.last_tick if app.last_tick else 0.0
        app.last_tick = now
        ready = self.ready_pods(app, now)
        for pod in ready:
            pod.requests += self.rps(app, now) / len(ready) * elapsed
        if not ready or any(p.reason for p in app.pods):
            return

        # The same formula the real HPA uses: ceil(current * utilisation / target).
        util = self.per_pod_cpu_m(app, now) / app.spec.cpu_request_m * 100
        wanted = math.ceil(len(ready) * util / app.spec.hpa_cpu_target)
        wanted = max(app.hpa_min, min(app.hpa_max, wanted))
        wait = SCALE_UP_EVERY_S if wanted > app.replicas else SCALE_DOWN_EVERY_S
        if wanted != app.replicas and now - app.last_scale >= wait:
            direction = "up" if wanted > app.replicas else "down"
            self.event(app, "Normal", "SuccessfulRescale", f"HorizontalPodAutoscaler/{app.spec.name}",
                       f"Scaled {direction} from {app.replicas} to {wanted}: CPU at {util:.0f}% of request")
            app.replicas = wanted
            app.last_scale = now
        while len(app.pods) < app.replicas:
            self.new_pod(app, now)
        del app.pods[app.replicas:]

    def get(self, name: str) -> Optional[SimApp]:
        app = self.apps.get(name)
        if app:
            self.tick(app)
        return app


cluster = Cluster()


class SimOps:
    """Stands in for K8sOps."""

    def apply_spec(self, spec) -> None:
        now = time.time()
        with cluster.lock:
            app = cluster.apps.get(spec.name)
            if app is None:
                app = SimApp(spec, spec.hpa_min, spec.hpa_max, spec.hpa_min, now)
                cluster.apps[spec.name] = app
            app.spec, app.hpa_min, app.hpa_max = spec, spec.hpa_min, spec.hpa_max
            app.replicas = max(spec.hpa_min, min(spec.hpa_max, app.replicas))
            app.pods = []
            for _ in range(app.replicas):
                cluster.new_pod(app, now)
            cluster.event(app, "Normal", "ScalingReplicaSet", f"Deployment/{spec.name}",
                          f"Rolling out version {spec.version} with {app.replicas} replica(s)")

    def wait_for_rollout(self, name: str, on_progress: Optional[Callable[[str], None]] = None) -> tuple[bool, str]:
        with cluster.lock:
            app = cluster.apps[name]
            fail, cluster.fail_next = cluster.fail_next, False
            total = app.replicas
        for done in range(1, total + 1):
            if on_progress:
                on_progress(f"{done}/{total} updated")
            time.sleep(1.5)
            if fail:
                with cluster.lock:
                    pod = app.pods[0]
                    for p in app.pods:
                        p.reason, p.restarts = "CrashLoopBackOff", 3
                    cluster.event(app, "Warning", "BackOff", f"Pod/{pod.name}", "Back-off restarting failed container")
                return False, f"pod {pod.name}: CrashLoopBackOff: back-off restarting failed container"
        with cluster.lock:
            now = time.time()
            for pod in app.pods:
                pod.ready_at = min(pod.ready_at, now)
        return True, f"{total}/{total} replicas available"

    def delete_app(self, name: str) -> None:
        with cluster.lock:
            cluster.apps.pop(name, None)

    def set_hpa_range(self, name: str, min_replicas: int, max_replicas: int) -> None:
        with cluster.lock:
            app = cluster.apps[name]
            app.hpa_min, app.hpa_max = min_replicas, max(min_replicas, max_replicas)
            app.replicas = max(app.hpa_min, min(app.hpa_max, app.replicas))
            cluster.event(app, "Normal", "PreScale", f"HorizontalPodAutoscaler/{name}",
                          f"Range set to {app.hpa_min}-{app.hpa_max}")


class SimBuilder:
    def build(self, app_name: str, sha: str, src_dir: Path, log: Callable[[str], None]) -> str:
        tag = f"{app_name}-{sha[:12]}"
        for line in ("[simulated] Step 1/3 : FROM public.ecr.aws/docker/library/python:3.12-slim",
                     "[simulated] Step 2/3 : COPY . /app",
                     "[simulated] Step 3/3 : CMD python app.py",
                     f"[simulated] pushed local-sim/apps:{tag}"):
            log(line)
            time.sleep(1)
        return f"local-sim/apps:{tag}"


def sim_clone(repo_url: str, branch: str, dest: Path) -> str:
    """Nothing is downloaded. Each call gets a new SHA, so each deploy looks like a new commit."""
    time.sleep(1)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "Dockerfile").write_text("FROM scratch\n")
    return hashlib.sha1(f"{repo_url}{branch}{uuid.uuid4()}".encode()).hexdigest()


def deployment_status(name, namespace):
    with cluster.lock:
        app = cluster.get(name)
        if app is None:
            return None
        ready = len(cluster.ready_pods(app, time.time()))
        return {"desired": app.replicas, "ready": ready, "updated": len(app.pods), "available": ready,
                "image": app.spec.image}


def list_pods(name, namespace):
    with cluster.lock:
        app = cluster.get(name)
        if app is None:
            return []
        now = time.time()
        return sorted(({
            "name": p.name,
            "phase": "Running" if p.ready_at <= now or p.reason else "Pending",
            "ready": p.reason is None and p.ready_at <= now,
            "restarts": p.restarts,
            "reason": p.reason,
            "node": "sim-node-1",
            "version": p.version,
            "started_at": datetime.fromtimestamp(p.started, timezone.utc).isoformat(),
        } for p in app.pods), key=lambda x: x["name"])


def pod_usage(name, namespace):
    with cluster.lock:
        app = cluster.get(name)
        if app is None:
            return {}
        now = time.time()
        cpu = cluster.per_pod_cpu_m(app, now)
        return {p.name: {"cpu_m": cpu, "mem_mi": 60 + random.uniform(0, 8)} for p in cluster.ready_pods(app, now)}


def hpa_status(name, namespace):
    with cluster.lock:
        app = cluster.get(name)
        if app is None:
            return None
        util = cluster.per_pod_cpu_m(app, time.time()) / app.spec.cpu_request_m * 100
        return {"min": app.hpa_min, "max": app.hpa_max, "target": app.spec.hpa_cpu_target,
                "current_util": round(util), "current_replicas": len(app.pods), "desired_replicas": app.replicas}


def requests_total(pod_name, port, namespace):
    with cluster.lock:
        for app in cluster.apps.values():
            for pod in app.pods:
                if pod.name == pod_name:
                    return pod.requests
    return None


def recent_events(name, namespace, limit=50):
    with cluster.lock:
        app = cluster.get(name)
        return list(app.events[:limit]) if app else []


class LogStream:
    """Same shape as the urllib3 response the real log follower reads: iterate bytes, then close."""

    def __init__(self, pod_name: str) -> None:
        self.pod_name = pod_name
        self.closed = threading.Event()

    def __iter__(self):
        while not self.closed.wait(1.0):
            path = random.choice(["/", "/healthz", "/work?ms=50"])
            yield f"[simulated] {self.pod_name} GET {path} 200\n".encode()

    def close(self) -> None:
        self.closed.set()


def install_fakes() -> None:
    observe.deployment_status = deployment_status
    observe.list_pods = list_pods
    observe.pod_usage = pod_usage
    observe.hpa_status = hpa_status
    observe.requests_total = requests_total
    observe.allocatable_cpu_m = lambda: 4000.0
    observe.recent_events = recent_events
    observe.open_log_stream = lambda pod, namespace, tail_lines=200: LogStream(pod)


def build_app():
    install_fakes()
    settings = Settings(
        _env_file=None,  # the simulation must never pick up real AWS settings
        database_url=f"sqlite:///{DB_PATH.as_posix()}",
        api_token="dev-token",
        app_host_suffix="127.0.0.1.nip.io",
        collect_interval_s=5,
    )
    app = create_app(settings, builder=SimBuilder(), k8s_ops=SimOps(), cloner=sim_clone)

    @app.post("/sim/fail-next")
    def fail_next():
        cluster.fail_next = True
        return {"fail_next": True}

    @app.post("/sim/load")
    def set_load(rps: Optional[float] = None):
        cluster.fixed_rps = rps
        return {"fixed_rps": rps}

    return app


if __name__ == "__main__":
    # The fake cluster lives in memory, so start the matching database empty too.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    uvicorn.run(build_app(), host="127.0.0.1", port=PORT)
