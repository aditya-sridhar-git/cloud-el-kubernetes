from tests.conftest import AUTH, create, wait_for_status


def test_api_requires_token(client):
    assert client.get("/api/apps").status_code == 401
    assert client.get("/api/apps", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/healthz").status_code == 200


def test_rejects_non_https_repo(client):
    r = client.post("/api/apps", json={"name": "x", "repo_url": "file:///etc"}, headers=AUTH)
    assert r.status_code == 422


def test_rejects_invalid_name_and_inconsistent_limits(client):
    bad_name = {"name": "Bad_Name", "repo_url": "https://github.com/a/b"}
    assert client.post("/api/apps", json=bad_name, headers=AUTH).status_code == 422
    bad_limits = {"name": "ok", "repo_url": "https://github.com/a/b", "cpu_request_m": 500, "cpu_limit_m": 100}
    assert client.post("/api/apps", json=bad_limits, headers=AUTH).status_code == 422


def test_create_builds_and_deploys(client, ops, builder):
    body = create(client)
    assert body["app"]["url"] == "http://demo.1.2.3.4.nip.io"
    dep = wait_for_status(client, "demo", body["deployment"]["id"])
    assert dep["status"] == "SUCCEEDED"
    assert dep["image"].endswith(f"demo-{dep['commit_sha'][:12]}")
    assert ops.applied[-1].image == dep["image"]
    assert ops.applied[-1].cpu_request_m == 100


def test_duplicate_app_conflicts(client):
    create(client)
    r = client.post("/api/apps", json={"name": "demo", "repo_url": "https://github.com/a/b"}, headers=AUTH)
    assert r.status_code == 409


def test_build_failure_marks_failed_without_touching_cluster(client, ops, builder):
    builder.fail_with = "Dockerfile syntax error"
    body = create(client)
    dep = wait_for_status(client, "demo", body["deployment"]["id"])
    assert dep["status"] == "FAILED"
    assert "Dockerfile syntax error" in dep["error"]
    assert ops.applied == []


def test_failed_rollout_without_history_is_failed(client, ops):
    ops.rollout_results = [(False, "progress deadline exceeded")]
    dep = wait_for_status(client, "demo", create(client)["deployment"]["id"])
    assert dep["status"] == "FAILED"
    assert "no previous stable version" in dep["error"]


def test_failed_rollout_rolls_back_to_last_stable(client, ops):
    first = wait_for_status(client, "demo", create(client)["deployment"]["id"])
    good_image = first["image"]

    ops.rollout_results = [(False, "pod demo-x: CrashLoopBackOff")]
    second = client.post("/api/apps/demo/deploy", headers=AUTH).json()
    dep = wait_for_status(client, "demo", second["id"])
    assert dep["status"] == "ROLLED_BACK"
    assert f"deployment #{first['id']}" in dep["error"]
    assert ops.applied[-1].image == good_image  # previous spec re-applied


def test_manual_rollback_reapplies_old_spec(client, ops):
    first = wait_for_status(client, "demo", create(client)["deployment"]["id"])
    second = wait_for_status(client, "demo", client.post("/api/apps/demo/deploy", headers=AUTH).json()["id"])
    assert second["image"] != first["image"]

    r = client.post(f"/api/apps/demo/rollback/{first['id']}", headers=AUTH)
    assert r.status_code == 202
    dep = wait_for_status(client, "demo", r.json()["id"])
    assert dep["status"] == "SUCCEEDED" and dep["trigger"] == "rollback"
    assert ops.applied[-1].image == first["image"]


def saved_app(client):
    return client.get("/api/apps/demo", headers=AUTH).json()


def test_automatic_rollback_restores_saved_settings(client, ops):
    wait_for_status(client, "demo", create(client)["deployment"]["id"])

    ops.rollout_results = [(False, "readiness probe failed")]
    r = client.patch("/api/apps/demo", json={"health_path": "/does-not-exist"}, headers=AUTH)
    dep = wait_for_status(client, "demo", r.json()["deployment"]["id"])
    assert dep["status"] == "ROLLED_BACK"
    assert "restored its settings" in dep["error"]
    assert saved_app(client)["health_path"] == "/healthz"

    # The bad value must not come back on the next redeploy.
    redeploy = client.post("/api/apps/demo/deploy", headers=AUTH).json()
    assert wait_for_status(client, "demo", redeploy["id"])["status"] == "SUCCEEDED"
    assert ops.applied[-1].health_path == "/healthz"


def test_manual_rollback_restores_that_deployments_settings(client, ops):
    first = wait_for_status(client, "demo", create(client)["deployment"]["id"])
    r = client.patch("/api/apps/demo", json={"cpu_request_m": 200, "cpu_limit_m": 800}, headers=AUTH)
    wait_for_status(client, "demo", r.json()["deployment"]["id"])
    assert saved_app(client)["cpu_request_m"] == 200

    r = client.post(f"/api/apps/demo/rollback/{first['id']}", headers=AUTH)
    assert wait_for_status(client, "demo", r.json()["id"])["status"] == "SUCCEEDED"
    assert (saved_app(client)["cpu_request_m"], saved_app(client)["cpu_limit_m"]) == (100, 500)


def test_good_change_queued_behind_a_failed_one_is_kept(client, ops):
    wait_for_status(client, "demo", create(client)["deployment"]["id"])

    ops.rollout_results = [(False, "readiness probe failed")]
    bad = client.patch("/api/apps/demo", json={"hpa_max": 9}, headers=AUTH).json()["deployment"]
    good = client.patch("/api/apps/demo", json={"hpa_max": 7}, headers=AUTH).json()["deployment"]
    assert wait_for_status(client, "demo", bad["id"])["status"] == "ROLLED_BACK"
    assert wait_for_status(client, "demo", good["id"])["status"] == "SUCCEEDED"
    assert saved_app(client)["hpa_max"] == 7
    assert ops.applied[-1].hpa_max == 7


def test_failed_build_rollout_keeps_saved_settings(client, ops):
    wait_for_status(client, "demo", create(client)["deployment"]["id"])
    ops.rollout_results = [(False, "pod demo-x: CrashLoopBackOff")]
    second = client.post("/api/apps/demo/deploy", headers=AUTH).json()
    assert wait_for_status(client, "demo", second["id"])["status"] == "ROLLED_BACK"
    assert saved_app(client)["cpu_request_m"] == 100


def test_config_change_redeploys_running_image(client, ops):
    first = wait_for_status(client, "demo", create(client)["deployment"]["id"])
    r = client.patch("/api/apps/demo", json={"hpa_max": 8, "cpu_request_m": 200, "cpu_limit_m": 800}, headers=AUTH)
    assert r.status_code == 200
    dep = wait_for_status(client, "demo", r.json()["deployment"]["id"])
    assert dep["trigger"] == "config" and dep["image"] == first["image"]
    assert (ops.applied[-1].hpa_max, ops.applied[-1].cpu_request_m) == (8, 200)


def test_config_change_rejects_max_below_min(client):
    create(client)
    r = client.patch("/api/apps/demo", json={"hpa_min": 5, "hpa_max": 2}, headers=AUTH)
    assert r.status_code == 422


def test_delete_removes_cluster_resources(client, ops):
    wait_for_status(client, "demo", create(client)["deployment"]["id"])
    assert client.delete("/api/apps/demo", headers=AUTH).status_code == 204
    assert ops.deleted == ["demo"]
    assert client.get("/api/apps/demo", headers=AUTH).status_code == 404


def test_websocket_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect
    import pytest

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws/apps/demo?token=wrong") as ws:
            ws.receive_json()


def test_websocket_streams_pipeline_events(client):
    with client.websocket_connect("/api/ws/apps/demo?token=test-token") as ws:
        create(client)
        seen = set()
        while "SUCCEEDED" not in seen:
            msg = ws.receive_json()
            if msg["type"] == "deployment":
                seen.add(msg["deployment"]["status"])
            if msg["type"] == "build_log":
                seen.add("log")
    assert {"QUEUED", "CLONING", "BUILDING", "APPLYING", "ROLLING_OUT", "SUCCEEDED", "log"} <= seen
