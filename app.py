"""
Prometheus Monitoring Stack - sample Flask application.

A small but genuinely functional Flask service instrumented with the
`prometheus_client` library. It exposes a `/metrics` endpoint in real
Prometheus exposition format, plus a couple of demo business endpoints
whose behaviour drives the custom metrics.
"""
from __future__ import annotations

import random
import time
import uuid

from flask import Flask, Response, jsonify, request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total number of HTTP requests received",
    ["endpoint", "method", "status"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["endpoint", "method"],
)

IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently being processed",
    ["endpoint"],
)

ORDERS_PROCESSED = Counter(
    "orders_processed_total",
    "Total number of orders successfully processed",
    ["status"],
)

ORDER_VALUE = Histogram(
    "order_value_dollars",
    "Distribution of processed order values in dollars",
)

# In-memory "orders" store to keep the demo business logic real.
_ORDERS: dict[str, dict] = {}


def _instrument(endpoint_name: str):
    """Decorator wiring up counter/histogram/gauge for a view function."""

    def decorator(view_func):
        def wrapped(*args, **kwargs):
            IN_PROGRESS.labels(endpoint=endpoint_name).inc()
            start = time.perf_counter()
            status = 500
            try:
                response = view_func(*args, **kwargs)
                # Normalize to a Flask Response so we can read status_code.
                if not isinstance(response, Response):
                    response = app.make_response(response)
                status = response.status_code
                return response
            finally:
                elapsed = time.perf_counter() - start
                REQUEST_LATENCY.labels(
                    endpoint=endpoint_name, method=request.method
                ).observe(elapsed)
                REQUEST_COUNT.labels(
                    endpoint=endpoint_name,
                    method=request.method,
                    status=str(status),
                ).inc()
                IN_PROGRESS.labels(endpoint=endpoint_name).dec()

        wrapped.__name__ = view_func.__name__
        return wrapped

    return decorator


@app.route("/")
@_instrument("index")
def index():
    return jsonify({"service": "prometheus-monitoring-stack-demo", "status": "ok"})


@app.route("/healthz")
@_instrument("healthz")
def healthz():
    return jsonify({"status": "healthy"})


@app.route("/orders", methods=["POST"])
@_instrument("orders")
def create_order():
    """Create a demo order. Real (if tiny) business logic:

    - Validates a required `amount` field.
    - Rejects negative/zero amounts as failed orders.
    - Stores accepted orders in memory and records their value.
    """
    payload = request.get_json(silent=True) or {}
    amount = payload.get("amount")

    if amount is None or not isinstance(amount, (int, float)) or amount <= 0:
        ORDERS_PROCESSED.labels(status="failed").inc()
        return jsonify({"error": "amount must be a positive number"}), 400

    order_id = str(uuid.uuid4())
    _ORDERS[order_id] = {"amount": amount, "order_id": order_id}

    ORDERS_PROCESSED.labels(status="success").inc()
    ORDER_VALUE.observe(float(amount))

    return jsonify({"order_id": order_id, "amount": amount, "status": "processed"}), 201


@app.route("/orders/<order_id>", methods=["GET"])
@_instrument("orders_detail")
def get_order(order_id: str):
    order = _ORDERS.get(order_id)
    if order is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(order)


@app.route("/flaky")
@_instrument("flaky")
def flaky():
    """Endpoint that randomly fails, useful for exercising error-rate alerts."""
    if random.random() < 0.3:
        return jsonify({"error": "simulated failure"}), 500
    return jsonify({"status": "ok"})


@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
