from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (or backend/.env for local runs)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AWS (values come from `terraform output`)
    aws_region: str = "ap-south-1"
    ecr_repo_uri: str = ""
    source_bucket: str = ""
    codebuild_project: str = ""
    builder: Literal["docker", "codebuild"] = "codebuild"

    # Kubernetes
    apps_namespace: str = "apps"
    app_host_suffix: str = "127.0.0.1.nip.io"  # apps are served at <app>.<suffix>
    rollout_timeout_s: int = 180

    # Platform
    database_url: str = "sqlite:///./data/platform.db"
    api_token: str = "dev-token"
    public_base_url: str = ""  # defaults to http://platform.<app_host_suffix>
    collect_interval_s: int = 15
    timezone: str = "Asia/Kolkata"  # for pre-scale schedules and peak-hour detection
    enable_background: bool = True  # collector + pre-scale scheduler; tests turn this off

    @property
    def base_url(self) -> str:
        return self.public_base_url or f"http://platform.{self.app_host_suffix}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
