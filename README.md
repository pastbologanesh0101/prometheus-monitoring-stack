# Prometheus Monitoring Stack

A small, fully working demo of a Prometheus-instrumented Flask application,
paired with a scrape config, Docker Compose wiring, and alerting rules.

> **Honesty note on this environment:** the Docker daemon was not available
> when this project was built, so Prometheus itself was **never actually run
> against this app here**. What *was* run and verified locally:
>
> - The real Flask app, with real requests hitting real endpoints, producing
>   a real `/metrics` output in valid Prometheus exposition format.
> - `prometheus.yml`, `docker-compose.yml`, and `alerts.yml` parsed with
>   PyYAML and checked with structural assertions (scrape target hostnames
>   match compose service names, intervals are sane, job names are unique,
>   alert names are unique, alert expressions reference metrics that
>   actually exist in `app.py`).
>
> Running `docker compose up` on a machine with Docker installed completes
> the loop end-to-end (Prometheus actually scraping the app and evaluating
> the alert rules) — see "Completing the loop" below.

## Architecture

```
                 scrape /metrics every 10s
   ┌─────────────┐   HTTP GET   ┌──────────────┐
   │  Prometheus  │ ───────────▶│  Flask app   │
   │  (port 9090) │◀─────────── │  (port 5000) │
   └──────┬───────┘   metrics   └──────────────┘
          │
          │ evaluates alerts.yml
          ▼
    (Alertmanager target list is present in
     prometheus.yml but no Alertmanager is
     wired up in this minimal demo)
```

Both containers run on a shared Docker bridge network (`monitoring`) defined
in `docker-compose.yml`, so Prometheus can resolve the app by its service
name (`app`), exactly as configured in `prometheus.yml`'s `flask_app` scrape
job (`targets: ["app:5000"]`). Prometheus's own scraped/evaluated data is
persisted to the named volume `prometheus_data`.

## The app

`app.py` is a real Flask service instrumented with `prometheus_client`:

- `GET /` – trivial index endpoint.
- `GET /healthz` – health check.
- `POST /orders` – demo business endpoint. Accepts `{"amount": <number>}`,
  rejects non-positive/missing amounts with a 400, and otherwise stores the
  order in memory and returns a 201 with an `order_id`.
- `GET /orders/<order_id>` – fetch a previously created order.
- `GET /flaky` – randomly returns a 500 about 30% of the time, useful for
  exercising the error-rate alert.
- `GET /metrics` – Prometheus exposition-format metrics.

### Metrics exposed

| Metric | Type | Labels | What it measures |
|---|---|---|---|
| `http_requests_total` | Counter | `endpoint`, `method`, `status` | Total requests, by endpoint and outcome |
| `http_request_duration_seconds` | Histogram | `endpoint`, `method` | Request latency distribution |
| `http_requests_in_progress` | Gauge | `endpoint` | Requests currently being handled |
| `orders_processed_total` | Counter | `status` (`success`/`failed`) | Business metric: orders processed via `/orders` |
| `order_value_dollars` | Histogram | – | Distribution of accepted order amounts |

### Example `/metrics` output (captured from a real local run)

```
# HELP http_requests_total Total number of HTTP requests received
# TYPE http_requests_total counter
http_requests_total{endpoint="index",method="GET",status="200"} 1.0
http_requests_total{endpoint="healthz",method="GET",status="200"} 1.0
http_requests_total{endpoint="orders",method="POST",status="201"} 1.0
http_requests_total{endpoint="orders",method="POST",status="400"} 1.0
http_requests_total{endpoint="orders_detail",method="GET",status="404"} 1.0
# HELP http_request_duration_seconds HTTP request latency in seconds
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_count{endpoint="index",method="GET"} 1.0
http_request_duration_seconds_count{endpoint="healthz",method="GET"} 1.0
http_request_duration_seconds_count{endpoint="orders",method="POST"} 2.0
http_request_duration_seconds_count{endpoint="orders_detail",method="GET"} 1.0
# HELP http_requests_in_progress Number of HTTP requests currently being processed
# TYPE http_requests_in_progress gauge
http_requests_in_progress{endpoint="index"} 0.0
http_requests_in_progress{endpoint="healthz"} 0.0
http_requests_in_progress{endpoint="orders"} 0.0
http_requests_in_progress{endpoint="orders_detail"} 0.0
# HELP orders_processed_total Total number of orders successfully processed
# TYPE orders_processed_total counter
orders_processed_total{status="success"} 1.0
orders_processed_total{status="failed"} 1.0
```

## Running the app directly (development)

No Docker required for this part:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then, in another terminal:

```bash
curl http://localhost:5000/
curl -X POST http://localhost:5000/orders -H 'Content-Type: application/json' -d '{"amount": 42}'
curl http://localhost:5000/metrics
```

## Completing the loop with Docker

Once Docker is available:

```bash
docker compose up --build
```

This builds the Flask app image, starts it alongside Prometheus on a shared
`monitoring` network, and Prometheus will begin scraping
`http://app:5000/metrics` every 10 seconds per `prometheus.yml`. Visit
`http://localhost:9090` to browse the Prometheus UI, check target health
under **Status → Targets**, and see the alert rules under **Alerts**.

## Alert rules (`alerts.yml`)

| Alert | Detects |
|---|---|
| `HighErrorRate` | More than 5% of requests over 5 minutes returning a 5xx status, from `http_requests_total`. |
| `HighRequestLatency` | p95 request latency (via `histogram_quantile` over `http_request_duration_seconds_bucket`) exceeding 500ms for 5+ minutes. |
| `TooManyInProgressRequests` | `http_requests_in_progress` summed across endpoints staying above 20 for 2+ minutes (possible stall). |
| `HighOrderFailureRate` | More than 20% of `/orders` submissions failing validation over 10 minutes, from `orders_processed_total`. |

## Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

28 tests, all passing locally in this environment:

- `tests/test_app.py` – runs the real Flask app via its test client and
  checks `/metrics` format, counter/histogram/gauge behavior, and the
  `/orders` business metric.
- `tests/test_config_validation.py` – loads `prometheus.yml`,
  `docker-compose.yml`, and `alerts.yml` with PyYAML and asserts on their
  structure (scrape target ↔ compose service name matching, sane
  intervals, unique job/alert names, alert expressions referencing real
  metric names pulled from `app.py`).

CI (`.github/workflows/tests.yml`) runs the same suite on Python 3.11 and
3.12 on every push and pull request — no Docker step, since the tests
never need a live Prometheus.

## License

MIT, see [LICENSE](LICENSE).
