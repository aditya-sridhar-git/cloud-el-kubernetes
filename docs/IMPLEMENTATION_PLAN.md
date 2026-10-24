# Implementation Plan: Intelligent K8s Deployment & Auto-Scaling Platform (K3s on AWS EC2)

## Context
> **Status:** this was the plan written before implementation. All of it is now built (see `docs/guide.md` for how to run it); the Terraform and PowerShell scripts have not yet been run against a real AWS account.

The repo started with only the synopsis (`docs/Intelligent_Kubernetes_Deployment_AutoScaling_Synopsis (1).docx`) and `CLAUDE.md`. The synopsis assumed Minikube/K3s on a laptop with a local registry. The team has now fixed the infrastructure:
- **Cluster:** **K3s on AWS EC2**. There are 1–2 Ubuntu instances: node 1 is the server (control plane plus workloads), and node 2 is an optional agent. The instances are stopped between sessions. EKS is noted only as the production alternative.
- **Builds:** **AWS CodeBuild → Amazon ECR**. The backend clones the repo, zips it, uploads it to S3 and triggers CodeBuild. CodeBuild builds and pushes to ECR. The backend polls the build, then deploys the image. A Docker-SDK builder is built first, then swapped for CodeBuild.
- **Cost guardrail:** an AWS Budget alert at about $5.
- **Unchanged:** the FastAPI orchestrator, the React+TS dashboard over WebSockets, Deployment/Service/HPA generated in code, rolling update + rollback, Locust/hey experiments, and the metrics-driven recommender evaluated against plain HPA.

The synopsis .docx and `CLAUDE.md` are both updated to match these decisions.

## Architecture
```
GitHub ─webhook─▶ EC2 node1 (Elastic IP) :80 ─ Traefik (K3s built-in) ─┬─▶ platform ns: frontend, backend (FastAPI)
browser ─────────────────────────────────────────────────────────────┘     └─▶ apps ns: <app> Deployment+Service+HPA
                                                                              (host <app>.<EIP>.nip.io)
backend: git clone → zip → S3 ──▶ CodeBuild (buildspec: docker build/push) ──▶ ECR ──pull──▶ K3s nodes
backend reads CodeBuild status + CloudWatch build logs; K8s API via in-cluster ServiceAccount
metrics-server (built into K3s) → HPA + dashboard metrics
```
- **Ingress without a domain.** K3s ships Traefik and ServiceLB. Each app gets a ClusterIP Service and an Ingress on host `<app>.<EIP>.nip.io`, and the platform is served at `platform.<EIP>.nip.io`. So there is no AWS load balancer and no DNS cost.
- **AWS credentials for pods.** Node 1's **EC2 instance profile** gives the backend its AWS permissions (S3 put, CodeBuild start/get, CloudWatch Logs read). IMDSv2 hop limit is set to 2 so that pods can reach the instance profile. This is simpler than IRSA on K3s and is acceptable for the demo.
- **ECR pulls.** ECR tokens expire after 12 h, so K3s is configured with the **kubelet ECR credential provider**: the `ecr-credential-provider` binary plus `--kubelet-arg=image-credential-provider-config=…` in user-data. Kubelet then pulls with the instance role, with no imagePullSecrets and no ImagePullBackOff.
- **Persistence.** The backend uses **SQLite on a PVC**, through K3s's built-in local-path provisioner. The data lives on the node's EBS volume and survives stopping and starting the instance. No DynamoDB or RDS is needed.
- **Security group.** 80/443 are open to everyone, which webhooks need. 6443 and 22 are open only to the team's IPs.
- **Tradeoffs, as stated in the synopsis:**
  - a single control plane, so no HA
  - manual K3s upgrades
  - a fixed node count, so pods scale but nodes don't
  - no AWS LB integration
- **Cost:**
  - about $1/day while running: t3.medium on-demand plus EBS
  - the Elastic IP / public IPv4 charge (about $3.6/month) and EBS storage still accrue while stopped
  - CodeBuild `BUILD_GENERAL1_SMALL` has 100 free minutes/month
  - the $5 budget is an *alert*, not a cap

