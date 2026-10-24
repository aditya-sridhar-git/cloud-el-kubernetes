"""GitHub push webhook: redeploys every app registered for the pushed repo and branch.

Each app has its own secret; a push only triggers apps whose secret validates the
X-Hub-Signature-256 header.
"""
import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlmodel import Session, select

from app.api import deps
from app.pipeline.orchestrator import Orchestrator
from app.pipeline.source import normalize_repo_url
from app.store import db
from app.store.models import App

router = APIRouter()


def verify_signature(secret: str, body: bytes, header: str) -> bool:
    if not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


@router.post("/webhooks/github", status_code=202)
async def github(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
    s: Session = Depends(db.get_session),
    orch: Orchestrator = Depends(deps.orchestrator),
):
    body = await request.body()
    if x_github_event == "ping":
        return {"ok": True, "message": "pong"}
    if x_github_event != "push":
        return {"ok": True, "message": f"ignored event '{x_github_event}'"}

    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON")
    if payload.get("deleted"):
        return {"ok": True, "message": "branch deletion ignored"}

    ref = payload.get("ref", "")
    if not ref.startswith("refs/heads/"):
        return {"ok": True, "message": "not a branch push"}
    branch = ref.removeprefix("refs/heads/")
    repo = payload.get("repository") or {}
    urls = {normalize_repo_url(u) for u in (repo.get("clone_url"), repo.get("html_url")) if u}

    candidates = [a for a in s.exec(select(App).where(App.branch == branch)).all() if normalize_repo_url(a.repo_url) in urls]
    if not candidates:
        return {"ok": True, "triggered": [], "message": "no app registered for this repo and branch"}

    triggered = []
    for app in candidates:
        if verify_signature(app.webhook_secret, body, x_hub_signature_256):
            dep = orch.submit_build(app.name, trigger="webhook")
            triggered.append({"app": app.name, "deployment_id": dep.id})
    if not triggered:
        raise HTTPException(status_code=401, detail="signature does not match any registered app's webhook secret")
    return {"ok": True, "triggered": triggered}
