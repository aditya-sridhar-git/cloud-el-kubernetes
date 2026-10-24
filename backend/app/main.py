import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, FastAPI

from app.api import apps, prescale, recommendations, webhooks, ws
from app.config import Settings, get_settings
from app.events import Hub
from app.intel.collector import Collector
from app.intel.prescale import PrescaleManager
from app.k8s.ops import K8sOps
from app.pipeline.builders import Builder, make_builder
from app.pipeline.orchestrator import Cloner, Orchestrator, mark_interrupted
from app.pipeline.source import clone
from app.store import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


def create_app(
    settings: Optional[Settings] = None,
    builder: Optional[Builder] = None,
    k8s_ops=None,
    cloner: Cloner = clone,
) -> FastAPI:
    """App factory; tests pass fakes for the builder, Kubernetes operations and git clone."""
    settings = settings or get_settings()
    hub = Hub()
    k8s_ops = k8s_ops or K8sOps(settings.apps_namespace, settings.app_host_suffix, settings.rollout_timeout_s)
    orchestrator = Orchestrator(settings, hub, builder or make_builder(settings), k8s_ops, cloner)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_engine(settings.database_url)
        interrupted = mark_interrupted()
        if interrupted:
            log.warning("marked %d interrupted deployment(s) as FAILED", interrupted)
        loop = asyncio.get_running_loop()
        hub.bind(loop)
        orchestrator.bind(loop)

        scheduler = None
        if settings.enable_background:
            scheduler = BackgroundScheduler(timezone=settings.timezone)
            collector = Collector(settings, hub)
            scheduler.add_job(
                collector.collect_once, "interval", seconds=settings.collect_interval_s,
                id="collector", max_instances=1, coalesce=True,
            )
            app.state.prescale = PrescaleManager(scheduler, k8s_ops, hub, settings.timezone)
            scheduler.start()
            app.state.prescale.sync()
        yield
        if scheduler:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="K8s Deployment & Auto-Scaling Platform", lifespan=lifespan)
    app.state.settings = settings
    app.state.hub = hub
    app.state.orchestrator = orchestrator
    app.state.k8s_ops = k8s_ops
    app.state.prescale = None

    api = APIRouter(prefix="/api")

    @api.get("/healthz")
    def healthz():
        return {"ok": True}

    for module in (apps, recommendations, prescale, webhooks, ws):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
