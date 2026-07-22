from __future__ import annotations

import os
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


if __name__ == "__main__":
    unittest.main()
