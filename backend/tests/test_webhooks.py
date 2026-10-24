import hashlib
import hmac
import json

from app.api.webhooks import verify_signature
from tests.conftest import AUTH, create, wait_for_status


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def push_body(branch="main", url="https://github.com/example/demo.git"):
    return json.dumps({"ref": f"refs/heads/{branch}", "repository": {"clone_url": url, "html_url": url[:-4]}}).encode()


def test_verify_signature():
    body = b'{"a":1}'
    assert verify_signature("s3cret", body, sign("s3cret", body))
    assert not verify_signature("other", body, sign("s3cret", body))
    assert not verify_signature("s3cret", body, "")


def test_push_with_valid_signature_triggers_deploy(client):
    secret = create(client)["app"]["webhook_secret"]
    body = push_body()
    r = client.post(
        "/api/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(secret, body)},
    )
    assert r.status_code == 202, r.text
    triggered = r.json()["triggered"]
    assert [t["app"] for t in triggered] == ["demo"]
    dep = wait_for_status(client, "demo", triggered[0]["deployment_id"])
    assert dep["trigger"] == "webhook" and dep["status"] == "SUCCEEDED"


def test_push_with_bad_signature_is_rejected(client):
    create(client)
    body = push_body()
    r = client.post(
        "/api/webhooks/github", content=body, headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign("wrong", body)}
    )
    assert r.status_code == 401


def test_push_to_other_branch_is_ignored(client):
    secret = create(client)["app"]["webhook_secret"]
    body = push_body(branch="feature")
    r = client.post(
        "/api/webhooks/github", content=body, headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(secret, body)}
    )
    assert r.status_code == 202 and r.json()["triggered"] == []


def test_ping_is_acknowledged(client):
    r = client.post("/api/webhooks/github", content=b"{}", headers={"X-GitHub-Event": "ping"})
    assert r.status_code == 202 and r.json()["message"] == "pong"
