"""
Structural validation tests for prometheus.yml, docker-compose.yml and
alerts.yml.

These do NOT start a real Prometheus server (no Docker available in this
environment) - they parse the YAML with PyYAML and assert on its structure,
and cross-check it against the actual metric names defined in app.py and
the actual service names defined in docker-compose.yml.
"""
import os
import re
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_yaml(filename):
    with open(os.path.join(ROOT, filename)) as fh:
        return yaml.safe_load(fh)


def _app_metric_names():
    """Extract metric names declared in app.py, e.g. Counter("foo_total", ...)."""
    with open(os.path.join(ROOT, "app.py")) as fh:
        source = fh.read()
    return set(re.findall(r'(?:Counter|Histogram|Gauge)\(\s*"([a-zA-Z_:][a-zA-Z0-9_:]*)"', source))


class ComposeStructureTests(unittest.TestCase):
    def setUp(self):
        self.compose = _load_yaml("docker-compose.yml")

    def test_compose_has_app_and_prometheus_services(self):
        services = self.compose.get("services", {})
        self.assertIn("app", services)
        self.assertIn("prometheus", services)

    def test_compose_services_share_a_network(self):
        services = self.compose.get("services", {})
        app_networks = set(services["app"].get("networks", []))
        prom_networks = set(services["prometheus"].get("networks", []))
        self.assertTrue(app_networks & prom_networks, "app and prometheus must share a network")

    def test_compose_has_named_volume_for_prometheus_data(self):
        volumes = self.compose.get("volumes", {})
        self.assertIn("prometheus_data", volumes)
        prom_mounts = self.compose["services"]["prometheus"].get("volumes", [])
        self.assertTrue(
            any(v.startswith("prometheus_data:") for v in prom_mounts),
            "prometheus service must mount the named volume",
        )

    def test_compose_mounts_prometheus_and_alert_config_files(self):
        prom_mounts = self.compose["services"]["prometheus"].get("volumes", [])
        joined = " ".join(prom_mounts)
        self.assertIn("prometheus.yml", joined)
        self.assertIn("alerts.yml", joined)


class PrometheusScrapeConfigTests(unittest.TestCase):
    def setUp(self):
        self.prom_config = _load_yaml("prometheus.yml")
        self.compose = _load_yaml("docker-compose.yml")

    def test_scrape_target_hostname_matches_compose_service_name(self):
        service_names = set(self.compose.get("services", {}).keys())
        scrape_configs = self.prom_config.get("scrape_configs", [])
        app_jobs = [j for j in scrape_configs if j.get("job_name") == "flask_app"]
        self.assertEqual(len(app_jobs), 1)

        targets = app_jobs[0]["static_configs"][0]["targets"]
        self.assertTrue(targets, "flask_app job must declare at least one target")
        for target in targets:
            hostname = target.split(":")[0]
            self.assertIn(
                hostname,
                service_names,
                f"scrape target host {hostname!r} must match a docker-compose service name",
            )

    def test_scrape_intervals_are_sane(self):
        global_interval = self.prom_config["global"]["scrape_interval"]
        self.assertRegex(global_interval, r"^\d+[smh]$")
        seconds = int(global_interval[:-1])
        self.assertGreaterEqual(seconds, 5)
        self.assertLessEqual(seconds, 300)

        for job in self.prom_config.get("scrape_configs", []):
            if "scrape_interval" in job:
                interval = job["scrape_interval"]
                self.assertRegex(interval, r"^\d+[smh]$")
                job_seconds = int(interval[:-1])
                self.assertGreaterEqual(job_seconds, 5)
                self.assertLessEqual(job_seconds, 300)

    def test_job_names_are_unique(self):
        job_names = [j["job_name"] for j in self.prom_config.get("scrape_configs", [])]
        self.assertEqual(len(job_names), len(set(job_names)), "job_name values must be unique")

    def test_prometheus_config_references_alert_rules_file(self):
        rule_files = self.prom_config.get("rule_files", [])
        self.assertIn("alerts.yml", rule_files)


class AlertRulesTests(unittest.TestCase):
    def setUp(self):
        self.alerts_config = _load_yaml("alerts.yml")
        self.known_metrics = _app_metric_names()

    def test_alerts_yaml_is_well_structured(self):
        groups = self.alerts_config.get("groups", [])
        self.assertTrue(groups, "alerts.yml must define at least one group")
        for group in groups:
            self.assertIn("name", group)
            self.assertIn("rules", group)
            self.assertTrue(group["rules"])

    def test_alert_names_are_unique(self):
        names = []
        for group in self.alerts_config.get("groups", []):
            for rule in group.get("rules", []):
                names.append(rule["alert"])
        self.assertEqual(len(names), len(set(names)), "alert names must be unique")

    def test_alert_expressions_reference_real_app_metrics(self):
        self.assertTrue(self.known_metrics, "expected to find metrics declared in app.py")
        for group in self.alerts_config.get("groups", []):
            for rule in group.get("rules", []):
                expr = rule["expr"]
                referenced = {m for m in self.known_metrics if m in expr}
                self.assertTrue(
                    referenced,
                    f"alert {rule['alert']!r} expr does not reference any known metric: {expr!r}",
                )

    def test_every_alert_has_severity_and_annotations(self):
        for group in self.alerts_config.get("groups", []):
            for rule in group.get("rules", []):
                self.assertIn("severity", rule.get("labels", {}))
                self.assertIn("summary", rule.get("annotations", {}))
                self.assertIn("description", rule.get("annotations", {}))

    def test_high_error_rate_and_high_latency_alerts_exist(self):
        names = {
            rule["alert"]
            for group in self.alerts_config.get("groups", [])
            for rule in group.get("rules", [])
        }
        self.assertIn("HighErrorRate", names)
        self.assertIn("HighRequestLatency", names)


if __name__ == "__main__":
    unittest.main()
