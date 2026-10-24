from typing import Optional

from pydantic import BaseModel, Field, model_validator

# DNS-1035 label: used as the Deployment/Service/Ingress name and the <app>.<suffix> hostname.
NAME_PATTERN = r"^[a-z]([-a-z0-9]{0,30}[a-z0-9])?$"


class ResourceFields(BaseModel):
    cpu_request_m: Optional[int] = Field(default=None, ge=10, le=4000)
    cpu_limit_m: Optional[int] = Field(default=None, ge=10, le=8000)
    mem_request_mi: Optional[int] = Field(default=None, ge=16, le=8192)
    mem_limit_mi: Optional[int] = Field(default=None, ge=16, le=16384)
    hpa_min: Optional[int] = Field(default=None, ge=1, le=20)
    hpa_max: Optional[int] = Field(default=None, ge=1, le=50)
    hpa_cpu_target: Optional[int] = Field(default=None, ge=10, le=95)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    health_path: Optional[str] = Field(default=None, pattern=r"^/")
    branch: Optional[str] = Field(default=None, min_length=1, max_length=100)


class AppCreate(ResourceFields):
    name: str = Field(pattern=NAME_PATTERN)
    repo_url: str
    branch: str = "main"
    port: int = Field(default=8080, ge=1, le=65535)
    health_path: str = Field(default="/healthz", pattern=r"^/")
    cpu_request_m: int = Field(default=100, ge=10, le=4000)
    cpu_limit_m: int = Field(default=500, ge=10, le=8000)
    mem_request_mi: int = Field(default=128, ge=16, le=8192)
    mem_limit_mi: int = Field(default=256, ge=16, le=16384)
    hpa_min: int = Field(default=1, ge=1, le=20)
    hpa_max: int = Field(default=5, ge=1, le=50)
    hpa_cpu_target: int = Field(default=50, ge=10, le=95)


class AppUpdate(ResourceFields):
    pass


def check_consistency(values: dict) -> None:
    """Shared validation for create and for an update merged onto the existing app."""
    if values["cpu_limit_m"] < values["cpu_request_m"]:
        raise ValueError("cpu_limit_m must be >= cpu_request_m")
    if values["mem_limit_mi"] < values["mem_request_mi"]:
        raise ValueError("mem_limit_mi must be >= mem_request_mi")
    if values["hpa_max"] < values["hpa_min"]:
        raise ValueError("hpa_max must be >= hpa_min")


class PrescaleCreate(BaseModel):
    day_of_week: str = Field(default="*", pattern=r"^[a-z0-9*,\-/]+$")
    hour: int = Field(ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    duration_min: int = Field(default=60, ge=1, le=24 * 60)
    min_replicas: int = Field(ge=1, le=50)

    @model_validator(mode="after")
    def _valid_cron(self):
        from apscheduler.triggers.cron import CronTrigger

        try:
            CronTrigger(day_of_week=self.day_of_week)
        except ValueError as e:
            raise ValueError(f"invalid day_of_week: {e}") from e
        return self
