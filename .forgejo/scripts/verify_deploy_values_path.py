from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("deploy-values-path.py")
ROOT = SCRIPT.parents[2]
SPEC = importlib.util.spec_from_file_location("deploy_values_path", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DeployValuesPathTests(unittest.TestCase):
    def assert_transient_write_retry_contract(self, workflow: str) -> None:
        self.assertIn("for write_attempt in $(seq 1 10)", workflow)
        self.assertIn("409|500|502|503)", workflow)
        self.assertIn("retrying fresh read", workflow)

    def test_lifecycle_is_the_only_mail_release_writer(self) -> None:
        workflow = (ROOT / ".forgejo/workflows/deploy.yml").read_text()
        self.assertIn(
            'deploy-values-path.py set /tmp/mail-values.yaml image.tag "${TAG}"',
            workflow,
        )
        self.assertIn("desired image tag {sys.argv[1]}", workflow)
        self.assert_transient_write_retry_contract(workflow)
        self.assertIn("FINAL_MAIL_TAG=", workflow)
        self.assertIn("central lifecycle did not converge Mail release", workflow)
        self.assertIn("for attempt in $(seq 1 540)", workflow)
        self.assertIn('status.get("observedGeneration", 0)', workflow)
        self.assertIn('status.get("updatedReplicas", 0) == replicas', workflow)
        self.assertIn('status.get("availableReplicas", 0) == replicas', workflow)
        self.assertIn('status.get("readyReplicas", 0) == replicas', workflow)
        self.assertIn('CURRENT_DESIRED_TAG="$(python3', workflow)
        self.assertIn('STORED_TAG="$(helm get values shizuha-mail', workflow)
        self.assertIn('LIVE="$(kubectl get deploy -n shizuha shizuha-mail', workflow)
        self.assertNotIn("helm upgrade shizuha-mail", workflow)
        self.assertNotIn("helm rollback shizuha-mail", workflow)
        self.assertNotIn("kubectl set image", workflow)
        self.assertNotIn("[ci skip]", workflow)
        self.assertNotIn("WARNING: values.yaml update failed", workflow)

    def test_mail_ci_jobs_are_bounded_and_stale_verifiers_are_reaped(self) -> None:
        backend = (ROOT / ".forgejo/workflows/deploy.yml").read_text()
        frontend = (ROOT / ".forgejo/workflows/deploy-frontend.yml").read_text()
        self.assertIn(
            "kubectl delete job -n build -l shizuha.io/orig-task=MAIL-48",
            backend,
        )
        self.assertGreaterEqual(backend.count("activeDeadlineSeconds:"), 3)
        self.assertGreaterEqual(frontend.count("activeDeadlineSeconds:"), 3)
        for label, workflow in (("backend", backend), ("frontend", frontend)):
            with self.subTest(workflow=label):
                ttl_values = [
                    int(value)
                    for value in re.findall(r"ttlSecondsAfterFinished:\s*(\d+)", workflow)
                ]
                self.assertGreaterEqual(len(ttl_values), 3)
                self.assertTrue(all(value <= 300 for value in ttl_values))
                self.assertIn("--cleanup", workflow)
                self.assertNotIn("--cache=false", workflow)

    def test_mail_multiarch_waits_run_concurrently_and_aggregate_failures(self) -> None:
        # MAIL-86: deploy.yml passes the attempt number to wait_job (PLAT-6113
        # first-attempt cold-cache retry) — `wait_job amd64 1 &`. The frontend
        # workflow's wait_job takes no attempt arg — `wait_job amd64 &`. Both
        # must launch the arch waits concurrently and aggregate their status.
        expected = {
            "deploy.yml": (
                "wait_job amd64 1 &\n"
                "          AMD64_WAIT_PID=$!\n"
                "          wait_job arm64 1 &\n"
                "          ARM64_WAIT_PID=$!\n"
                "          WAIT_STATUS=0\n"
                '          wait "$AMD64_WAIT_PID" || WAIT_STATUS=$?\n'
                '          wait "$ARM64_WAIT_PID" || WAIT_STATUS=$?\n'
                '          [ "$WAIT_STATUS" -eq 0 ] || exit "$WAIT_STATUS"'
            ),
            "deploy-frontend.yml": (
                "wait_job amd64 &\n"
                "          AMD64_WAIT_PID=$!\n"
                "          wait_job arm64 &\n"
                "          ARM64_WAIT_PID=$!\n"
                "          WAIT_STATUS=0\n"
                '          wait "$AMD64_WAIT_PID" || WAIT_STATUS=$?\n'
                '          wait "$ARM64_WAIT_PID" || WAIT_STATUS=$?\n'
                '          [ "$WAIT_STATUS" -eq 0 ] || exit "$WAIT_STATUS"'
            ),
        }
        for name in ("deploy.yml", "deploy-frontend.yml"):
            workflow = (ROOT / ".forgejo/workflows" / name).read_text()
            with self.subTest(workflow=name):
                self.assertIn(expected[name], workflow)
                self.assertNotIn(
                    "wait_job amd64\n          wait_job arm64",
                    workflow,
                )

    def test_frontend_promotes_chart_and_waits_for_helm_ownership(self) -> None:
        workflow = (ROOT / ".forgejo/workflows/deploy-frontend.yml").read_text()
        self.assertIn("actions/checkout@v4", workflow)
        self.assertIn("monotonic-release-guard.sh", workflow)
        self.assertIn('frontend.image.tag "${FTAG}"', workflow)
        self.assertIn("desired frontend tag {sys.argv[1]} before rollout", workflow)
        self.assert_transient_write_retry_contract(workflow)
        self.assertIn("FINAL_FRONTEND_TAG=", workflow)
        self.assertIn("PROMOTED=false", workflow)
        self.assertIn("PROMOTED=true", workflow)
        self.assertIn(
            "actions/workflows/deploy-id-mail-lifecycle.yml/dispatches",
            workflow,
        )
        self.assertIn("if [ \"${PROMOTED}\" != true ]", workflow)
        self.assertIn("STORED_FRONTEND_ENABLED=", workflow)
        self.assertIn("STORED_FRONTEND_TAG=", workflow)
        self.assertIn("STORED_BACKEND_TAG=", workflow)
        self.assertIn("helm_owns_frontend", workflow)
        self.assertIn("deployment_converged shizuha-mail-frontend", workflow)
        self.assertIn("deployment_converged shizuha-mail", workflow)
        self.assertIn("central lifecycle did not converge Mail frontend", workflow)
        self.assertNotIn("kubectl set image", workflow)
        self.assertNotIn("helm upgrade", workflow)
        self.assertNotIn("helm rollback", workflow)

    def test_existing_desired_tag_redispatches_central_lifecycle(self) -> None:
        workflow = (ROOT / ".forgejo/workflows/deploy.yml").read_text()
        self.assertIn("PROMOTED=false", workflow)
        self.assertIn("PROMOTED=true", workflow)
        self.assertIn(
            "actions/workflows/deploy-id-mail-lifecycle.yml/dispatches",
            workflow,
        )
        self.assertIn("if [ \"${PROMOTED}\" != true ]", workflow)
        self.assertIn("test \"${DISPATCH_CODE}\" = 204", workflow)
        self.assertIn("-d '{\"ref\":\"master\"}'", workflow)

    def test_updates_only_backend_image_tag(self) -> None:
        source = (
            "image:\n"
            "  repository: localhost:30500/shizuha-mail\n"
            "  tag: mail-old\n"
            "frontend:\n"
            "  image:\n"
            "    repository: localhost:30500/shizuha-mail-frontend\n"
            "    tag: mail-frontend-old\n"
        )
        lines = source.splitlines(keepends=True)
        index, match, old = MODULE.find_scalar(lines, ("image", "tag"))
        self.assertEqual(old, "mail-old")
        lines[index] = (
            f'{match.group("prefix")}{match.group("key")}:'
            f'{match.group("separator")}mail-new{match.group("newline")}'
        )
        updated = "".join(lines)
        self.assertIn("  tag: mail-new\n", updated)
        self.assertIn("    tag: mail-frontend-old\n", updated)

    def test_updates_only_frontend_image_tag(self) -> None:
        source = (
            "image:\n"
            "  repository: localhost:30500/shizuha-mail\n"
            "  tag: mail-old\n"
            "frontend:\n"
            "  image:\n"
            "    repository: localhost:30500/shizuha-mail-frontend\n"
            "    tag: mail-frontend-old\n"
        )
        lines = source.splitlines(keepends=True)
        index, match, old = MODULE.find_scalar(lines, ("frontend", "image", "tag"))
        self.assertEqual(old, "mail-frontend-old")
        lines[index] = (
            f'{match.group("prefix")}{match.group("key")}:'
            f'{match.group("separator")}mail-frontend-new{match.group("newline")}'
        )
        updated = "".join(lines)
        self.assertIn("  tag: mail-old\n", updated)
        self.assertIn("    tag: mail-frontend-new\n", updated)

    def test_missing_path_fails_closed(self) -> None:
        with self.assertRaisesRegex(SystemExit, "could not locate image.tag"):
            MODULE.find_scalar(["frontend:\n", "  tag: old\n"], ("image", "tag"))

    def test_cli_preserves_file_shape(self) -> None:
        source = "image:\n  tag: old\n\nother: value\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.yaml"
            path.write_text(source)
            lines = path.read_text().splitlines(keepends=True)
            index, match, _ = MODULE.find_scalar(lines, ("image", "tag"))
            lines[index] = (
                f'{match.group("prefix")}{match.group("key")}:'
                f'{match.group("separator")}new{match.group("newline")}'
            )
            path.write_text("".join(lines))
            self.assertEqual(path.read_text(), source.replace("  tag: old", "  tag: new"))


if __name__ == "__main__":
    unittest.main()
