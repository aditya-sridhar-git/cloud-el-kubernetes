"""Workload-aware configuration recommendations.

Explainable heuristics over collected samples, not a new autoscaling algorithm:

* Requests/limits: CPU request = p90 per-pod CPU x 1.2, limit = max(2 x request, p99 x 1.5).
  Memory request = p95 per-pod memory x 1.2, limit = max x 1.5.
* HPA target: 70%, lowered to 60% when the HPA spent >15% of the window more than 20% above
  its target (scale-up lagged behind load, so more headroom is needed).
* Per-pod capacity: a linear fit of total CPU against request rate gives CPU cost per request;
  capacity = (target x request - idle CPU per pod) / cost per request.
* Replica range: min = p10 load / capacity, max = peak load x 1.25 / capacity, capped by what
  the fixed-size K3s cluster can actually schedule.
* Pre-scaling: suggested when scale-up lag is high, ahead of the busiest hour of the day.
"""
import math
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.store.models import App, MetricSample

MIN_SAMPLES = 10
OVERLOAD_FRACTION = 0.15


class InsufficientData(Exception):
    pass


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile; p in [0, 100]."""
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def round_up(value: float, step: int) -> int:
    return int(math.ceil(value / step) * step)


def recommend(
    samples: list[MetricSample],
    app: App,
    allocatable_cpu_m: Optional[float] = None,
    tz: str = "UTC",
) -> tuple[dict[str, Any], list[str]]:
    usable = [s for s in samples if s.ready_replicas > 0]
    if len(usable) < MIN_SAMPLES:
        raise InsufficientData(
            f"need at least {MIN_SAMPLES} samples with running pods, have {len(usable)}; "
            "run some load against the app for a few minutes first"
        )
    why: list[str] = []

    # ---- requests and limits -------------------------------------------------------------
    cpu_pp = [s.cpu_m / s.ready_replicas for s in usable]
    mem_pp = [s.mem_mi / s.ready_replicas for s in usable]
    cpu_p90, cpu_p99 = percentile(cpu_pp, 90), percentile(cpu_pp, 99)
    cpu_req = min(max(round_up(cpu_p90 * 1.2, 10), 50), 2000)
    cpu_lim = max(cpu_req * 2, round_up(cpu_p99 * 1.5, 10))
    mem_p95, mem_max = percentile(mem_pp, 95), max(mem_pp)
    mem_req = max(round_up(mem_p95 * 1.2, 16), 64)
    mem_lim = max(round_up(mem_max * 1.5, 16), mem_req + 64)
    why.append(
        f"Per-pod CPU p90 was {cpu_p90:.0f}m (p99 {cpu_p99:.0f}m): request {cpu_req}m (p90 x 1.2), "
        f"limit {cpu_lim}m (current {app.cpu_request_m}m / {app.cpu_limit_m}m)."
    )
    why.append(
        f"Per-pod memory p95 was {mem_p95:.0f}Mi (max {mem_max:.0f}Mi): request {mem_req}Mi, limit {mem_lim}Mi "
        f"(current {app.mem_request_mi}Mi / {app.mem_limit_mi}Mi)."
    )

    # ---- HPA target from observed scale-up lag ------------------------------------------
    with_util = [s for s in usable if s.hpa_util is not None and s.hpa_target]
    overloaded = [s for s in with_util if s.hpa_util > s.hpa_target * 1.2]
    overload_frac = len(overloaded) / len(with_util) if with_util else 0.0
    target = 60 if overload_frac > OVERLOAD_FRACTION else 70
    why.append(
        f"The HPA ran more than 20% above its target in {overload_frac:.0%} of samples; "
        + ("lowering the target to 60% for more scale-up headroom." if target == 60 else "a 70% target is sufficient.")
    )

    # ---- capacity per pod and replica range ---------------------------------------------
    hpa_min, hpa_max = app.hpa_min, app.hpa_max
    capacity: Optional[float] = None
    slope = intercept = None
    loaded = [s for s in usable if s.rps is not None]
    rps_values = [s.rps for s in loaded]
    if len(loaded) >= 5 and len(set(rps_values)) > 1:
        slope, intercept = statistics.linear_regression(rps_values, [s.cpu_m for s in loaded])
        avg_pods = statistics.mean(s.ready_replicas for s in loaded)
        idle_pp = max(intercept, 0.0) / avg_pods
        budget = target / 100 * cpu_req - idle_pp
        if slope > 0 and budget > 0:
            capacity = budget / slope
    if capacity:
        low, peak = percentile(rps_values, 10), max(rps_values)
        hpa_min = max(1, math.ceil(low / capacity))
        hpa_max = max(hpa_min + 1, math.ceil(peak * 1.25 / capacity))
        why.append(
            f"Each request costs about {slope:.2f}m CPU, so one pod handles about {capacity:.1f} req/s at "
            f"{target}% of a {cpu_req}m request. Load ranged from {low:.1f} (p10) to {peak:.1f} req/s (peak): "
            f"replicas {hpa_min}-{hpa_max} (peak x 1.25 headroom; current {app.hpa_min}-{app.hpa_max})."
        )
    else:
        why.append("Not enough request-rate variation to estimate per-pod capacity; replica range left unchanged.")

    if allocatable_cpu_m:
        schedulable = int(allocatable_cpu_m * 0.8 // cpu_req)  # 20% left for system and platform pods
        if hpa_max > schedulable:
            why.append(
                f"The cluster has {allocatable_cpu_m:.0f}m allocatable CPU, enough for about {schedulable} pods at "
                f"{cpu_req}m; maxReplicas capped from {hpa_max} to {max(hpa_min, schedulable)}. "
                "The cluster itself is the bottleneck: add a worker node to scale further."
            )
            hpa_max = max(hpa_min, schedulable)

    # ---- pre-scaling ----------------------------------------------------------------------
    prescale = None
    if capacity and overload_frac > OVERLOAD_FRACTION:
        zone = ZoneInfo(tz)
        by_hour: dict[int, list[float]] = defaultdict(list)
        for s in loaded:
            by_hour[datetime.fromtimestamp(s.ts, zone).hour].append(s.rps)
        peak_hour = max(by_hour, key=lambda h: statistics.mean(by_hour[h]))
        floor = min(hpa_max, max(hpa_min, math.ceil(percentile(by_hour[peak_hour], 90) / capacity)))
        if floor > hpa_min:
            start_hour = (peak_hour - 1) % 24
            prescale = {"hour": start_hour, "minute": 50, "duration_min": 70, "min_replicas": floor}
            why.append(
                f"Scale-up lagged and load peaks around {peak_hour:02d}:00 ({tz}); suggest raising minReplicas to "
                f"{floor} from {start_hour:02d}:50 for 70 minutes so pods are ready before the peak."
            )

    payload = {
        "cpu_request_m": cpu_req,
        "cpu_limit_m": cpu_lim,
        "mem_request_mi": mem_req,
        "mem_limit_mi": mem_lim,
        "hpa_min": hpa_min,
        "hpa_max": hpa_max,
        "hpa_cpu_target": target,
        "prescale": prescale,
        "stats": {
            "samples": len(usable),
            "window_start": min(s.ts for s in usable),
            "window_end": max(s.ts for s in usable),
            "cpu_per_pod_p90_m": round(cpu_p90, 1),
            "mem_per_pod_p95_mi": round(mem_p95, 1),
            "cpu_per_request_m": round(slope, 3) if slope is not None else None,
            "capacity_rps_per_pod": round(capacity, 2) if capacity else None,
            "overload_fraction": round(overload_frac, 3),
        },
    }
    return payload, why


CONFIG_FIELDS = ("cpu_request_m", "cpu_limit_m", "mem_request_mi", "mem_limit_mi", "hpa_min", "hpa_max", "hpa_cpu_target")
