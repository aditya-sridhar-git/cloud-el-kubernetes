from functools import lru_cache

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException


@lru_cache
def api_client() -> client.ApiClient:
    """In-cluster service account when running as a pod, otherwise the local kubeconfig."""
    try:
        config.load_incluster_config()
    except ConfigException:
        config.load_kube_config()
    return client.ApiClient()


def apps() -> client.AppsV1Api:
    return client.AppsV1Api(api_client())


def core() -> client.CoreV1Api:
    return client.CoreV1Api(api_client())


def autoscaling() -> client.AutoscalingV2Api:
    return client.AutoscalingV2Api(api_client())


def networking() -> client.NetworkingV1Api:
    return client.NetworkingV1Api(api_client())


def custom() -> client.CustomObjectsApi:
    return client.CustomObjectsApi(api_client())
