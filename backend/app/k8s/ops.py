"""Write-side Kubernetes operations used by the orchestrator: apply, wait for rollout, delete.

Wrapped in a class so tests can substitute a fake without a cluster.
"""
import logging
import time
from typing import Callable, Optional

from kubernetes.client.exceptions import ApiException
from kubernetes.dynamic import DynamicClient

from app.k8s import client as k8s
from app.k8s.manifests import AppSpec, all_manifests

log = logging.getLogger(__name__)

# Container waiting reasons that will not fix themselves; fail fast instead of waiting
# for the 120s progress deadline.
FATAL_WAITING_REASONS = {"ErrImagePull", "ImagePullBackOff", "InvalidImageName", "CrashLoopBackOff", "CreateContainerConfigError"}


class K8sOps:
    def __init__(self, namespace: str, host_suffix: str, rollout_timeout_s: int = 180) -> None:
        self.namespace = namespace
        self.host_suffix = host_suffix
        self.rollout_timeout_s = rollout_timeout_s

    def apply_spec(self, spec: AppSpec) -> None:
        """Server-side apply of Deployment, Service, Ingress and HPA."""
        dyn = DynamicClient(k8s.api_client())
        for manifest in all_manifests(spec, self.namespace, self.host_suffix):
            resource = dyn.resources.get(api_version=manifest["apiVersion"], kind=manifest["kind"])
            resource.server_side_apply(
                body=manifest,
                name=manifest["metadata"]["name"],
                namespace=self.namespace,
                field_manager="k8s-platform",
                force_conflicts=True,
            )

    def wait_for_rollout(self, name: str, on_progress: Optional[Callable[[str], None]] = None) -> tuple[bool, str]:
        """Block until the Deployment's new ReplicaSet is fully available, or it fails/times out."""
        deadline = time.monotonic() + self.rollout_timeout_s
        last_msg = ""
        while time.monotonic() < deadline:
            d = k8s.apps().read_namespaced_deployment_status(name, self.namespace)
            st = d.status
            desired = d.spec.replicas or 0

            for cond in st.conditions or []:
                if cond.type == "Progressing" and cond.status == "False" and cond.reason == "ProgressDeadlineExceeded":
                    return False, f"progress deadline exceeded: {cond.message}"

            fatal = self._fatal_pod_reason(name, d)
            if fatal:
                return False, fatal

            generation_seen = (st.observed_generation or 0) >= (d.metadata.generation or 0)
            updated, available, total = st.updated_replicas or 0, st.available_replicas or 0, st.replicas or 0
            if generation_seen and updated == desired and available >= desired and total == desired:
                return True, f"{available}/{desired} replicas available"

            msg = f"{updated}/{desired} updated, {available} available"
            if msg != last_msg and on_progress:
                on_progress(msg)
            last_msg = msg
            time.sleep(3)
        return False, f"rollout did not finish within {self.rollout_timeout_s}s"

    def _fatal_pod_reason(self, name: str, deployment) -> Optional[str]:
        """Return a reason if a pod of the *new* template is stuck in an unrecoverable state."""
        version = (deployment.spec.template.metadata.labels or {}).get("platform/version")
        selector = f"app={name}" + (f",platform/version={version}" if version else "")
        pods = k8s.core().list_namespaced_pod(self.namespace, label_selector=selector).items
        for pod in pods:
            for cs in pod.status.container_statuses or []:
                waiting = cs.state.waiting if cs.state else None
                if waiting and waiting.reason in FATAL_WAITING_REASONS:
                    # CrashLoopBackOff deserves a couple of restarts before giving up.
                    if waiting.reason == "CrashLoopBackOff" and (cs.restart_count or 0) < 2:
                        continue
                    return f"pod {pod.metadata.name}: {waiting.reason}: {waiting.message or ''}".strip()
        return None

    def delete_app(self, name: str) -> None:
        calls = [
            lambda: k8s.autoscaling().delete_namespaced_horizontal_pod_autoscaler(name, self.namespace),
            lambda: k8s.networking().delete_namespaced_ingress(name, self.namespace),
            lambda: k8s.core().delete_namespaced_service(name, self.namespace),
            lambda: k8s.apps().delete_namespaced_deployment(name, self.namespace),
        ]
        for call in calls:
            try:
                call()
            except ApiException as e:
                if e.status != 404:
                    raise

    def set_hpa_range(self, name: str, min_replicas: int, max_replicas: int) -> None:
        """Used by pre-scaling to raise (and later restore) the replica floor without a redeploy."""
        patch = {"spec": {"minReplicas": min_replicas, "maxReplicas": max(min_replicas, max_replicas)}}
        k8s.autoscaling().patch_namespaced_horizontal_pod_autoscaler(name, self.namespace, patch)
