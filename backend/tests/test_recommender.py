import pytest

from app.intel.recommender import InsufficientData, percentile, recommend
from app.store.models import App, MetricSample

APP = App(name="demo", repo_url="https://github.com/x/y", webhook_secret="s")


def make_samples(n=40, overload=False):
    """Synthetic run: load ramps 0 -> 78 req/s; CPU = 20m idle per pod + 2m per request."""
    samples = []
    for i in range(n):
        rps = float(i * 2)
        pods = 1 + i // 10
        cpu = 20 * pods + 2 * rps
        samples.append(
            MetricSample(
                app_name="demo",
                ts=1_700_000_000 + i * 15,
                desired_replicas=pods,
                ready_replicas=pods,
                cpu_m=cpu,
                mem_mi=60.0 * pods,
                hpa_util=95 if overload and i % 2 else 45,
                hpa_target=50,
                rps=rps,
            )
        )
    return samples


def test_percentile_nearest_rank():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == 9
    assert percentile([5], 99) == 5


def test_requires_enough_samples():
    with pytest.raises(InsufficientData):
        recommend(make_samples(5), APP)


def test_resources_derived_from_per_pod_usage():
    payload, why = recommend(make_samples(), APP)
    assert payload["cpu_request_m"] % 10 == 0 and payload["cpu_request_m"] >= 50
    assert payload["cpu_limit_m"] >= 2 * payload["cpu_request_m"]
    assert payload["mem_request_mi"] >= 64 and payload["mem_limit_mi"] > payload["mem_request_mi"]
    assert any("Per-pod CPU p90" in line for line in why)


def test_capacity_from_linear_fit():
    payload, _ = recommend(make_samples(), APP)
    stats = payload["stats"]
    # 2m per request, plus one extra pod (20m idle) per 20 req/s of load -> ~3m per request overall.
    assert stats["cpu_per_request_m"] == pytest.approx(3.0, rel=0.1)
    assert stats["capacity_rps_per_pod"] > 0
    assert 1 <= payload["hpa_min"] < payload["hpa_max"]


def test_no_overload_keeps_70_percent_target_and_no_prescale():
    payload, _ = recommend(make_samples(), APP)
    assert payload["hpa_cpu_target"] == 70
    assert payload["prescale"] is None


def test_overload_lowers_target():
    payload, why = recommend(make_samples(overload=True), APP)
    assert payload["hpa_cpu_target"] == 60
    assert payload["stats"]["overload_fraction"] > 0.15


def test_max_replicas_capped_by_cluster_capacity():
    payload, why = recommend(make_samples(), APP, allocatable_cpu_m=200)
    assert payload["hpa_max"] <= max(payload["hpa_min"], int(200 * 0.8 // payload["cpu_request_m"]))
    assert any("bottleneck" in line for line in why)


def test_without_rps_keeps_replica_range():
    samples = make_samples()
    for s in samples:
        s.rps = None
    payload, why = recommend(samples, APP)
    assert (payload["hpa_min"], payload["hpa_max"]) == (APP.hpa_min, APP.hpa_max)
    assert any("left unchanged" in line for line in why)
