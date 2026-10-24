from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DeployStatus:
    QUEUED = "QUEUED"
    CLONING = "CLONING"
    BUILDING = "BUILDING"
    APPLYING = "APPLYING"
    ROLLING_OUT = "ROLLING_OUT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"

    IN_PROGRESS = {QUEUED, CLONING, BUILDING, APPLYING, ROLLING_OUT}


class App(SQLModel, table=True):
    name: str = Field(primary_key=True)
    repo_url: str
    branch: str = "main"
    port: int = 8080
    health_path: str = "/healthz"

    # Resources: HPA CPU utilisation is measured against cpu_request_m.
    cpu_request_m: int = 100
    cpu_limit_m: int = 500
    mem_request_mi: int = 128
    mem_limit_mi: int = 256

    hpa_min: int = 1
    hpa_max: int = 5
    hpa_cpu_target: int = 50

    webhook_secret: str
    created_at: datetime = Field(default_factory=utcnow)


class Deployment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_name: str = Field(index=True)
    trigger: str  # manual | webhook | rollback | config | recommendation
    status: str = DeployStatus.QUEUED
    commit_sha: Optional[str] = None
    image: Optional[str] = None
    spec_json: Optional[str] = None  # the exact AppSpec applied; rollback re-applies this
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None


class MetricSample(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_name: str = Field(index=True)
    ts: float = Field(index=True)  # unix seconds
    desired_replicas: int
    ready_replicas: int
    cpu_m: float  # total across pods, millicores
    mem_mi: float  # total across pods, MiB
    hpa_util: Optional[int] = None  # average CPU utilisation (% of request) reported by the HPA
    hpa_target: Optional[int] = None
    rps: Optional[float] = None  # requests/second across pods, from the apps' /metrics counters


class Recommendation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_name: str = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow)
    payload_json: str
    rationale: str
    status: str = "new"  # new | applied | dismissed


class PrescaleSchedule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_name: str = Field(index=True)
    day_of_week: str = "*"  # APScheduler cron syntax, e.g. "mon-fri"
    hour: int
    minute: int = 0
    duration_min: int = 60
    min_replicas: int
    enabled: bool = True
