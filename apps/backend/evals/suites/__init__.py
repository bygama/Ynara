"""Suites disponibles, por nombre."""

from __future__ import annotations

from evals.harness import Suite
from evals.suites.crisis import CrisisSuite
from evals.suites.false_confirmation import FalseConfirmationSuite
from evals.suites.memory_extraction import MemoryExtractionSuite
from evals.suites.tool_calling import ToolCallingSuite

SUITES: dict[str, Suite] = {
    suite.name: suite
    for suite in (
        ToolCallingSuite(),
        FalseConfirmationSuite(),
        MemoryExtractionSuite(),
        CrisisSuite(),
    )
}
