"""Summarise experiment runs and compare configurations.

For every results/<run>/ folder it computes:
  p95 latency (ms), error rate     from Locust's aggregated stats
  replica-minutes                  integral of desired replicas over the run (cost proxy)
  avg CPU utilisation vs target    from the HPA samples
  overloaded time (s)              samples above 120% of the HPA target (scale-up lag)
  over-provisioned time (s)        samples below 50% of target while above minReplicas
  time to peak replicas (s)        from the first load increase until the max replica count is reached

and writes results/summary.csv, results/summary.md (mean and std dev per scenario and config)
and one PNG per scenario overlaying the configurations.

  python analyze.py                 # all runs under results/
  python analyze.py results/step-*  # selected runs
"""
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RESULTS = Path(__file__).parent / "results"
METRICS = [
    ("p95_ms", "p95 latency (ms)"),
    ("error_rate_pct", "errors (%)"),
    ("replica_minutes", "replica-minutes"),
    ("avg_util_pct", "avg CPU util (%)"),
    ("target_pct", "HPA target (%)"),
    ("overloaded_s", "overloaded (s)"),
    ("overprovisioned_s", "over-provisioned (s)"),
    ("time_to_peak_s", "time to peak replicas (s)"),
]


def read_locust(run: Path) -> tuple[dict, list[dict]]:
    with open(run / "locust_stats.csv", newline="") as f:
        stats = next(r for r in csv.DictReader(f) if r["Name"] == "Aggregated")
    with open(run / "locust_stats_history.csv", newline="") as f:
        history = [r for r in csv.DictReader(f) if r["Name"] == "Aggregated"]
    return stats, history


def summarise(run: Path) -> dict:
    meta = json.loads((run / "meta.json").read_text())
    samples = sorted(json.loads((run / "metrics.json").read_text()), key=lambda s: s["ts"])
    stats, history = read_locust(run)
    t0, t1 = meta["t_start"], meta["t_end"]
    in_run = [s for s in samples if t0 <= s["ts"] <= t1] or samples
    settings = meta["settings"]

    requests_total = int(stats["Request Count"])
    failures = int(stats["Failure Count"])

    replica_minutes = 0.0
    for a, b in zip(in_run, in_run[1:]):
        replica_minutes += a["desired_replicas"] * (b["ts"] - a["ts"]) / 60

    interval = statistics.median([b["ts"] - a["ts"] for a, b in zip(in_run, in_run[1:])]) if len(in_run) > 1 else 15
    with_util = [s for s in in_run if s["hpa_util"] is not None]
    target = settings["hpa_cpu_target"]
    overloaded = sum(1 for s in with_util if s["hpa_util"] > target * 1.2) * interval
    overprovisioned = sum(
        1 for s in with_util if s["hpa_util"] < target * 0.5 and s["desired_replicas"] > settings["hpa_min"]
    ) * interval

    # Load step: the first moment the user count rises above the base level (ignoring the 0 -> base ramp-up).
    users = [(float(r["Timestamp"]), int(r["User Count"])) for r in history]
    base_users = min((u for _, u in users if u > 0), default=0)
    t_load = next((t for t, u in users if u > base_users), t0)
    peak = max((s["ready_replicas"] for s in in_run), default=0)
    t_peak = next((s["ts"] for s in in_run if s["ts"] >= t_load and s["ready_replicas"] >= peak), None)

    return {
        "run": run.name,
        "scenario": meta["scenario"],
        "config": meta["config"],
        "requests": requests_total,
        "p95_ms": float(stats["95%"]),
        "error_rate_pct": round(100 * failures / requests_total, 3) if requests_total else 0.0,
        "replica_minutes": round(replica_minutes, 2),
        "avg_util_pct": round(statistics.mean(s["hpa_util"] for s in with_util), 1) if with_util else None,
        "target_pct": target,
        "overloaded_s": round(overloaded),
        "overprovisioned_s": round(overprovisioned),
        "time_to_peak_s": round(t_peak - t_load) if t_peak else None,
        "peak_replicas": peak,
        "_samples": in_run,
        "_t0": t0,
        "_users": users,
    }


def fmt(values: list) -> str:
    values = [v for v in values if v is not None]
    if not values:
        return "—"
    if len(values) == 1:
        return f"{values[0]:g}"
    return f"{statistics.mean(values):.1f} ± {statistics.stdev(values):.1f}"


def write_tables(rows: list[dict]) -> None:
    public = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    with open(RESULTS / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(public[0]))
        w.writeheader()
        w.writerows(public)

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["scenario"], r["config"])].append(r)
    lines = [
        "| scenario | config | runs | " + " | ".join(label for _, label in METRICS) + " |",
        "|---" * (3 + len(METRICS)) + "|",
    ]
    for (scenario, config), rs in sorted(groups.items()):
        cells = [fmt([r[key] for r in rs]) for key, _ in METRICS]
        lines.append(f"| {scenario} | {config} | {len(rs)} | " + " | ".join(cells) + " |")
    (RESULTS / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def plot(rows: list[dict]) -> None:
    by_scenario: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_scenario[r["scenario"]].append(r)
    for scenario, rs in by_scenario.items():
        fig, axes = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
        for r in rs:
            xs = [s["ts"] - r["_t0"] for s in r["_samples"]]
            label = r["run"].removeprefix(f"{scenario}-")  # e.g. baseline-20261024-103000
            axes[0].plot(xs, [s["ready_replicas"] for s in r["_samples"]], label=label)
            axes[1].plot(xs, [s["hpa_util"] for s in r["_samples"]], label=label)
            axes[2].plot([t - r["_t0"] for t, _ in r["_users"]], [u for _, u in r["_users"]], label=label)
        axes[0].set_ylabel("ready replicas")
        axes[1].set_ylabel("CPU util (% of request)")
        axes[2].set_ylabel("Locust users (~req/s)")
        axes[2].set_xlabel("seconds since start")
        for ax in axes:
            ax.grid(alpha=0.3)
        axes[0].legend(fontsize=8)
        fig.suptitle(f"{scenario}: baseline vs workload-aware configuration")
        fig.tight_layout()
        out = RESULTS / f"{scenario}.png"
        fig.savefig(out, dpi=120)
        plt.close(fig)
        print(f"wrote {out}")


def main() -> None:
    dirs = [Path(a) for a in sys.argv[1:]] or sorted(p for p in RESULTS.glob("*") if (p / "meta.json").exists())
    if not dirs:
        sys.exit("no runs found; run run_experiment.py first")
    rows = [summarise(d) for d in dirs]
    write_tables(rows)
    plot(rows)


if __name__ == "__main__":
    main()
