"""Source handling, the collector's rate calculation and the CodeBuild builder."""
import zipfile
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber

from app.config import Settings
from app.events import Hub
from app.intel.collector import Collector
from app.pipeline.builders import BuildError, CodeBuildBuilder
from app.pipeline.source import SourceError, normalize_repo_url, validate_repo_url, zip_source


@pytest.mark.parametrize("url", ["https://github.com/user/repo.git", "https://gitlab.com/group/sub/repo"])
def test_accepts_https_urls(url):
    assert validate_repo_url(url) == url


@pytest.mark.parametrize("url", ["file:///etc/passwd", "git@github.com:u/r.git", "http://github.com/u/r", "/tmp/repo", "https://h/r;rm -rf"])
def test_rejects_unsafe_urls(url):
    with pytest.raises(SourceError):
        validate_repo_url(url)


def test_normalize_repo_url():
    assert normalize_repo_url("https://GitHub.com/User/Repo.git/") == normalize_repo_url("https://github.com/user/repo")


def test_zip_excludes_git_dir(tmp_path: Path):
    src = tmp_path / "src"
    (src / ".git").mkdir(parents=True)
    (src / ".git" / "config").write_text("x")
    (src / "app").mkdir()
    (src / "Dockerfile").write_text("FROM scratch")
    (src / "app" / "main.py").write_text("print(1)")
    with zipfile.ZipFile(zip_source(src, tmp_path / "out.zip")) as zf:
        assert sorted(zf.namelist()) == ["Dockerfile", "app/main.py"]


def test_collector_rate_from_counter_deltas():
    c = Collector(Settings(_env_file=None), Hub())
    assert c._rate("demo", 100.0, {"pod-a": 50}) is None  # first tick has no baseline
    # pod-a served 150 more; pod-b is new and served 30 since it started; 10 s elapsed.
    assert c._rate("demo", 110.0, {"pod-a": 200, "pod-b": 30}) == 18.0
    # pod-a restarted (counter reset) -> ignored rather than negative.
    assert c._rate("demo", 120.0, {"pod-a": 5, "pod-b": 50}) == 2.0


class FakeS3:
    def __init__(self):
        self.uploads = []

    def upload_file(self, filename, bucket, key):
        self.uploads.append((bucket, key))


def _builder(tmp_path):
    settings = Settings(
        _env_file=None, source_bucket="src-bucket", codebuild_project="builder", ecr_repo_uri="123.dkr.ecr.ap-south-1.amazonaws.com/apps"
    )
    codebuild = boto3.client("codebuild", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="x")
    logs = boto3.client("logs", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="x")
    src = tmp_path / "src"
    src.mkdir()
    (src / "Dockerfile").write_text("FROM scratch")
    s3 = FakeS3()
    return CodeBuildBuilder(settings, s3=s3, codebuild=codebuild, logs=logs, poll_s=0), codebuild, logs, s3, src


def _build(status, phase="POST_BUILD"):
    return {"builds": [{"id": "b:1", "buildStatus": status, "currentPhase": phase, "logs": {"groupName": "g", "streamName": "s"}}]}


def test_codebuild_success_streams_logs_and_returns_image(tmp_path):
    builder, codebuild, logs, s3, src = _builder(tmp_path)
    sha = "abcdef1234567890" * 2
    with Stubber(codebuild) as cb, Stubber(logs) as lg:
        cb.add_response(
            "start_build",
            {"build": {"id": "b:1"}},
            {
                "projectName": "builder",
                "sourceTypeOverride": "S3",
                "sourceLocationOverride": f"src-bucket/sources/demo/{sha}.zip",
                "environmentVariablesOverride": [{"name": "IMAGE_TAG", "value": "demo-abcdef123456", "type": "PLAINTEXT"}],
            },
        )
        cb.add_response("batch_get_builds", _build("IN_PROGRESS", "BUILD"))
        lg.add_response("get_log_events", {"events": [{"message": "Step 1/2\n"}], "nextForwardToken": "t1"})
        lg.add_response("get_log_events", {"events": [], "nextForwardToken": "t1"})
        cb.add_response("batch_get_builds", _build("SUCCEEDED"))
        lg.add_response("get_log_events", {"events": [{"message": "Pushed\n"}], "nextForwardToken": "t2"})
        lg.add_response("get_log_events", {"events": [], "nextForwardToken": "t2"})
        lg.add_response("get_log_events", {"events": [], "nextForwardToken": "t2"})
        lines = []
        image = builder.build("demo", sha, src, lines.append)

    assert image == "123.dkr.ecr.ap-south-1.amazonaws.com/apps:demo-abcdef123456"
    assert s3.uploads == [("src-bucket", f"sources/demo/{sha}.zip")]
    assert "Step 1/2" in lines and "Pushed" in lines


def test_codebuild_failure_raises(tmp_path):
    builder, codebuild, logs, _, src = _builder(tmp_path)
    with Stubber(codebuild) as cb, Stubber(logs) as lg:
        cb.add_response("start_build", {"build": {"id": "b:1"}})
        cb.add_response("batch_get_builds", _build("FAILED", "BUILD"))
        lg.add_response("get_log_events", {"events": [{"message": "error: no such file\n"}], "nextForwardToken": "t1"})
        lg.add_response("get_log_events", {"events": [], "nextForwardToken": "t1"})
        lg.add_response("get_log_events", {"events": [], "nextForwardToken": "t1"})
        with pytest.raises(BuildError, match="FAILED in phase BUILD"):
            builder.build("demo", "f" * 40, src, lambda line: None)
