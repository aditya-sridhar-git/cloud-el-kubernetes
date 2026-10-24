"""Read-side Kubernetes queries: pods, metrics, HPA status, request counters, events, logs."""
import re
from typing import Any, Optional

from kubernetes.client.exceptions import ApiException
from kubernetes.utils import parse_quantity

from app.k8s import client as k8s

_COUNTER_RE = re.compile(r"^http_requests_total\s+([0-9.eE+-]+)\s*$", re.MULTILINE)


def deployment_status(name: str, namespace: str) -> Optional[dict[str, Any]]:
    try:
        d = k8s.apps().read_namespaced_deployment_status(name, namespace)
    except ApiException as e:
        if e.status == 404:
            return None
        raise
    st = d.status
    return {
        "desired": d.spec.replicas or 0,
        "ready": st.ready_replicas or 0,
        "updated": st.updated_replicas or 0,
        "available": st.available_replicas or 0,
        "image": d.spec.template.spec.containers[0].image,
    }


def list_pods(name: str, namespace: str) -> list[dict[str, Any]]:
    pods = k8s.core().list_namespaced_pod(namespace, label_selector=f"app={name}").items
    out = []
    for p in pods:
        statuses = p.status.container_statuses or []
        reason = None
        for cs in statuses:
            if cs.state and cs.state.waiting:
                reason = cs.state.waiting.reason
            elif cs.state and cs.state.terminated:
                reason = cs.state.terminated.reason
        out.append(
            {
                "name": p.metadata.name,
                "phase": p.status.phase,
                "ready": bool(statuses) and all(cs.ready for cs in statuses),
                "restarts": sum(cs.restart_count or 0 for cs in statuses),
                "reason": reason,
                "node": p.spec.node_name,
                "version": (p.metadata.labels or {}).get("platform/version"),
                "started_at": p.status.start_time.isoformat() if p.status.start_time else None,
            }
        )
    return sorted(out, key=lambda x: x["name"])


def pod_usage(name: str, namespace: str) -> dict[str, dict[str, float]]:
    """Per-pod CPU (millicores) and memory (MiB) from metrics.k8s.io (Metrics Server)."""
    try:
        res = k8s.custom().list_namespaced_custom_object(
            "metrics.k8s.io", "v1beta1", namespace, "pods", label_selector=f"app={name}"
        )
    except ApiException as e:
        if e.status in (404, 503):  # metrics not available yet
            return {}
        raise
    usage = {}
    for item in res.get("items", []):
        cpu = sum(float(parse_quantity(c["usage"]["cpu"])) for c in item["containers"]) * 1000
        mem = sum(float(parse_quantity(c["usage"]["memory"])) for c in item["containers"]) / 2**20
        usage[item["metadata"]["name"]] = {"cpu_m": cpu, "mem_mi": mem}
    return usage


def hpa_status(name: str, namespace: str) -> Optional[dict[str, Any]]:
    try:
        h = k8s.autoscaling().read_namespaced_horizontal_pod_autoscaler(name, namespace)
    except ApiException as e:
        if e.status == 404:
            return None
        raise
    util = None
    for m in h.status.current_metrics or []:
        if m.type == "Resource" and m.resource and m.resource.name == "cpu" and m.resource.current:
            util = m.resource.current.average_utilization
    target = None
    for m in h.spec.metrics or []:
        if m.type == "Resource" and m.resource.name == "cpu":
            target = m.resource.target.average_utilization
    return {
        "min": h.spec.min_replicas,
        "max": h.spec.max_replicas,
        "target": target,
        "current_util": util,
        "current_replicas": h.status.current_replicas,
        "desired_replicas": h.status.desired_replicas,
    }


def requests_total(pod_name: str, port: int, namespace: str) -> Optional[float]:
    """Read the app's http_requests_total counter through the API server's pod proxy.

    The proxy works both in-cluster and from a laptop with a kubeconfig, so the collector
    does not need direct network access to pod IPs.
    """
    try:
        body = k8s.core().connect_get_namespaced_pod_proxy_with_path(f"{pod_name}:{port}", namespace, "metrics")
    except ApiException:
        return None
    match = _COUNTER_RE.search(body if isinstance(body, str) else str(body))
    return float(match.group(1)) if match else None


def allocatable_cpu_m() -> float:
    """Total allocatable CPU across nodes. The node count is fixed on K3s, so this caps maxReplicas."""
    nodes = k8s.core().list_node().items
    return sum(float(parse_quantity(n.status.allocatable["cpu"])) * 1000 for n in nodes)


def recent_events(name: str, namespace: str, limit: int = 50) -> list[dict[str, Any]]:
    events = k8s.core().list_namespaced_event(namespace).items
    mine = [e for e in events if (e.involved_object.name or "").startswith(name)]
    mine.sort(key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp, reverse=True)
    return [
        {
            "time": (e.last_timestamp or e.event_time or e.metadata.creation_timestamp).isoformat(),
            "type": e.type,
            "reason": e.reason,
            "object": f"{e.involved_object.kind}/{e.involved_object.name}",
            "message": e.message,
        }
        for e in mine[:limit]
    ]


def open_log_stream(pod_name: str, namespace: str, tail_lines: int = 200):
    """Returns a urllib3 response streaming the pod's logs; close it to stop following."""
    return k8s.core().read_namespaced_pod_log(
        pod_name, namespace, follow=True, tail_lines=tail_lines, _preload_content=False
    )
