"""Pure functions that turn an AppSpec into Kubernetes manifests (plain dicts).

Every app gets a Deployment, a ClusterIP Service, a Traefik Ingress on <app>.<host_suffix>
and an autoscaling/v2 HPA. Resource requests are always set because HPA utilisation is a
percentage of the CPU request.
"""
import json
from dataclasses import asdict, dataclass
from typing import Any

MANAGED_BY = "k8s-platform"


@dataclass(frozen=True)
class AppSpec:
    name: str
    image: str
    version: str
    port: int
    health_path: str
    cpu_request_m: int
    cpu_limit_m: int
    mem_request_mi: int
    mem_limit_mi: int
    hpa_min: int
    hpa_max: int
    hpa_cpu_target: int

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "AppSpec":
        return cls(**json.loads(raw))

    @classmethod
    def from_app(cls, app: Any, image: str, version: str) -> "AppSpec":
        """Build from an App row (or anything with the same attributes)."""
        return cls(
            name=app.name,
            image=image,
            version=version,
            port=app.port,
            health_path=app.health_path,
            cpu_request_m=app.cpu_request_m,
            cpu_limit_m=app.cpu_limit_m,
            mem_request_mi=app.mem_request_mi,
            mem_limit_mi=app.mem_limit_mi,
            hpa_min=app.hpa_min,
            hpa_max=app.hpa_max,
            hpa_cpu_target=app.hpa_cpu_target,
        )


def labels(spec: AppSpec) -> dict[str, str]:
    return {"app": spec.name, "app.kubernetes.io/managed-by": MANAGED_BY}


def deployment(spec: AppSpec, namespace: str) -> dict:
    probe = {"httpGet": {"path": spec.health_path, "port": spec.port}}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": spec.name, "namespace": namespace, "labels": labels(spec)},
        "spec": {
            # No `replicas`: the HPA owns the replica count, and setting it here would make
            # every apply fight the autoscaler.
            "selector": {"matchLabels": {"app": spec.name}},
            "progressDeadlineSeconds": 120,
            "revisionHistoryLimit": 5,
            "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}},
            "template": {
                "metadata": {"labels": {**labels(spec), "platform/version": spec.version[:63]}},
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": spec.image,
                            "ports": [{"containerPort": spec.port, "name": "http"}],
                            "env": [{"name": "APP_VERSION", "value": spec.version}],
                            "resources": {
                                "requests": {"cpu": f"{spec.cpu_request_m}m", "memory": f"{spec.mem_request_mi}Mi"},
                                "limits": {"cpu": f"{spec.cpu_limit_m}m", "memory": f"{spec.mem_limit_mi}Mi"},
                            },
                            "readinessProbe": {**probe, "initialDelaySeconds": 3, "periodSeconds": 5, "failureThreshold": 3},
                            "livenessProbe": {**probe, "initialDelaySeconds": 15, "periodSeconds": 10, "failureThreshold": 3},
                        }
                    ]
                },
            },
        },
    }


def service(spec: AppSpec, namespace: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": spec.name, "namespace": namespace, "labels": labels(spec)},
        "spec": {
            "type": "ClusterIP",
            "selector": {"app": spec.name},
            "ports": [{"name": "http", "port": 80, "targetPort": spec.port}],
        },
    }


def ingress(spec: AppSpec, namespace: str, host_suffix: str) -> dict:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {"name": spec.name, "namespace": namespace, "labels": labels(spec)},
        "spec": {
            "ingressClassName": "traefik",
            "rules": [
                {
                    "host": app_host(spec.name, host_suffix),
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {"service": {"name": spec.name, "port": {"number": 80}}},
                            }
                        ]
                    },
                }
            ],
        },
    }


def hpa(spec: AppSpec, namespace: str) -> dict:
    return {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": {"name": spec.name, "namespace": namespace, "labels": labels(spec)},
        "spec": {
            "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": spec.name},
            "minReplicas": spec.hpa_min,
            "maxReplicas": spec.hpa_max,
            "metrics": [
                {
                    "type": "Resource",
                    "resource": {"name": "cpu", "target": {"type": "Utilization", "averageUtilization": spec.hpa_cpu_target}},
                }
            ],
            # Identical behaviour for every configuration, so experiments compare only the
            # settings the recommender changes. Scale-down is shortened from the 300s default
            # to keep demo runs short.
            "behavior": {
                "scaleUp": {
                    "stabilizationWindowSeconds": 0,
                    "selectPolicy": "Max",
                    "policies": [
                        {"type": "Percent", "value": 100, "periodSeconds": 15},
                        {"type": "Pods", "value": 4, "periodSeconds": 15},
                    ],
                },
                "scaleDown": {
                    "stabilizationWindowSeconds": 120,
                    "policies": [{"type": "Percent", "value": 50, "periodSeconds": 30}],
                },
            },
        },
    }


def all_manifests(spec: AppSpec, namespace: str, host_suffix: str) -> list[dict]:
    return [deployment(spec, namespace), service(spec, namespace), ingress(spec, namespace, host_suffix), hpa(spec, namespace)]


def app_host(name: str, host_suffix: str) -> str:
    return f"{name}.{host_suffix}"
