import itertools
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.pipeline.builders import BuildError

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeOps:
    """Stands in for K8sOps: records applied specs, returns scripted rollout results."""

    def __init__(self):
        self.applied = []
        self.rollout_results = []  # list of (ok, message); default success
        self.deleted = []
        self.hpa_ranges = []

    def apply_spec(self, spec):
        self.applied.append(spec)

    def wait_for_rollout(self, name, on_progress=None):
        if on_progress:
            on_progress("1/1 updated")
        return self.rollout_results.pop(0) if self.rollout_results else (True, "1/1 replicas available")

    def delete_app(self, name):
        self.deleted.append(name)

    def set_hpa_range(self, name, lo, hi):
        self.hpa_ranges.append((name, lo, hi))


class FakeBuilder:
    def __init__(self):
        self.fail_with = None
        self.builds = []

    def build(self, app_name, sha, src_dir, log):
        log("Step 1/3 : FROM python:3.12-slim")
        if self.fail_with:
            raise BuildError(self.fail_with)
        self.builds.append((app_name, sha))
        return f"123.dkr.ecr.ap-south-1.amazonaws.com/apps:{app_name}-{sha[:12]}"


_shas = itertools.count(1)


def fake_cloner(repo_url, branch, dest: Path) -> str:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "Dockerfile").write_text("FROM scratch\n")
    return f"{next(_shas):012x}" + "0" * 28  # unique in the first 12 chars, which form the image tag


@pytest.fixture
def ops():
    return FakeOps()


@pytest.fixture
def builder():
    return FakeBuilder()


@pytest.fixture
def client(tmp_path, ops, builder):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        api_token=TOKEN,
        app_host_suffix="1.2.3.4.nip.io",
        enable_background=False,
        _env_file=None,
    )
    app = create_app(settings, builder=builder, k8s_ops=ops, cloner=fake_cloner)
    with TestClient(app) as c:
        yield c


def wait_for_status(client, app_name, dep_id, done=("SUCCEEDED", "FAILED", "ROLLED_BACK"), timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        deps = client.get(f"/api/apps/{app_name}/deployments", headers=AUTH).json()
        dep = next(d for d in deps if d["id"] == dep_id)
        if dep["status"] in done:
            return dep
        time.sleep(0.05)
    raise AssertionError(f"deployment {dep_id} did not finish: {dep}")


def create(client, name="demo", **overrides):
    body = {"name": name, "repo_url": "https://github.com/example/demo.git", **overrides}
    r = client.post("/api/apps", json=body, headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()
