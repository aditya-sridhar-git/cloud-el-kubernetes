// Sample deployment target: dependency-free Node.js app with the same contract as the Flask app
// (/healthz, /metrics request counter, /work?ms=N CPU burner).
const http = require("http");

const port = 8080;
let requestsTotal = 0;

function json(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");

  // Probes and the metrics scrape would otherwise inflate the request rate the recommender reads.
  if (url.pathname !== "/healthz" && url.pathname !== "/metrics") requestsTotal++;

  if (url.pathname === "/healthz") {
    res.writeHead(200, { "Content-Type": "text/plain" });
    return res.end("ok");
  }

  if (url.pathname === "/metrics") {
    res.writeHead(200, { "Content-Type": "text/plain" });
    return res.end(
      "# HELP http_requests_total Requests served, excluding /healthz and /metrics.\n" +
        "# TYPE http_requests_total counter\n" +
        `http_requests_total ${requestsTotal}\n`
    );
  }

  if (url.pathname === "/work") {
    const requested = parseInt(url.searchParams.get("ms") ?? "50", 10);
    const ms = Math.min(Math.max(Number.isNaN(requested) ? 50 : requested, 0), 5000);
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      // busy loop
    }
    return json(res, 200, { worked_ms: ms });
  }

  if (url.pathname === "/") {
    return json(res, 200, {
      app: "node-app",
      version: process.env.APP_VERSION ?? "dev",
      pod: process.env.HOSTNAME ?? "local",
    });
  }

  json(res, 404, { error: "not found" });
});

server.listen(port, "0.0.0.0", () => console.log(`node-app listening on ${port}`));
