"""The deployment state machine.

  QUEUED -> CLONING -> BUILDING -> APPLYING -> ROLLING_OUT -> SUCCEEDED
                                                          |-> ROLLED_BACK (previous stable spec restored)
                                                          |-> FAILED      (nothing to roll back to)

Spec-only deployments (rollback, config change, recommendation) skip clone and build and
start at APPLYING with the image of the last successful deployment.
"""
import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Optional

from app.config import Settings
from app.events import Hub
from app.k8s.manifests import AppSpec
from app.pipeline.builders import Builder
from app.pipeline.source import clone
from app.store import db
from app.store.models import App, Deployment, DeployStatus, utcnow

log = logging.getLogger(__name__)

Cloner = Callable[[str, str, Path], str]


class Orchestrator:
    def __init__(self, settings: Settings, hub: Hub, builder: Builder, k8s_ops, cloner: Cloner = clone) -> None:
        self.settings = settings
        self.hub = hub
        self.builder = builder
        self.k8s = k8s_ops
        self.cloner = cloner
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Pipelines run on the server's event loop; sync routes submit from worker threads."""
        self._loop = loop

    # ---- public API -------------------------------------------------------------------

    def submit_build(self, app_name: str, trigger: str) -> Deployment:
        """Clone, build and deploy the head of the app's branch."""
        dep = self._create(app_name, trigger)
        self._spawn(self._run_build(dep.id, app_name))
        return dep

    def submit_spec(self, app_name: str, spec: AppSpec, trigger: str) -> Deployment:
        """Deploy an explicit spec without building (rollback, config change, recommendation)."""
        dep = self._create(app_name, trigger, commit_sha=spec.version, image=spec.image)
        self._spawn(self._run_spec(dep.id, app_name, spec))
        return dep

    # ---- pipeline ---------------------------------------------------------------------

    async def _run_build(self, dep_id: int, app_name: str) -> None:
        async with self._lock(app_name):
            workdir = Path(tempfile.mkdtemp(prefix=f"build-{app_name}-"))
            try:
                with db.session() as s:
                    app = s.get(App, app_name)
                if app is None:
                    raise RuntimeError(f"app {app_name} no longer exists")

                self._set(dep_id, app_name, DeployStatus.CLONING)
                sha = await asyncio.to_thread(self.cloner, app.repo_url, app.branch, workdir / "src")
                self._set(dep_id, app_name, DeployStatus.BUILDING, commit_sha=sha)

                def build_log(line: str) -> None:
                    self.hub.publish(app_name, {"type": "build_log", "deployment_id": dep_id, "line": line})

                image = await asyncio.to_thread(self.builder.build, app_name, sha, workdir / "src", build_log)
                spec = AppSpec.from_app(app, image=image, version=sha[:12])
                await self._rollout(dep_id, app_name, spec)
            except Exception as e:  # any failure before rollout: nothing was changed in the cluster
                log.exception("deployment %s failed", dep_id)
                self._set(dep_id, app_name, DeployStatus.FAILED, error=str(e)[:2000], finished=True)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

    async def _run_spec(self, dep_id: int, app_name: str, spec: AppSpec) -> None:
        async with self._lock(app_name):
            try:
                await self._rollout(dep_id, app_name, spec)
            except Exception as e:
                log.exception("deployment %s failed", dep_id)
                self._set(dep_id, app_name, DeployStatus.FAILED, error=str(e)[:2000], finished=True)

    async def _rollout(self, dep_id: int, app_name: str, spec: AppSpec) -> None:
        self._set(dep_id, app_name, DeployStatus.APPLYING, image=spec.image, spec_json=spec.to_json())
        await asyncio.to_thread(self.k8s.apply_spec, spec)

        self._set(dep_id, app_name, DeployStatus.ROLLING_OUT)
        progress = lambda msg: self._event(app_name, dep_id, msg)  # noqa: E731
        ok, msg = await asyncio.to_thread(self.k8s.wait_for_rollout, app_name, progress)
        if ok:
            self._set(dep_id, app_name, DeployStatus.SUCCEEDED, finished=True)
            return

        # Automatic rollback to the last stable spec.
        with db.session() as s:
            previous = db.last_successful(s, app_name, exclude_id=dep_id)
        if previous is None or previous.spec_json is None:
            self._set(dep_id, app_name, DeployStatus.FAILED, error=f"{msg}; no previous stable version", finished=True)
            return

        self._event(app_name, dep_id, f"rollout failed ({msg}); rolling back to deployment #{previous.id}")
        prev_spec = AppSpec.from_json(previous.spec_json)
        await asyncio.to_thread(self.k8s.apply_spec, prev_spec)
        ok_back, msg_back = await asyncio.to_thread(self.k8s.wait_for_rollout, app_name, progress)
        if ok_back:
            self._set(dep_id, app_name, DeployStatus.ROLLED_BACK, error=f"{msg}; rolled back to deployment #{previous.id}", finished=True)
        else:
            self._set(dep_id, app_name, DeployStatus.FAILED, error=f"{msg}; rollback also failed: {msg_back}", finished=True)

    # ---- helpers ----------------------------------------------------------------------

    def _lock(self, app_name: str) -> asyncio.Lock:
        return self._locks.setdefault(app_name, asyncio.Lock())

    def _spawn(self, coro) -> None:
        if self._loop is None:
            raise RuntimeError("Orchestrator is not bound to an event loop")

        def start() -> None:
            task = self._loop.create_task(coro)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        self._loop.call_soon_threadsafe(start)

    def _create(self, app_name: str, trigger: str, **fields) -> Deployment:
        with db.session() as s:
            dep = Deployment(app_name=app_name, trigger=trigger, **fields)
            s.add(dep)
            s.commit()
            s.refresh(dep)
        self._publish_status(dep)
        return dep

    def _set(self, dep_id: int, app_name: str, status: str, finished: bool = False, **fields) -> None:
        with db.session() as s:
            dep = s.get(Deployment, dep_id)
            dep.status = status
            for k, v in fields.items():
                setattr(dep, k, v)
            if finished:
                dep.finished_at = utcnow()
            s.add(dep)
            s.commit()
            s.refresh(dep)
        self._publish_status(dep)

    def _publish_status(self, dep: Deployment) -> None:
        self.hub.publish(dep.app_name, {"type": "deployment", "deployment": dep.model_dump(mode="json")})

    def _event(self, app_name: str, dep_id: Optional[int], message: str) -> None:
        self.hub.publish(app_name, {"type": "event", "deployment_id": dep_id, "message": message})


def mark_interrupted() -> int:
    """On startup, fail deployments that were in progress when the backend stopped."""
    from sqlmodel import select

    with db.session() as s:
        stale = s.exec(select(Deployment).where(Deployment.status.in_(DeployStatus.IN_PROGRESS))).all()
        for dep in stale:
            dep.status = DeployStatus.FAILED
            dep.error = "interrupted: the backend restarted during this deployment"
            dep.finished_at = utcnow()
            s.add(dep)
        s.commit()
        return len(stale)
