# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

An "Intelligent Kubernetes-Based Application Deployment and Auto-Scaling Platform" (RVCE, B.E. CSE Cloud Computing Elective, course IS355TBD). It is a compact demonstration platform, not a production PaaS, so prefer simple, explainable implementations over production-grade complexity.

Given a public Git repo URL (with a Dockerfile at its root), it clones the source, builds an image with AWS CodeBuild, pushes it to Amazon ECR, generates and applies a Deployment, Service, Ingress and HPA on a K3s cluster running on AWS EC2, and shows state, metrics and logs live on a dashboard. It does rolling updates with automatic and manual rollback, and a recommender suggests resource requests/limits, HPA ranges and pre-scaling from observed metrics. The recommender is evaluated experimentally against plain reactive HPA and is **not** a new autoscaling algorithm.

Documents:
- `docs/Intelligent_Kubernetes_Deployment_AutoScaling_Synopsis (1).docx`: the synopsis and source of truth for scope (updated for the AWS architecture).
- `docs/guide.md`: how to run everything (AWS setup, deploy, experiments, troubleshooting).
- `docs/IMPLEMENTATION_PLAN.md`: the original design and milestones.

## Status

All parts are written. Tested locally: the backend (`pytest`, 53 tests with fake builder and Kubernetes ops), the dashboard (`npm run build` and `lint`, plus a manual browser check against a local backend) and the experiment tooling (Locust dry run). **Not yet run against real AWS or a real cluster:** the Terraform, the K3s bootstrap (`user_data.sh.tpl`), the `scripts/*.ps1` files, `K8sOps` / `observe.py` against a live API server, and the CodeBuild path. Treat bugs there as likely on first contact and verify with `kubectl` rather than assuming.

## Architecture

- **Cluster: K3s on AWS EC2, not EKS.** 1–2 Ubuntu instances. Node 1 is the K3s server (control plane plus workloads) with an Elastic IP; node 2 is an optional agent. Metrics Server, Traefik ingress and the local-path provisioner are built into K3s. Accepted tradeoffs: no HA, manual upgrades, fixed node count, no AWS LB integration. EKS is mentioned only as the production alternative.
- **Routing without a domain:** the platform is at `platform.<EIP>.nip.io` and each app at `<app>.<EIP>.nip.io`, through Traefik Ingresses.
- **Builds:** the backend clones the repo, zips it to S3 and starts CodeBuild (`infra/codebuild/buildspec.yml`), which builds and pushes to ECR with the tag `<app>-<sha12>`; it polls the build and tails CloudWatch logs. The builder is pluggable (`BUILDER=docker|codebuild`); `docker` builds locally and exists for development. The platform's own images are built by the same CodeBuild project (`scripts/build-image.ps1`).
- **ECR pulls:** the kubelet ECR credential provider and the node IAM instance profile (configured in `infra/terraform/user_data.sh.tpl`). Do not use imagePullSecrets: ECR tokens expire after 12 h.
- **AWS access for the backend pod:** through node 1's instance profile, with the IMDSv2 hop limit set to 2 so pods can reach it. All nodes share one role.
- **Persistence:** SQLite (SQLModel) on a local-path PVC. The backend deployment uses the `Recreate` strategy because there is a single writer.
- **The backend is the orchestrator** and the only component that talks to AWS and the Kubernetes API; the dashboard talks only to it (REST plus one WebSocket per app).
- **Base images** use the `public.ecr.aws/docker/library/*` mirror to avoid Docker Hub rate limits in CodeBuild.

### Backend (`backend/app/`)
- `main.py`: `create_app(settings, builder, k8s_ops, cloner)` factory. Tests inject fakes (see `tests/conftest.py`).
- `pipeline/orchestrator.py`: the deployment state machine `QUEUED → CLONING → BUILDING → APPLYING → ROLLING_OUT → SUCCEEDED | ROLLED_BACK | FAILED`. It runs as asyncio tasks bound to the server's loop, with one lock per app. Routes are sync and submit via `call_soon_threadsafe`.
- **Rollback re-applies the last SUCCEEDED deployment's stored `spec_json`**; it is not `kubectl rollout undo`. A first-ever deployment that fails ends as FAILED, since there is nothing to roll back to.
- `k8s/manifests.py`: manifests are generated in code from `AppSpec`, never hand-written per app. **Every Deployment must set CPU/memory requests** (HPA utilisation is a percentage of the request, and the recommender tunes them). The Deployment deliberately omits `replicas` so applies don't fight the HPA.
- `k8s/ops.py` (apply via server-side apply, rollout wait, delete, HPA patch) and `k8s/observe.py` (pods, Metrics Server usage, HPA, per-pod `/metrics` scrape through the API-server pod proxy, events, logs).
- `intel/collector.py` (15 s samples), `intel/recommender.py` (heuristics over `statistics`, with a plain-language rationale), `intel/prescale.py` (APScheduler cron jobs patching HPA `minReplicas`).
- `api/`: REST routers behind a bearer `API_TOKEN` (the webhook uses a per-app HMAC instead), plus `ws.py` for live events and pod logs.

## Commands

Backend (`cd backend`; the venv lives in `backend/.venv`):
- `.venv\Scripts\python -m pytest`: all tests, no AWS or cluster needed.
- `.venv\Scripts\python -m uvicorn app.main:app --port 8077`: run locally. Port 8000 is blocked on this Windows machine.
- Settings are environment variables, see `backend/.env.example`.

Frontend (`cd frontend`):
- `npm run dev` (set `VITE_API_TARGET` to the backend URL), `npm run build` (type-check plus bundle), `npm run lint`.

Experiments (`cd experiments`, own venv): `python run_experiment.py ...` then `python analyze.py`. See `docs/guide.md` section 9.

Infra and platform (repo root, PowerShell):
- First time: copy `infra/terraform/terraform.tfvars.example` to `terraform.tfvars` and fill it in.
- `scripts/up.ps1` (terraform apply), `scripts/get-kubeconfig.ps1` (writes `.kube/config`), `scripts/deploy-platform.ps1` (build images in CodeBuild, apply `k8s/platform/platform.yaml`, print the dashboard URL and token).
- `scripts/stop.ps1` / `scripts/start.ps1`: stop and start the EC2 nodes between sessions. `scripts/down.ps1` destroys everything.

Sample apps (`sample-apps/flask-app`, `sample-apps/node-app`) listen on 8080 and expose `/`, `/healthz`, `/metrics` and `/work?ms=N`. Run one locally with `python sample-apps/flask-app/app.py` or `node sample-apps/node-app/server.js`.

## Cost notes

Stop the instances when not working. EBS and the Elastic IP are still billed while stopped. CodeBuild small instances get 100 free build-minutes per month. The AWS Budget (~$5) is an alert, not a cap.

## Environment

Development happens on Windows (PowerShell). Laptop needs: AWS CLI v2, Terraform, kubectl, Node 20+, Python 3.11+, hey, and Docker Desktop only for `BUILDER=docker`.
