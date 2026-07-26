from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from overmind_v2 import sessions  # noqa: E402


class SessionRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory(prefix="overmind-v2-sessions.")
        self.addCleanup(self._tempdir.cleanup)
        self.state_dir = Path(self._tempdir.name)

    def test_a_registered_live_process_reads_back_as_live(self) -> None:
        sessions.register(self.state_dir, "session-a", os.getpid(), cwd="/tmp")

        records = sessions.read_all(self.state_dir)

        self.assertEqual(1, len(records))
        self.assertEqual("session-a", records[0]["session_id"])
        self.assertTrue(sessions.is_live(records[0]))

    def test_a_dead_pid_is_not_live(self) -> None:
        """The whole point: a session that exited must be detectable as gone."""

        record = {"session_id": "gone", "pid": 999_999_999, "identity": "1"}

        self.assertFalse(sessions.is_live(record))

    def test_a_recycled_pid_is_not_mistaken_for_the_original(self) -> None:
        """pids are reused; claiming a stranger's process would be the worst failure."""

        record = {
            "session_id": "stale",
            "pid": os.getpid(),
            "identity": "definitely-not-this-process",
        }

        self.assertFalse(sessions.is_live(record))

    def test_a_malformed_record_is_ignored_rather_than_fatal(self) -> None:
        directory = sessions.sessions_dir(self.state_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "broken.json").write_text("{not json", encoding="utf-8")
        sessions.register(self.state_dir, "session-b", os.getpid())

        records = sessions.read_all(self.state_dir)

        self.assertEqual(["session-b"], [r["session_id"] for r in records])

    def test_ancestry_reaches_this_process_and_its_parent(self) -> None:
        chain = sessions.ancestry(os.getpid())

        self.assertEqual(os.getpid(), chain[0])
        self.assertIn(os.getppid(), chain)

    def test_a_process_is_attributed_to_a_registered_ancestor(self) -> None:
        sessions.register(self.state_dir, "owner-session", os.getppid())

        self.assertEqual(
            "owner-session", sessions.owning_session(self.state_dir, os.getpid())
        )

    def test_an_unregistered_ancestry_attributes_to_nothing(self) -> None:
        self.assertIsNone(sessions.owning_session(self.state_dir, os.getpid()))

    def test_an_explicit_owner_overrides_ancestry(self) -> None:
        sessions.register(self.state_dir, "owner-session", os.getppid())
        os.environ["OVERMIND_V2_OWNER_SESSION"] = "forced"
        self.addCleanup(os.environ.pop, "OVERMIND_V2_OWNER_SESSION", None)

        self.assertEqual("forced", sessions.owning_session(self.state_dir, os.getpid()))

    def test_forget_removes_a_session(self) -> None:
        sessions.register(self.state_dir, "session-c", os.getpid())
        sessions.forget(self.state_dir, "session-c")

        self.assertEqual([], sessions.read_all(self.state_dir))

    def test_registering_twice_updates_rather_than_duplicates(self) -> None:
        sessions.register(self.state_dir, "session-d", os.getpid())
        sessions.register(self.state_dir, "session-d", os.getpid(), cwd="/elsewhere")

        records = sessions.read_all(self.state_dir)

        self.assertEqual(1, len(records))
        self.assertEqual("/elsewhere", records[0]["cwd"])

    def test_the_registry_file_is_json_and_private(self) -> None:
        sessions.register(self.state_dir, "session-e", os.getpid())
        path = sessions.sessions_dir(self.state_dir) / "session-e.json"

        json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(0o600, path.stat().st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
