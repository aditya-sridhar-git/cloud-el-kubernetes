import hmac
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlmodel import Session

from app.config import Settings
from app.k8s.manifests import AppSpec, app_host
from app.pipeline.orchestrator import Orchestrator
from app.store import db
from app.store.models import App, Deployment


def settings(request: Request) -> Settings:
    return request.app.state.settings


def orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def token_ok(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode(), expected.encode())


def require_token(request: Request, authorization: str = Header(default="")) -> None:
    expected = request.app.state.settings.api_token
    if not authorization.startswith("Bearer ") or not token_ok(authorization[7:], expected):
        raise HTTPException(status_code=401, detail="missing or invalid API token")


def get_app(name: str, s: Session = Depends(db.get_session)) -> App:
    app = s.get(App, name)
    if app is None:
        raise HTTPException(status_code=404, detail=f"app '{name}' not found")
    return app


def app_url(app_name: str, cfg: Settings) -> str:
    return f"http://{app_host(app_name, cfg.app_host_suffix)}"


def webhook_url(cfg: Settings) -> str:
    return f"{cfg.base_url}/api/webhooks/github"


def apply_config(s: Session, orch: Orchestrator, app: App, trigger: str) -> Optional[Deployment]:
    """Roll out the app's current config with the image that is already running.

    If the app has never deployed successfully, the new config simply takes effect on the next build.
    """
    last = db.last_successful(s, app.name)
    if last is None or not last.spec_json:
        return None
    running = AppSpec.from_json(last.spec_json)
    spec = AppSpec.from_app(app, image=running.image, version=running.version)
    return orch.submit_spec(app.name, spec, trigger)
