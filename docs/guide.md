# Run guide

How to bring the platform up on AWS, deploy an app from a Git URL, and run the scaling experiments. Commands are PowerShell, run from the repository root.

> **Status of this guide.** The backend (53 tests), the dashboard (build and lint) and the experiment tooling were tested on a laptop. The Terraform, the K3s bootstrap script and the `scripts/*.ps1` files **have not been run against a real AWS account** yet, because `terraform` and the AWS CLI weren't available when they were written. Expect to fix a small issue or two on the first `up.ps1`. The "Troubleshooting" section lists the likeliest ones.

## 1. What you get

```
GitHub push ──webhook──▶ http://platform.<IP>.nip.io ──▶ dashboard + API (pods on K3s)
Dashboard: Git URL ──▶ clone ──▶ S3 ──▶ CodeBuild ──▶ ECR ──▶ K3s (Deployment + Service + Ingress + HPA)
                                                              └─▶ http://<app>.<IP>.nip.io
```

`<IP>` is the Elastic IP of the K3s server. [nip.io](https://nip.io) is a public DNS service that resolves `anything.1.2.3.4.nip.io` to `1.2.3.4`, so no domain is needed.

### What an app must have to be deployable
- A **public HTTPS Git repository** (GitHub, GitLab, ...).
- A **`Dockerfile` at the repository root**. There is no auto-detection.
- The container listens on one port (default 8080) and answers a **health check path** (default `/healthz`) with HTTP 200. Both are set per app.
- A single container. Apps needing a database or several services are not supported.
- Optional, for the request-rate charts and the recommender: expose `/metrics` with a line `http_requests_total <number>`. See [sample-apps/flask-app/app.py](../sample-apps/flask-app/app.py). Without it everything else still works.

## 2. Prerequisites

Install once (a new terminal is needed afterwards):

```powershell
winget install Hashicorp.Terraform
winget install Amazon.AWSCLI
winget install Kubernetes.kubectl
winget install OpenJS.NodeJS.LTS      # only for frontend development
winget install Python.Python.3.12     # backend tests and experiments
```

- **Load generator:** install [`hey`](https://github.com/rakyll/hey) (a single binary; put it on your PATH). Locust is installed from `experiments/requirements.txt` later.
- **Docker Desktop** is only needed for `BUILDER=docker` local development. The deployed platform builds everything in CodeBuild.

### AWS account
1. Use a personal AWS account. Create an IAM user (or use SSO) with permissions for EC2, VPC, IAM, S3, ECR, CodeBuild, CloudWatch Logs, Budgets and SSM. `AdministratorAccess` is simplest for a class project.
2. Run `aws configure` and enter the access key, secret key and region (the default in this repo is `ap-south-1`).
3. Verify: `aws sts get-caller-identity`.

## 3. Bring up the infrastructure

```powershell
Copy-Item infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
notepad infra/terraform/terraform.tfvars
```

Set:
- `admin_cidrs`: your public IP as `["x.x.x.x/32"]`. Find it with `curl https://checkip.amazonaws.com`. Only this address can reach the Kubernetes API (6443) and SSH.
- `budget_email`: receives the $5 budget alerts. Confirm the subscription email AWS sends.
- For the experiments set `cpu_credits = "unlimited"` (see section 9).

Then:

```powershell
scripts/up.ps1                  # terraform init + apply; review the plan and type "yes"
```

Wait **3–5 minutes** for K3s to bootstrap on first boot, then:

```powershell
scripts/get-kubeconfig.ps1      # writes .kube/config pointing at the Elastic IP
$env:KUBECONFIG = "$PWD\.kube\config"
kubectl get nodes               # the server should be Ready
kubectl top nodes               # works once Metrics Server is up (about a minute)
kubectl get pods -A             # coredns, traefik, metrics-server running
```

## 4. Deploy the platform

```powershell
scripts/deploy-platform.ps1
```

This zips `backend/` and `frontend/`, builds both images in CodeBuild (about 3–6 minutes each), creates the config and a random API token, and applies [k8s/platform/platform.yaml](../k8s/platform/platform.yaml). At the end it prints:

```
Dashboard:  http://platform.<IP>.nip.io
API token:  <token>
Webhook:    http://platform.<IP>.nip.io/api/webhooks/github
```

Open the dashboard and paste the token. Later runs reuse the same token. To re-apply manifests without rebuilding: `scripts/deploy-platform.ps1 -SkipBuild`.

## 5. Deploy an app from a Git URL

The sample apps live inside this repo, but an app must be at the **root of its own repository**. Create a public GitHub repo and push the contents of `sample-apps/flask-app/` (so `Dockerfile` and `app.py` are at the top level).

In the dashboard: **+ New app** →
- Name: `flask-app` (becomes `flask-app.<IP>.nip.io`)
- Git URL: `https://github.com/<you>/<repo>.git`, branch `main`
- Port `8080`, health path `/healthz`
- Leave the resource defaults (100m CPU request, HPA 1–5 at 50%).

The app page shows the pipeline live: **QUEUED → CLONING → BUILDING** (CodeBuild log streams in the *Deployments* tab) **→ APPLYING → ROLLING OUT → SUCCEEDED**. Then:

```powershell
curl http://flask-app.<IP>.nip.io/
```

### Redeploy on every push (webhook)
In the GitHub repo: *Settings → Webhooks → Add webhook*.
- Payload URL: the **Payload URL** from the app's *Settings* tab.
- Content type: `application/json`
- Secret: the **Secret** from the same tab (click *Show*).
- Events: "Just the push event".

Push a commit to the branch: a new deployment (trigger `webhook`) starts and rolls out with zero downtime. Check *Recent Deliveries* on GitHub if nothing happens (a `401` means a wrong secret).

## 6. Watch autoscaling

Drive load, then watch the dashboard's *Overview* tab (replicas, CPU vs target, request rate) or `kubectl get hpa -n apps -w`:

```powershell
hey -z 3m -c 40 "http://flask-app.<IP>.nip.io/work?ms=50"
```

Replicas should rise within about a minute (CPU above the 50% target) and fall back a few minutes after load stops. Metric samples are taken every 15 seconds.

## 7. Rollback demo

- **Automatic:** in the sample repo make `/healthz` return HTTP 500 (or make the app crash on start), commit and push. The new pods never become ready, so the rollout fails (within about 2 minutes) and the deployment ends as **ROLLED BACK**. The previous version keeps serving the whole time. (An app's very first deployment has nothing to roll back to and ends as FAILED.)
- **Manual:** *Deployments* tab → **Roll back to this** on any earlier successful deployment.

## 8. Recommendations and pre-scaling

1. Run load for about 5–10 minutes, ideally with the load changing (e.g. an experiment scenario below).
2. *Scaling* tab → **Generate recommendation**. It shows suggested requests/limits, replica range and target CPU next to the current values, with the reasoning in plain text.
3. **Apply** rolls the new configuration out with the same image. If the metrics show the HPA lagging behind load, the recommendation also offers a **pre-scale schedule**: raise `minReplicas` shortly before the daily peak, then restore it.

## 9. Experiments (baseline HPA vs workload-aware configuration)

```powershell
cd experiments
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
$env:PLATFORM_TOKEN = "<API token>"
$P = "http://platform.<IP>.nip.io"
```

For fair results use a `t3.medium` server with `cpu_credits = "unlimited"` in `terraform.tfvars` (re-run `scripts/up.ps1`), so burstable-CPU throttling doesn't distort latency.

1. **Baseline runs** (naive 100m/128Mi requests, HPA 1–10 at 50%). Repeat three times per scenario:
   ```powershell
   .venv\Scripts\python run_experiment.py --platform $P --app flask-app --config baseline --scenario step --label run1
   ```
   Scenarios: `step` (7 min), `spike` (8 min), `periodic` (10 min). Each run first waits for the app to scale back down to its minimum.
2. **Generate a recommendation** from those runs (*Scaling* tab, analyse the last 1–3 hours).
3. **Recommended runs** with the same scenarios, three times each:
   ```powershell
   .venv\Scripts\python run_experiment.py --platform $P --app flask-app --config recommended --scenario step --label run1
   ```
4. **Analyse:**
   ```powershell
   .venv\Scripts\python analyze.py
   ```
   Writes `experiments/results/summary.csv`, `summary.md` (mean ± std dev per scenario and configuration) and one PNG per scenario. Metrics compared: p95 latency, error rate, replica-minutes (a cost proxy), average CPU vs target, time overloaded, time over-provisioned, and time to reach peak replicas.

`--peak-users` (default 40) and `--work-ms` (default 50) change the load. Scale the peak to the cluster: one t3.medium has about 2 vCPUs, so far more than ~100 req/s at 50 ms of CPU each saturates the node regardless of configuration.

## 10. Day-to-day and cost

```powershell
scripts/stop.ps1      # stop the EC2 node(s) between sessions
scripts/start.ps1     # start them again (same Elastic IP; give K3s about a minute)
scripts/down.ps1      # destroy everything (ECR images, S3, cluster state)
```

- A running `t3.small` (the default) costs roughly $0.75/day, and a `t3.medium` roughly $1.30/day. While stopped you still pay for the EBS volume and the Elastic IP (a few dollars a month).
- CodeBuild's small instance includes 100 free build-minutes per month.
- The $5 budget **only emails you**; it does not stop spending. Run `stop.ps1` or `down.ps1` when you finish.
- If your public IP changes (new network), update `admin_cidrs` and re-run `scripts/up.ps1`, otherwise `kubectl` times out.

## 11. Developing locally

Backend (against the live cluster, or with no cluster to just explore the API):

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest                       # 53 tests, no AWS or cluster needed
Copy-Item .env.example .env                          # fill in from: terraform -chdir=infra/terraform output
.venv\Scripts\python -m uvicorn app.main:app --port 8077   # port 8000 is blocked on some Windows machines
```

With `BUILDER=docker` (and Docker Desktop running) the backend builds locally and pushes to ECR; with `BUILDER=codebuild` it uses CodeBuild. It reaches the cluster through `KUBECONFIG`.

Dashboard:

```powershell
cd frontend
npm install
$env:VITE_API_TARGET = "http://localhost:8077"
npm run dev                                          # http://localhost:5173
npm run build; npm run lint
```

## 12. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `get-kubeconfig.ps1`: "Kubeconfig not in S3 yet" | K3s is still bootstrapping, or the bootstrap failed. Connect with SSM (EC2 console → instance → Connect → Session Manager) and read `/var/log/k3s-bootstrap.log`. |
| `kubectl` times out | Your IP changed: update `admin_cidrs`, re-run `scripts/up.ps1`. Or the instance is stopped: `scripts/start.ps1`. |
| App deployment FAILED with "Unable to locate credentials" | The backend pod can't reach the node's instance profile. The nodes need IMDS hop limit 2 (set in Terraform); check `aws ec2 describe-instances` → `HttpPutResponseHopLimit`. |
| Build fails: "repository has no Dockerfile" | The Dockerfile must be at the repo root, on the branch you registered. |
| Build FAILED in CodeBuild | The error includes the CodeBuild phase; the full log is in the dashboard's *Deployments* tab or the CodeBuild console. Base images pulled from Docker Hub can hit rate limits: use `public.ecr.aws/docker/library/...` base images as the platform's own Dockerfiles do. |
| Pods in `ImagePullBackOff` | Check `kubectl describe pod -n apps <pod>`. The node must have the ECR credential provider (look for `/var/lib/rancher/credentialprovider/` on the node and the kubelet args in `/etc/rancher/k3s/config.yaml`), and the image tag must exist in ECR. |
| Rollout "progress deadline exceeded" | The container isn't passing its readiness probe: wrong port or health path in the app settings, or the app crashes (`kubectl logs -n apps deploy/<app>`). |
| `<app>.<IP>.nip.io` doesn't resolve | Some routers and DNS servers block answers that point to a private/other IP ("DNS rebinding protection"). Switch the DNS server to 8.8.8.8 or 1.1.1.1, or use `curl --resolve`. |
| Charts empty or `cpu = 0` | Metrics Server needs about a minute after pods start; request rate needs the app's `/metrics` endpoint. |
| Webhook delivery returns 401 | The secret in GitHub doesn't match the app's secret (*Settings* tab). |
| Recommendation: "need at least 10 samples" | Run load for a few minutes first, and widen the analysis window. |
| `terraform apply` errors on names | S3 bucket names are global; change `name` in `terraform.tfvars`. |

Useful commands: `kubectl get pods -A`, `kubectl logs -n platform deploy/platform-backend`, `kubectl describe hpa -n apps <app>`, `kubectl get events -n apps --sort-by=.lastTimestamp`.

## 13. Known limitations

- Single control plane (no high availability), manual K3s upgrades, fixed node count: pods autoscale, nodes do not. The recommender caps `maxReplicas` at what the cluster can schedule and says so.
- One shared API token for the dashboard; the platform is exposed over plain HTTP. Fine for a classroom demo, not for real use.
- Only public repositories and Dockerfile-based, single-container apps.
- All nodes share one IAM role (ECR pull plus the backend's S3/CodeBuild access).
- The recommender is a set of explainable heuristics, not a new autoscaling algorithm. Its output should be evaluated with the experiments above rather than trusted blindly.
