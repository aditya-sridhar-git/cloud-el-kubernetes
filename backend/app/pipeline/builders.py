"""Image builders. Both produce an image in ECR and return its URI.

- DockerSdkBuilder: builds with the local Docker daemon (used first, to get the pipeline working
  end-to-end from a laptop).
- CodeBuildBuilder: zips the source to S3 and runs the CodeBuild project (the target setup:
  no Docker daemon on the backend host, builds isolated from the platform).
"""
import base64
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional, Protocol

import boto3

from app.config import Settings
from app.pipeline.source import zip_source

LogFn = Callable[[str], None]


class BuildError(Exception):
    pass


class Builder(Protocol):
    def build(self, app_name: str, sha: str, src_dir: Path, log: LogFn) -> str: ...


def image_tag(app_name: str, sha: str) -> str:
    return f"{app_name}-{sha[:12]}"


class DockerSdkBuilder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build(self, app_name: str, sha: str, src_dir: Path, log: LogFn) -> str:
        import docker  # imported lazily: only needed when this builder is selected

        repo = self.settings.ecr_repo_uri
        tag = image_tag(app_name, sha)
        api = docker.from_env().api

        for chunk in api.build(path=str(src_dir), tag=f"{repo}:{tag}", rm=True, decode=True):
            if "error" in chunk:
                raise BuildError(chunk["error"].strip())
            if chunk.get("stream", "").strip():
                log(chunk["stream"].rstrip())

        ecr = boto3.client("ecr", region_name=self.settings.aws_region)
        auth = ecr.get_authorization_token()["authorizationData"][0]
        user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
        for chunk in api.push(repo, tag=tag, stream=True, decode=True, auth_config={"username": user, "password": password}):
            if "error" in chunk:
                raise BuildError(chunk["error"].strip())
            if chunk.get("status") and not chunk.get("progressDetail"):
                log(f"push: {chunk['status']}")
        return f"{repo}:{tag}"


class CodeBuildBuilder:
    TERMINAL_FAILURES = {"FAILED", "FAULT", "STOPPED", "TIMED_OUT"}

    def __init__(self, settings: Settings, s3=None, codebuild=None, logs=None, poll_s: float = 5) -> None:
        self.settings = settings
        self.s3 = s3 or boto3.client("s3", region_name=settings.aws_region)
        self.codebuild = codebuild or boto3.client("codebuild", region_name=settings.aws_region)
        self.logs = logs or boto3.client("logs", region_name=settings.aws_region)
        self.poll_s = poll_s

    def build(self, app_name: str, sha: str, src_dir: Path, log: LogFn) -> str:
        tag = image_tag(app_name, sha)
        key = f"sources/{app_name}/{sha}.zip"

        with tempfile.TemporaryDirectory() as tmp:
            archive = zip_source(src_dir, Path(tmp) / "source.zip")
            self.s3.upload_file(str(archive), self.settings.source_bucket, key)
        log(f"uploaded source to s3://{self.settings.source_bucket}/{key}")

        build = self.codebuild.start_build(
            projectName=self.settings.codebuild_project,
            sourceTypeOverride="S3",
            sourceLocationOverride=f"{self.settings.source_bucket}/{key}",
            environmentVariablesOverride=[{"name": "IMAGE_TAG", "value": tag, "type": "PLAINTEXT"}],
        )["build"]
        build_id = build["id"]
        log(f"started CodeBuild {build_id}")

        next_token: Optional[str] = None
        while True:
            build = self.codebuild.batch_get_builds(ids=[build_id])["builds"][0]
            next_token = self._tail_logs(build, next_token, log)
            status = build["buildStatus"]
            if status == "SUCCEEDED":
                self._tail_logs(build, next_token, log)  # flush the last lines
                return f"{self.settings.ecr_repo_uri}:{tag}"
            if status in self.TERMINAL_FAILURES:
                self._tail_logs(build, next_token, log)
                raise BuildError(f"CodeBuild {status} in phase {build.get('currentPhase', '?')}")
            time.sleep(self.poll_s)

    def _tail_logs(self, build: dict, token: Optional[str], log: LogFn) -> Optional[str]:
        info = build.get("logs") or {}
        group, stream = info.get("groupName"), info.get("streamName")
        if not group or not stream:
            return token  # log stream is created a few seconds after the build starts
        while True:
            kwargs = {"logGroupName": group, "logStreamName": stream, "startFromHead": True}
            if token:
                kwargs["nextToken"] = token
            try:
                resp = self.logs.get_log_events(**kwargs)
            except self.logs.exceptions.ResourceNotFoundException:
                return token
            for event in resp["events"]:
                log(event["message"].rstrip())
            new_token = resp["nextForwardToken"]
            if new_token == token:
                return token
            token = new_token


def make_builder(settings: Settings) -> Builder:
    return DockerSdkBuilder(settings) if settings.builder == "docker" else CodeBuildBuilder(settings)
