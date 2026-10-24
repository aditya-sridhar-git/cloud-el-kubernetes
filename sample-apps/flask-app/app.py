"""Sample deployment target: a tiny Flask app with a health probe, a request counter and a CPU-burning endpoint."""
import os
import threading
import time

from flask import Flask, Response, jsonify, request

app = Flask(__name__)

_lock = threading.Lock()
_requests_total = 0


@app.before_request
def count_request():
    global _requests_total
    # Probes and the metrics scrape would otherwise inflate the request rate the recommender reads.
    if request.path not in ("/healthz", "/metrics"):
        with _lock:
            _requests_total += 1


@app.get("/")
def index():
    return jsonify(app="flask-app", version=os.getenv("APP_VERSION", "dev"), pod=os.getenv("HOSTNAME", "local"))


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/work")
def work():
    """Busy-loop for ?ms=N milliseconds so load tests can generate CPU usage."""
    ms = min(max(request.args.get("ms", default=50, type=int), 0), 5000)
    deadline = time.perf_counter() + ms / 1000
    while time.perf_counter() < deadline:
        pass
    return jsonify(worked_ms=ms)


@app.get("/metrics")
def metrics():
    with _lock:
        total = _requests_total
    body = (
        "# HELP http_requests_total Requests served, excluding /healthz and /metrics.\n"
        "# TYPE http_requests_total counter\n"
        f"http_requests_total {total}\n"
    )
    return Response(body, mimetype="text/plain")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
