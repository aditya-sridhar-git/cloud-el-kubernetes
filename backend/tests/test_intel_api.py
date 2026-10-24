from app.store import db
from tests.conftest import AUTH, create, wait_for_status
from tests.test_recommender import make_samples


def seed_samples(app_name="demo", **kw):
    import time

    with db.session() as s:
        now = time.time()
        for i, sample in enumerate(make_samples(**kw)):
            sample.app_name = app_name
            sample.ts = now - 600 + i * 15
            s.add(sample)
        s.commit()


def test_recommendation_needs_data(client):
    create(client)
    r = client.post("/api/apps/demo/recommendations", headers=AUTH)
    assert r.status_code == 422
    assert "samples" in r.json()["detail"]


def test_generate_and_apply_recommendation(client, ops):
    first = wait_for_status(client, "demo", create(client)["deployment"]["id"])
    seed_samples()

    r = client.post("/api/apps/demo/recommendations?window_min=30", headers=AUTH)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["status"] == "new" and len(rec["rationale"]) >= 3
    payload = rec["payload"]

    r = client.post(f"/api/recommendations/{rec['id']}/apply", headers=AUTH)
    assert r.status_code == 200
    dep = wait_for_status(client, "demo", r.json()["deployment"]["id"])
    assert dep["trigger"] == "recommendation" and dep["image"] == first["image"]
    applied = ops.applied[-1]
    assert (applied.cpu_request_m, applied.hpa_min, applied.hpa_max, applied.hpa_cpu_target) == (
        payload["cpu_request_m"],
        payload["hpa_min"],
        payload["hpa_max"],
        payload["hpa_cpu_target"],
    )
    listed = client.get("/api/apps/demo/recommendations", headers=AUTH).json()
    assert listed[0]["status"] == "applied"


def test_dismiss_recommendation(client):
    create(client)
    seed_samples()
    rec = client.post("/api/apps/demo/recommendations", headers=AUTH).json()
    assert client.post(f"/api/recommendations/{rec['id']}/dismiss", headers=AUTH).json()["status"] == "dismissed"


def test_metrics_endpoint_returns_samples_in_window(client):
    create(client)
    seed_samples()
    rows = client.get("/api/apps/demo/metrics", headers=AUTH).json()
    assert len(rows) == 40
    assert rows == sorted(rows, key=lambda r: r["ts"])


def test_prescale_crud(client):
    create(client)
    r = client.post("/api/apps/demo/prescale", json={"hour": 9, "minute": 50, "min_replicas": 3, "day_of_week": "mon-fri"}, headers=AUTH)
    assert r.status_code == 201, r.text
    sched_id = r.json()["id"]
    assert len(client.get("/api/apps/demo/prescale", headers=AUTH).json()) == 1
    assert client.delete(f"/api/prescale/{sched_id}", headers=AUTH).status_code == 204
    assert client.get("/api/apps/demo/prescale", headers=AUTH).json() == []


def test_prescale_rejects_bad_cron(client):
    create(client)
    r = client.post("/api/apps/demo/prescale", json={"hour": 9, "min_replicas": 3, "day_of_week": "someday"}, headers=AUTH)
    assert r.status_code == 422


def test_prescale_manager_raises_and_restores_floor(client, ops):
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.intel.prescale import PrescaleManager

    create(client)
    sched_id = client.post("/api/apps/demo/prescale", json={"hour": 9, "min_replicas": 4}, headers=AUTH).json()["id"]
    scheduler = BackgroundScheduler()
    mgr = PrescaleManager(scheduler, ops, client.app.state.hub, "UTC")
    mgr.start(sched_id)
    assert ops.hpa_ranges[-1] == ("demo", 4, 5)
    assert scheduler.get_job(f"prescale-{sched_id}-end") is not None
    mgr.end("demo")
    assert ops.hpa_ranges[-1] == ("demo", 1, 5)
