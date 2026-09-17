"""
Unit tests for the sample Flask app's Prometheus instrumentation.

These tests actually run the Flask app with its real test client -
no mocking of prometheus_client - and inspect the real /metrics output.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402


class MetricsFormatTests(unittest.TestCase):
    """Validate that /metrics produces genuinely well-formed exposition text."""

    def setUp(self):
        self.client = app_module.app.test_client()

    def test_metrics_endpoint_returns_200(self):
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)

    def test_metrics_content_type_is_prometheus_exposition(self):
        resp = self.client.get("/metrics")
        self.assertIn("text/plain", resp.content_type)

    def test_metrics_output_has_help_and_type_lines(self):
        resp = self.client.get("/metrics")
        body = resp.get_data(as_text=True)
        self.assertIn("# HELP http_requests_total", body)
        self.assertIn("# TYPE http_requests_total counter", body)
        self.assertIn("# HELP http_request_duration_seconds", body)
        self.assertIn("# TYPE http_request_duration_seconds histogram", body)

    def test_metrics_lines_are_well_formed(self):
        """Every non-comment, non-blank line must look like `name{labels} value`
        or `name value`, per the Prometheus text exposition format."""
        resp = self.client.get("/metrics")
        body = resp.get_data(as_text=True)
        metric_line_re = re.compile(
            r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})?\s+[^\s]+(\s+[0-9]+)?$"
        )
        checked_any = False
        for line in body.splitlines():
            if not line or line.startswith("#"):
                continue
            checked_any = True
            self.assertRegex(
                line, metric_line_re, msg=f"malformed metric line: {line!r}"
            )
        self.assertTrue(checked_any, "expected at least one metric data line")


class RequestCounterTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def _count_for(self, body: str, endpoint: str, method: str, status: str) -> float:
        pattern = re.compile(
            r'^http_requests_total\{endpoint="%s",method="%s",status="%s"\} ([0-9.]+)$'
            % (re.escape(endpoint), re.escape(method), re.escape(status)),
            re.MULTILINE,
        )
        match = pattern.search(body)
        return float(match.group(1)) if match else 0.0

    def test_request_counter_increments_on_success(self):
        before = self._count_for(
            self.client.get("/metrics").get_data(as_text=True), "healthz", "GET", "200"
        )
        self.client.get("/healthz")
        after = self._count_for(
            self.client.get("/metrics").get_data(as_text=True), "healthz", "GET", "200"
        )
        self.assertEqual(after, before + 1)

    def test_request_counter_tracks_distinct_status_codes(self):
        body_before = self.client.get("/metrics").get_data(as_text=True)
        before_404 = self._count_for(body_before, "orders_detail", "GET", "404")

        self.client.get("/orders/does-not-exist")

        body_after = self.client.get("/metrics").get_data(as_text=True)
        after_404 = self._count_for(body_after, "orders_detail", "GET", "404")
        self.assertEqual(after_404, before_404 + 1)

    def test_request_counter_is_per_endpoint(self):
        body_before = self.client.get("/metrics").get_data(as_text=True)
        before_index = self._count_for(body_before, "index", "GET", "200")
        before_health = self._count_for(body_before, "healthz", "GET", "200")

        self.client.get("/")

        body_after = self.client.get("/metrics").get_data(as_text=True)
        after_index = self._count_for(body_after, "index", "GET", "200")
        after_health = self._count_for(body_after, "healthz", "GET", "200")

        self.assertEqual(after_index, before_index + 1)
        self.assertEqual(after_health, before_health)  # unaffected


class LatencyHistogramTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_latency_histogram_records_observation(self):
        body_before = self.client.get("/metrics").get_data(as_text=True)
        count_before = self._observation_count(body_before, "index")

        self.client.get("/")

        body_after = self.client.get("/metrics").get_data(as_text=True)
        count_after = self._observation_count(body_after, "index")

        self.assertEqual(count_after, count_before + 1)

    def _observation_count(self, body: str, endpoint: str) -> float:
        pattern = re.compile(
            r'^http_request_duration_seconds_count\{endpoint="%s",method="GET"\} ([0-9.]+)$'
            % re.escape(endpoint),
            re.MULTILINE,
        )
        match = pattern.search(body)
        return float(match.group(1)) if match else 0.0

    def test_latency_histogram_has_buckets(self):
        resp = self.client.get("/metrics")
        body = resp.get_data(as_text=True)
        self.assertIn("http_request_duration_seconds_bucket", body)
        self.assertIn('le="+Inf"', body)


class InProgressGaugeTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_in_progress_gauge_returns_to_zero_after_request(self):
        # Before any request, the gauge for this endpoint should not be
        # left elevated from a prior request.
        self.client.get("/")
        body = self.client.get("/metrics").get_data(as_text=True)
        pattern = re.compile(
            r'^http_requests_in_progress\{endpoint="index"\} ([0-9.]+)$', re.MULTILINE
        )
        match = pattern.search(body)
        self.assertIsNotNone(match)
        self.assertEqual(float(match.group(1)), 0.0)

    def test_in_progress_gauge_is_exposed_for_metrics_endpoint_itself(self):
        # Sanity check: the metric family exists and is a gauge.
        body = self.client.get("/metrics").get_data(as_text=True)
        self.assertIn("# TYPE http_requests_in_progress gauge", body)


class OrdersBusinessMetricTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def _success_count(self, body: str) -> float:
        pattern = re.compile(
            r'^orders_processed_total\{status="success"\} ([0-9.]+)$', re.MULTILINE
        )
        match = pattern.search(body)
        return float(match.group(1)) if match else 0.0

    def _failed_count(self, body: str) -> float:
        pattern = re.compile(
            r'^orders_processed_total\{status="failed"\} ([0-9.]+)$', re.MULTILINE
        )
        match = pattern.search(body)
        return float(match.group(1)) if match else 0.0

    def test_orders_endpoint_creates_order_with_valid_amount(self):
        resp = self.client.post("/orders", json={"amount": 42.5})
        self.assertEqual(resp.status_code, 201)
        self.assertIn("order_id", resp.get_json())

    def test_orders_counter_increments_on_success(self):
        body_before = self.client.get("/metrics").get_data(as_text=True)
        before = self._success_count(body_before)

        self.client.post("/orders", json={"amount": 10})

        body_after = self.client.get("/metrics").get_data(as_text=True)
        after = self._success_count(body_after)
        self.assertEqual(after, before + 1)

    def test_orders_counter_increments_on_failure_for_bad_input(self):
        body_before = self.client.get("/metrics").get_data(as_text=True)
        before = self._failed_count(body_before)

        resp = self.client.post("/orders", json={"amount": -5})
        self.assertEqual(resp.status_code, 400)

        body_after = self.client.get("/metrics").get_data(as_text=True)
        after = self._failed_count(body_after)
        self.assertEqual(after, before + 1)

    def test_created_order_is_retrievable(self):
        create_resp = self.client.post("/orders", json={"amount": 99})
        order_id = create_resp.get_json()["order_id"]

        get_resp = self.client.get(f"/orders/{order_id}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.get_json()["amount"], 99)


if __name__ == "__main__":
    unittest.main()
