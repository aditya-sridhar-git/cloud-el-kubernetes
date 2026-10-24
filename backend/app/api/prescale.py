from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from app.api import deps
from app.api.schemas import PrescaleCreate
from app.store import db
from app.store.models import App, PrescaleSchedule

router = APIRouter(dependencies=[Depends(deps.require_token)])


def _resync(request: Request) -> None:
    if request.app.state.prescale:
        request.app.state.prescale.sync()


@router.get("/apps/{name}/prescale")
def list_schedules(app: App = Depends(deps.get_app), s: Session = Depends(db.get_session)):
    return s.exec(select(PrescaleSchedule).where(PrescaleSchedule.app_name == app.name)).all()


@router.post("/apps/{name}/prescale", status_code=201)
def create_schedule(
    body: PrescaleCreate, request: Request, app: App = Depends(deps.get_app), s: Session = Depends(db.get_session)
):
    sched = PrescaleSchedule(app_name=app.name, **body.model_dump())
    s.add(sched)
    s.commit()
    s.refresh(sched)
    _resync(request)
    return sched


@router.delete("/prescale/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, request: Request, s: Session = Depends(db.get_session)):
    sched = s.get(PrescaleSchedule, schedule_id)
    if sched is None:
        raise HTTPException(status_code=404, detail="schedule not found")
    s.delete(sched)
    s.commit()
    _resync(request)