## Repository layout
```
infra/terraform/     # default VPC lookup, SG, EC2 node1 (+ optional node2 via var), EIP, instance profile,
                     # user_data (k3s install with --tls-san EIP, ECR credential provider), ECR repo + lifecycle,
                     # S3 source bucket (expiry rule), CodeBuild project + role, AWS Budget ($5 alert)
infra/codebuild/buildspec.yml   # ECR login, docker build, docker push <repo>:<app>-<sha>
scripts/             # up.ps1 (terraform apply / start instances), start.ps1, stop.ps1, get-kubeconfig.ps1,
                     # deploy-platform.ps1, down.ps1 (terraform destroy)
k8s/platform/        # namespaces, backend+frontend Deployments/Services/Ingress, SA + Role/ClusterRole, PVC
backend/
  app/main.py, config.py           # FastAPI app; pydantic-settings (region, bucket, ECR URI, builder type, EIP)
  app/api/                         # apps.py, deployments.py, webhooks.py, recommendations.py, ws.py
  app/pipeline/source.py           # git clone (shallow, branch/sha) → zip → S3 upload
  app/pipeline/builders.py         # Builder protocol; DockerSdkBuilder (phase 1) and CodeBuildBuilder (phase 2)
  app/pipeline/orchestrator.py     # deployment state machine
  app/k8s/                         # client.py, manifests.py, rollout.py, metrics.py
  app/store/                       # SQLModel models + repo functions (apps, deployments, samples, recs)
  app/intel/                       # collector.py, recommender.py, prescale.py
  tests/
frontend/            # Vite + React + TS, Recharts, React Router
sample-apps/flask-app/, sample-apps/node-app/   # Dockerfile, /healthz, /metrics counter, /work?ms=N CPU endpoint
experiments/         # locustfile.py, scenarios.yaml, run_experiment.py, analyze.py
```

## Key component design
**Builder abstraction (`pipeline/builders.py`).** `build(app, source) -> BuildResult(image_uri, logs_iter, status)`.
- `DockerSdkBuilder` builds locally and pushes to ECR. It is used on a laptop with Docker Desktop to get the end-to-end pipeline working first.
- `CodeBuildBuilder` calls `start_build` with `sourceLocationOverride=s3://bucket/<app>/<sha>.zip` and env vars `IMAGE_TAG` and `APP`. It polls `batch_get_builds` and tails CloudWatch `get_log_events`.
- A `BUILDER=docker|codebuild` setting selects the builder.

**Manifests (`k8s/manifests.py`).** Pure functions that build the Deployment, Service, Ingress and HPA dicts, applied via server-side apply.
- The Deployment always has CPU/memory requests and limits, which HPA requires.
- It has `/healthz` readiness and liveness probes.
- Its rolling-update strategy is `maxSurge=1, maxUnavailable=0` with `progressDeadlineSeconds=120`.
- The HPA uses `autoscaling/v2` with a CPU target and an explicit `behavior`.

**Orchestrator.** Each deployment moves through `QUEUED → CLONING → BUILDING → PUSHED → APPLYING → ROLLING_OUT → SUCCEEDED | FAILED | ROLLED_BACK`. Every transition is persisted and broadcast over WS. It runs as asyncio tasks, with `asyncio.to_thread` for blocking boto3, git and k8s calls.

**Rollout/rollback (`k8s/rollout.py`).**
- Watch until the updated, available and desired replica counts match, or until `ProgressDeadlineExceeded`.
- On failure, re-apply the last SUCCEEDED deployment's image and spec from SQLite.
- A manual rollback to any earlier version uses the same path.

**Webhook.** Handles GitHub `push`: verify `X-Hub-Signature-256` against a per-app secret, match the repo and branch, then trigger the pipeline with the head SHA.

**Live data (`/ws/apps/{name}`).** One endpoint with typed messages: `status`, `build_log`, `pod_log`, `metrics`, `event`.

**Collector.** Every 15 s it records:
- pod CPU/memory from `metrics.k8s.io`
- desired/ready replicas
- HPA utilization
- request rate, scraped from each pod's `/metrics`

Samples are stored in SQLite and pushed over WS.

**Recommender.** Explainable heuristics, not a new algorithm:
- **Requests/limits:** CPU request = p90 per-pod CPU × 1.2, and CPU limit = p99 × 1.5. Memory request = p95 × 1.2, and memory limit = max × 1.5.
- **Per-pod capacity:** a linear fit of CPU against RPS gives the capacity per pod.
- **HPA range:** min/max replicas = ceil(baseline or peak RPS / capacity), with headroom. The target is 60–70%, depending on the observed scale-up lag.
- **Pre-scaling:** when peaks recur or scale-up lag is high, an APScheduler job raises HPA `minReplicas` ahead of the peak.
- **Output:** recommendations are stored with their rationale and shown in the UI with Apply and Dismiss. Apply goes through the normal rollout path.
- **Capacity limit:** with a fixed node count, `maxReplicas` is capped by the node's allocatable CPU. The recommender reports when the cluster itself is the bottleneck.

**Frontend.**
- Apps list
- New App form: repo, branch, port, resources, HPA
- App detail:
  - status timeline
  - replica, CPU and memory charts
  - build and pod logs
  - history with Rollback
  - HPA config
  - Recommendations
- Experiments view, optional

**Demo security.** The dashboard uses a shared bearer token kept in a K8s Secret. Webhooks use HMAC. Only public Git repos are supported. Backend RBAC is scoped to the `apps` namespace, plus read access to metrics.

