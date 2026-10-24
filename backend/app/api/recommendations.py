import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.api import deps
from app.config import Settings
from app.intel.recommender import CONFIG_FIELDS, InsufficientData, recommend
from app.k8s import observe
from app.pipeline.orchestrator import Orchestrator
from app.store import db
from app.store.models import App, MetricSample, Recommendation

log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(deps.require_token)])


def _out(rec: Recommendation) -> dict:
    return {**rec.model_dump(mode="json", exclude={"payload_json"}), "payload": json.loads(rec.payload_json), "rationale": rec.rationale.split("\n")}


@router.post("/apps/{name}/recommendations", status_code=201)
async def generate(
    app: App = Depends(deps.get_app),
    s: Session = Depends(db.get_session),
    cfg: Settings = Depends(deps.settings),
    window_min: int = Query(30, ge=1, le=7 * 24 * 60),
):
    since = time.time() - window_min * 60
    samples = s.exec(
        select(MetricSample).where(MetricSample.app_name == app.name, MetricSample.ts >= since).order_by(MetricSample.ts)
    ).all()
    try:
        allocatable = await asyncio.to_thread(observe.allocatable_cpu_m)
    except Exception:
        log.warning("could not read node allocatable CPU; maxReplicas will not be capped")
        allocatable = None
    try:
        payload, why = recommend(list(samples), app, allocatable, tz=cfg.timezone)
    except InsufficientData as e:
        raise HTTPException(status_code=422, detail=str(e))
    rec = Recommendation(app_name=app.name, payload_json=json.dumps(payload), rationale="\n".join(why))
    s.add(rec)
    s.commit()
    s.refresh(rec)
    return _out(rec)


@router.get("/apps/{name}/recommendations")
def list_recommendations(app: App = Depends(deps.get_app), s: Session = Depends(db.get_session)):
    q = select(Recommendation).where(Recommendation.app_name == app.name).order_by(Recommendation.id.desc()).limit(20)
    return [_out(r) for r in s.exec(q).all()]


def _get(s: Session, rec_id: int) -> Recommendation:
    rec = s.get(Recommendation, rec_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return rec


@router.post("/recommendations/{rec_id}/apply")
def apply(rec_id: int, s: Session = Depends(db.get_session), orch: Orchestrator = Depends(deps.orchestrator)):
    rec = _get(s, rec_id)
    app = s.get(App, rec.app_name)
    payload = json.loads(rec.payload_json)
    for field in CONFIG_FIELDS:
        setattr(app, field, payload[field])
    rec.status = "applied"
    s.add(app)
    s.add(rec)
    s.commit()
    s.refresh(app)
    dep = deps.apply_config(s, orch, app, trigger="recommendation")
    return {"recommendation": _out(rec), "deployment": dep.model_dump(mode="json") if dep else None}


@router.post("/recommendations/{rec_id}/dismiss")
def dismiss(rec_id: int, s: Session = Depends(db.get_session)):
    rec = _get(s, rec_id)
    rec.status = "dismissed"
    s.add(rec)
    s.commit()
    return _out(rec)
