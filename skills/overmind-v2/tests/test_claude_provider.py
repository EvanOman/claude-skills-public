from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from overmind_v2.providers import ClaudeProvider  # noqa: E402


class ClaudeProviderReconcileTest(unittest.TestCase):
    def reconcile(self, state: str, **fields: object) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="overmind-v2-claude-state.") as root:
            job_dir = Path(root) / "job"
            job_dir.mkdir()
            state_path = job_dir / "state.json"
            state_path.write_text(
                json.dumps({"state": state, **fields}), encoding="utf-8"
            )
            job = {
                "provider_job_id": "deadbeef",
                "provider_state_path": str(state_path),
                "brief_path": str(job_dir / "brief.md"),
            }
            return ClaudeProvider().reconcile(job)

    def test_succeeded_detail_is_not_reported_as_an_error(self) -> None:
        update = self.reconcile(
            "completed",
            detail="returned CLAUDE_V2_LIVE_OK",
            output={"result": "CLAUDE_V2_LIVE_OK"},
        )

        self.assertEqual("succeeded", update["state"])
        self.assertNotIn("error", update)

    def test_unsuccessful_states_preserve_provider_detail_or_error(self) -> None:
        cases = (
            ("failed", "detail", "worker failed", "failed"),
            ("cancelled", "detail", "stopped by caller", "interrupted"),
            ("mystery", "error", "unrecognized provider state", "unknown"),
        )
        for raw_state, field, message, expected_state in cases:
            with self.subTest(state=raw_state, field=field):
                update = self.reconcile(raw_state, **{field: message})
                self.assertEqual(expected_state, update["state"])
                self.assertEqual(message, update["error"])


if __name__ == "__main__":
    unittest.main()