## Phased milestones (≈8 weeks, 4 people)
| Wk | Milestone | Done when |
|---|---|---|
| 1 | **Infra**: Terraform (EC2+EIP+SG, instance profile, K3s user-data with ECR credential provider, ECR, S3, Budget), start/stop scripts, sample apps | `kubectl top nodes` works from a laptop; a manually pushed ECR image runs and is reachable at `<app>.<EIP>.nip.io` |
| 2 | **Backend + Docker SDK builder**: FastAPI, SQLite store, clone, DockerSdkBuilder | The API builds a sample repo and pushes it to ECR from a laptop |
| 3 | **Manifests + apply + rollout watch**: orchestrator, unit tests | Git URL → running app with an HPA, end to end, with the backend running locally |
| 4 | **Swap to CodeBuild + run platform in-cluster**: buildspec, CodeBuild project, CodeBuildBuilder, platform images and manifests, webhook | A push to the sample repo triggers a CodeBuild build, then a redeploy, fully on AWS |
| 5 | **Dashboard**: React, WS streaming, charts, logs | Charts move under `hey` load |
| 6 | **Rolling update + rollback** | A broken `/healthz` commit rolls back automatically; manual rollback works |
| 7 | **Intelligent component**: collector, recommender, pre-scale, UI | Recommendations are generated from a load run and applied with one click |
| 8 | **Experiments + report + synopsis/CLAUDE.md finalised** | Comparison tables and plots exist; the docs match what was built |

Suggested tracks: (A) Terraform/K3s/CodeBuild, (B) backend pipeline and k8s, (C) frontend, (D) sample apps, collector, recommender and experiments.

## Experiment protocol
- **Configurations:** baseline (100m/128Mi, HPA 1–10 at 50% CPU) against the recommender output from a training run.
- **Scenarios:** step, spike and periodic, run with Locust headless from a laptop against `<app>.<EIP>.nip.io`.
- **Measures:**
  - p95 latency and error rate
  - time-to-scale
  - replica-minutes
  - average CPU vs target
  - over- and under-provisioned intervals
- **Tooling:** `run_experiment.py` applies the configuration via the API, runs Locust, and pulls samples from `GET /apps/{name}/metrics`. `analyze.py` produces plots and tables. Each scenario runs 3×.
- **Constraint:** use t3.medium (not burstable-credit-starved t3.small) for node 1 during experiments, or enable `unlimited` credits, so that CPU throttling from credits doesn't skew results.

## Documentation updates
**Synopsis .docx** (edited in place using the docx skill, preserving formatting):
- **Methodology:** add the S3 → CodeBuild → ECR build flow and the K3s cluster on AWS EC2.
- **Software Requirements:**
  - Containerization: Docker, AWS CodeBuild, Amazon ECR
  - Orchestration: K3s on AWS EC2 with the built-in Metrics Server
  - Add: AWS S3, IAM, AWS Budgets, and Terraform
- **Hardware Requirements:** 1–2 AWS EC2 instances (t3.small/t3.medium, Ubuntu), with the laptop needing only a browser and dev tools.
- **Tradeoffs:** add a sentence each on the tradeoffs (no HA, fixed nodes) and on EKS as the production alternative.

**`CLAUDE.md`** (final rewrite):
- Replace the Minikube/local-registry notes with the K3s-on-EC2, CodeBuild/ECR, credential-provider and nip.io architecture.
- Add the build, test, infra and start/stop commands and the repo layout.
- Add cost notes: stop instances; the EIP is billed while stopped.

## Verification
- **Unit:** run `pytest backend/tests`. It covers manifests, the recommender maths, HMAC, orchestrator transitions, and CodeBuildBuilder with `moto`/`botocore.stub`.
- **Frontend:** `npm run build` and `npm run lint` must pass.
- **End-to-end on AWS**, after `scripts/up.ps1` and `scripts/deploy-platform.ps1`:
  1. Register `sample-apps/flask-app`. The build logs from CodeBuild should stream, the deployment should reach SUCCEEDED, and `curl http://flask-app.<EIP>.nip.io/healthz` should respond.
  2. Run `hey -z 3m -c 50 …/work?ms=50`. Replicas should rise in the UI and in `kubectl get hpa -n apps -w`.
  3. Push a commit. The webhook should trigger a rolling update with no failed `hey` requests.
  4. Push a commit that breaks `/healthz`. It should reach ROLLED_BACK.
  5. Apply a recommendation and check the result with `kubectl get deploy,hpa -o yaml`.
  6. Run `scripts/stop.ps1` then `start.ps1`. The platform data in SQLite should still be there, and image pulls should still work after more than 12 h.
- **Budget:** the alert email is confirmed in AWS Budgets.
- **Prerequisites (Windows):** AWS CLI v2, Terraform, kubectl, Helm (optional), Docker Desktop (phase-1 builder and platform images), Node 20, Python 3.11+, hey, and Locust.
