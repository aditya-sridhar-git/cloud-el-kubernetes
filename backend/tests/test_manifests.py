from app.k8s.manifests import AppSpec, all_manifests, deployment, hpa, ingress, service

SPEC = AppSpec(
    name="demo",
    image="repo:demo-abc",
    version="abc123",
    port=8080,
    health_path="/healthz",
    cpu_request_m=150,
    cpu_limit_m=600,
    mem_request_mi=128,
    mem_limit_mi=256,
    hpa_min=2,
    hpa_max=6,
    hpa_cpu_target=65,
)


def test_deployment_always_sets_requests_and_limits():
    container = deployment(SPEC, "apps")["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"] == {
        "requests": {"cpu": "150m", "memory": "128Mi"},
        "limits": {"cpu": "600m", "memory": "256Mi"},
    }


def test_deployment_leaves_replicas_to_the_hpa():
    assert "replicas" not in deployment(SPEC, "apps")["spec"]


def test_deployment_rolling_update_and_probes():
    d = deployment(SPEC, "apps")
    assert d["spec"]["strategy"]["rollingUpdate"] == {"maxSurge": 1, "maxUnavailable": 0}
    c = d["spec"]["template"]["spec"]["containers"][0]
    assert c["readinessProbe"]["httpGet"] == {"path": "/healthz", "port": 8080}
    assert c["livenessProbe"]["httpGet"] == {"path": "/healthz", "port": 8080}
    assert d["spec"]["template"]["metadata"]["labels"]["platform/version"] == "abc123"


def test_service_and_ingress_route_to_the_app_port():
    assert service(SPEC, "apps")["spec"]["ports"][0]["targetPort"] == 8080
    rule = ingress(SPEC, "apps", "1.2.3.4.nip.io")["spec"]["rules"][0]
    assert rule["host"] == "demo.1.2.3.4.nip.io"
    assert rule["http"]["paths"][0]["backend"]["service"] == {"name": "demo", "port": {"number": 80}}


def test_hpa_uses_spec_range_and_target():
    h = hpa(SPEC, "apps")["spec"]
    assert (h["minReplicas"], h["maxReplicas"]) == (2, 6)
    assert h["metrics"][0]["resource"]["target"] == {"type": "Utilization", "averageUtilization": 65}
    assert h["scaleTargetRef"]["name"] == "demo"


def test_all_manifests_share_namespace_and_labels():
    for m in all_manifests(SPEC, "apps", "x.nip.io"):
        assert m["metadata"]["namespace"] == "apps"
        assert m["metadata"]["labels"]["app"] == "demo"


def test_spec_json_round_trip():
    assert AppSpec.from_json(SPEC.to_json()) == SPEC
