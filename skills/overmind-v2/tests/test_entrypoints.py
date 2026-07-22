from __future__ import annotations

import os
import subprocess
import unittest

from support import entrypoint


class EntrypointContractTest(unittest.TestCase):
    def test_documented_entrypoints_exist_and_are_executable(self) -> None:
        missing: list[str] = []
        for name in ("cli", "mcp"):
            path = entrypoint(name)
            if not path.is_file():
                missing.append(f"{name}: missing {path}")
            elif not os.access(path, os.X_OK):
                missing.append(f"{name}: not executable {path}")
        self.assertFalse(
            missing,
            "Overmind v2 production is absent or incomplete:\n" + "\n".join(missing),
        )

    def test_cli_help_explains_group_and_preview_inputs(self) -> None:
        cli = entrypoint("cli")
        run_many = subprocess.run(
            [str(cli), "run-many", "--help"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        collect = subprocess.run(
            [str(cli), "collect", "--help"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(0, run_many.returncode, run_many.stderr)
        self.assertIn("provider, brief, cwd, and label", run_many.stdout)
        self.assertIn("idempotency_key", run_many.stdout)
        self.assertEqual(0, collect.returncode, collect.stderr)
        self.assertIn("one group/job ID", collect.stdout)
        self.assertIn("--preview-bytes", collect.stdout)


if __name__ == "__main__":
    unittest.main()
