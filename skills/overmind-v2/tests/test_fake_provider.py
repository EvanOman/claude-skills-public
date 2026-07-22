from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from support import TESTS


class FakeProviderFixtureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="overmind-v2-fake-test.")
        self.root = Path(self.temporary.name)
        self.env = dict(os.environ)
        self.env.update(
            OVERMIND_V2_FAKE_STATE_DIR=str(self.root / "state"),
            OVERMIND_V2_FAKE_CALL_LOG=str(self.root / "calls.jsonl"),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def call(self, action: str, request: dict[str, object]) -> dict[str, object]:
        completed = subprocess.run(
            [str(TESTS / "fake_provider.py"), action],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            env=self.env,
            check=True,
            timeout=3,
        )
        value = json.loads(completed.stdout)
        self.assertIsInstance(value, dict)
        return value

    def test_lifecycle_is_deterministic_and_billing_visible(self) -> None:
        capabilities = self.call("capabilities", {})
        self.assertEqual("fake", capabilities["provider"])
        launched = self.call(
            "launch",
            {
                "billing_class": "subscription-native",
                "fake": {"mode": "success", "delay": 0.03, "result": "fixture-result"},
            },
        )
        self.assertEqual("running", launched["state"])
        time.sleep(0.04)
        reconciled = self.call(
            "reconcile", {"provider_job_id": launched["provider_job_id"]}
        )
        self.assertEqual("succeeded", reconciled["state"])
        self.assertEqual("fixture-result", Path(str(reconciled["result_path"])).read_text())

        held = self.call("launch", {"fake": {"mode": "hold"}})
        stopped = self.call(
            "interrupt", {"provider_job_id": held["provider_job_id"]}
        )
        self.assertEqual("interrupted", stopped["state"])
        self.assertTrue(stopped["signal_sent"])

        fallback = self.call(
            "launch",
            {
                "billing_class": "subscription-native",
                "fake": {"mode": "fallback-metered"},
            },
        )
        self.assertEqual("explicit-metered", fallback["billing_class"])


if __name__ == "__main__":
    unittest.main()
