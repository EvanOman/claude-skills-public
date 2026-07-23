from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from overmind_v2 import TERMINAL_STATES  # noqa: E402
from overmind_v2.broker import Broker  # noqa: E402
from overmind_v2.providers import FakeProvider  # noqa: E402


class DoctorRecentFailureEvidenceTest(unittest.TestCase):
    """`doctor` must surface broker-observed terminal failures per provider.

    A CLI-based capability probe (`available`/`authenticated`) cannot see
    exhausted subscription quota; the broker's own job history can.
    """

    def test_doctor_surfaces_last_terminal_provider_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="overmind-v2-doctor.") as root:
            state_dir = Path(root)
            broker = Broker(state_dir, providers={"codex": FakeProvider()}, recover=False)
            try:
                created = broker.run(
                    {
                        "provider": "codex",
                        "cwd": str(state_dir),
                        "brief": "FAKE_FAIL FAKE_SLEEP=0",
                    }
                )
                job_id = created["jobs"][0]["id"]
                job = broker.store.get_job(job_id)
                deadline = time.monotonic() + 2
                while job["state"] not in TERMINAL_STATES and time.monotonic() < deadline:
                    job = broker._reconcile_job(job_id)
                    time.sleep(0.01)
                self.assertEqual("failed", job["state"], job)

                report = broker.doctor()
                # FakeProvider.production is False, so it reports under
                # test_providers rather than providers.
                evidence = report["test_providers"]["codex"]["last_failure"]
                self.assertIsNotNone(evidence)
                self.assertEqual(job_id, evidence["job_id"])
                self.assertIn("deterministic fake failure", evidence["message"])
                self.assertIsInstance(evidence["occurred_at"], float)
            finally:
                broker.close()

    def test_doctor_reports_none_when_no_terminal_failure_recorded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="overmind-v2-doctor-clean.") as root:
            broker = Broker(
                Path(root), providers={"codex": FakeProvider()}, recover=False
            )
            try:
                report = broker.doctor()
                self.assertIsNone(report["test_providers"]["codex"]["last_failure"])
            finally:
                broker.close()

    def test_doctor_evidence_excerpt_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="overmind-v2-doctor-bound.") as root:
            state_dir = Path(root)
            broker = Broker(state_dir, providers={"codex": FakeProvider()}, recover=False)
            try:
                job_id, short_id = broker.store.allocate_id("jobs")
                group_id, group_short_id = broker.store.allocate_id("groups")
                job_dir = broker.store.artifacts_dir / job_id
                job_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                brief_path = job_dir / "brief.txt"
                brief_path.write_text("noop", encoding="utf-8")
                broker.store.create_launch(
                    operation="run",
                    request_payload={"jobs": [{"provider": "codex"}]},
                    group={
                        "id": group_id,
                        "short_id": group_short_id,
                        "label": "doctor-bound",
                    },
                    jobs=[
                        {
                            "id": job_id,
                            "short_id": short_id,
                            "group_id": group_id,
                            "parent_job_id": None,
                            "provider": "codex",
                            "label": "codex-1",
                            "cwd": str(state_dir),
                            "billing_class": "subscription-native",
                            "brief_path": str(brief_path),
                        }
                    ],
                    idempotency_key=None,
                )
                long_message = "quota exhausted " * 100
                broker.store.update_job(
                    job_id,
                    kind="job.reconciled",
                    state="failed",
                    fields={"error": long_message},
                )

                report = broker.doctor()
                evidence = report["test_providers"]["codex"]["last_failure"]
                self.assertLessEqual(len(evidence["message"]), 501)
                self.assertTrue(evidence["message"].endswith("…"))
            finally:
                broker.close()


if __name__ == "__main__":
    unittest.main()
