from __future__ import annotations

import importlib.util
import unittest

from support import SKILL


MODULE_PATH = SKILL / "scripts" / "bakeoff.py"
SPEC = importlib.util.spec_from_file_location("overmind_v2_bakeoff", MODULE_PATH)
assert SPEC and SPEC.loader
BAKEOFF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BAKEOFF)


def side(*, calls: int, polling: bool = False, restart: bool = True, p95: float = 10) -> dict[str, object]:
    return {
        "worker_count": 4,
        "terminal_count": 4,
        "result_count": 4,
        "lifecycle_call_count": calls,
        "model_driven_polling": polling,
        "restart_idempotency": restart,
        "status_p95_ms": p95,
    }


class BakeoffReportTest(unittest.TestCase):
    def test_objective_thresholds_pass_at_required_boundary(self) -> None:
        report = BAKEOFF.evaluate(side(calls=12), side(calls=3, p95=49.999), True)
        self.assertTrue(report["pass"], report)
        self.assertTrue(all(report["checks"].values()))

    def test_each_v2_regression_fails_the_report(self) -> None:
        cases = (
            side(calls=4),
            side(calls=3, polling=True),
            side(calls=3, restart=False),
            side(calls=3, p95=50.0),
        )
        for candidate in cases:
            with self.subTest(candidate=candidate):
                report = BAKEOFF.evaluate(side(calls=12), candidate, True)
                self.assertFalse(report["pass"], report)


if __name__ == "__main__":
    unittest.main()
