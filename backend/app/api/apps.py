import asyncio
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from sqlmodel import Session, delete, select

from app.api import deps
from app.api.schemas import AppCreate, AppUpdate, check_consistency
from app.config import Settings
from app.k8s import observe
from app.k8s.manifests import AppSpec
from app.pipeline.orchestrator import Orchestrator
from app.pipeline.source import SourceError, validate_repo_url
from app.store import db
from app.store.models import App, Deployment, MetricSample, PrescaleSchedule, Recommendation

router = APIRouter(dependencies=[Depends(deps.require_token)])


def _summary(s: Session, app: App, cfg: Settings) -> dict:
    latest = s.exec(select(Deployment).where(Deployment.app_name == app.name).order_by(Deployment.id.desc())).first()
    return {
        **app.model_dump(mode="json"),
        "url": deps.app_url(app.name, cfg),
        "webhook_url": deps.webhook_url(cfg),
        "latest_deployment": latest.model_dump(mode="json") if latest else None,
    }


@router.get("/apps")
def list_apps(s: Session = Depends(db.get_session), cfg: Settings = Depends(deps.settings)):
    return [_summary(s, a, cfg) for a in s.exec(select(App).order_by(App.created_at)).all()]


@router.post("/apps", status_code=201)
def create_app(
    body: AppCreate,
    s: Session = Depends(db.get_session),
    cfg: Settings = Depends(deps.settings),
    orch: Orchestrator = Depends(deps.orchestrator),
):
    try:
        validate_repo_url(body.repo_url)
        check_consistency(body.model_dump())
    except (SourceError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    if s.get(App, body.name):
        raise HTTPException(status_code=409, detail=f"app '{body.name}' already exists")

    app = App(**body.model_dump(), webhook_secret=secrets.token_hex(20))
    s.add(app)
    s.commit()
    s.refresh(app)
    dep = orch.submit_build(app.name, trigger="manual")
    return {"app": _summary(s, app, cfg), "deployment": dep.model_dump(mode="json")}


@router.get("/apps/{name}")
def get_app(app: App = Depends(deps.get_app), s: Session = Depends(db.get_session), cfg: Settings = Depends(deps.settings)):
    return _summary(s, app, cfg)


@router.patch("/apps/{name}")
def update_app(
    body: AppUpdate,
    app: App = Depends(deps.get_app),
    s: Session = Depends(db.get_session),
    cfg: Settings = Depends(deps.settings),
    orch: Orchestrator = Depends(deps.orchestrator),
):
    changes = body.model_dump(exclude_none=True)
    merged = {**app.model_dump(), **changes}
    try:
        check_consistency(merged)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    for k, v in changes.items():
        setattr(app, k, v)
    s.add(app)
    s.commit()
    s.refresh(app)
    dep = deps.apply_config(s, orch, app, trigger="config")
    return {"app": _summary(s, app, cfg), "deployment": dep.model_dump(mode="json") if dep else None}


@router.delete("/apps/{name}", status_code=204)
async def delete_app(request: Request, app: App = Depends(deps.get_app), s: Session = Depends(db.get_session)):
    try:
        await asyncio.to_thread(request.app.state.k8s_ops.delete_app, app.name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"could not delete Kubernetes resources: {e}")
    for model in (Deployment, MetricSample, Recommendation, PrescaleSchedule):
        s.exec(delete(model).where(model.app_name == app.name))
    s.delete(app)
    s.commit()
    if request.app.state.prescale:
        request.app.state.prescale.sync()


@router.post("/apps/{name}/deploy", status_code=202)
def deploy(app: App = Depends(deps.get_app), orch: Orchestrator = Depends(deps.orchestrator)):
    return orch.submit_build(app.name, trigger="manual").model_dump(mode="json")


@router.get("/apps/{name}/deployments")
def list_deployments(app: App = Depends(deps.get_app), s: Session = Depends(db.get_session), limit: int = Query(50, le=500)):
    q = select(Deployment).where(Deployment.app_name == app.name).order_by(Deployment.id.desc()).limit(limit)
    return [d.model_dump(mode="json") for d in s.exec(q).all()]


@router.post("/apps/{name}/rollback/{deployment_id}", status_code=202)
def rollback(
    deployment_id: int,
    app: App = Depends(deps.get_app),
    s: Session = Depends(db.get_session),
    orch: Orchestrator = Depends(deps.orchestrator),
):
    target = s.get(Deployment, deployment_id)
    if target is None or target.app_name != app.name or not target.spec_json:
        raise HTTPException(status_code=404, detail="deployment not found or it never reached the cluster")
    spec = AppSpec.from_json(target.spec_json)
    return orch.submit_spec(app.name, spec, trigger="rollback").model_dump(mode="json")


@router.get("/apps/{name}/status")
async def live_status(app: App = Depends(deps.get_app), cfg: Settings = Depends(deps.settings)):
    ns = cfg.apps_namespace

    def gather():
        return {
            "deployment": observe.deployment_status(app.name, ns),
            "pods": observe.list_pods(app.name, ns),
            "hpa": observe.hpa_status(app.name, ns),
            "usage": observe.pod_usage(app.name, ns),
        }

    try:
        return await asyncio.to_thread(gather)
    except (ApiException, ConfigException, OSError) as e:
        raise HTTPException(status_code=503, detail=f"cluster unavailable: {e}")


@router.get("/apps/{name}/events")
async def events(app: App = Depends(deps.get_app), cfg: Settings = Depends(deps.settings)):
    try:
        return await asyncio.to_thread(observe.recent_events, app.name, cfg.apps_namespace)
    except (ApiException, ConfigException, OSError) as e:
        raise HTTPException(status_code=503, detail=f"cluster unavailable: {e}")


@router.get("/apps/{name}/metrics")
def metrics(
    app: App = Depends(deps.get_app),
    s: Session = Depends(db.get_session),
    since: Optional[float] = Query(None, description="unix seconds; default: last 30 minutes"),
    until: Optional[float] = Query(None, description="unix seconds; default: now"),
):
    since = since if since is not None else time.time() - 30 * 60
    q = select(MetricSample).where(MetricSample.app_name == app.name, MetricSample.ts >= since)
    if until is not None:
        q = q.where(MetricSample.ts <= until)
    return [m.model_dump(mode="json") for m in s.exec(q.order_by(MetricSample.ts)).all()]
