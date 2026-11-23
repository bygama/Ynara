"""Línea de comandos del runner. Se corre desde ``apps/backend``::

    uv run python -m evals list
    uv run python -m evals run --suite tool_calling --provider principal
    uv run python -m evals compare evals/out/a.json evals/out/b.json

``run`` llama a un proveedor real con la configuración de tu ``.env``: gasta tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.llm.config import load_llm_config
from evals.client import EvalConfigError, build_eval_client
from evals.harness import CaseResult, run_suite
from evals.report import format_comparison, format_summary, load_report, summarize, write_report
from evals.suites import SUITES

OUT_DIR = Path(__file__).resolve().parent / "out"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="suites disponibles y cuántos casos tiene cada una")

    run = commands.add_parser("run", help="corre una suite contra un proveedor")
    run.add_argument("--suite", required=True, choices=sorted(SUITES))
    run.add_argument("--provider", required=True, help="name del proveedor en LLM_PROVIDERS")
    run.add_argument("--limit", type=int, help="corre solo los primeros N casos")
    run.add_argument("--repeat", type=int, default=1, help="veces que se corre cada caso")
    run.add_argument("--concurrency", type=int, default=4, help="llamadas simultáneas")
    run.add_argument("--out", type=Path, help="archivo del reporte (default: evals/out/)")

    compare = commands.add_parser("compare", help="dos reportes de la misma suite, lado a lado")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    return parser


def _list() -> int:
    for name, suite in sorted(SUITES.items()):
        print(f"{name}: {len(suite.load())} casos. {suite.description}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    suite = SUITES[args.suite]
    cases = suite.load()[: args.limit] if args.limit else suite.load()
    config = load_llm_config()
    try:
        client = build_eval_client(get_settings(), config, args.provider)
    except EvalConfigError as exc:
        print(f"No se puede correr: {exc}", file=sys.stderr)
        return 2

    done = 0
    total = len(cases) * args.repeat

    def _progress(_result: CaseResult) -> None:
        nonlocal done
        done += 1
        print(f"\r{done}/{total}", end="", file=sys.stderr, flush=True)

    started = datetime.now(UTC)
    try:
        results = await run_suite(
            client,
            suite,
            cases,
            repeat=args.repeat,
            concurrency=args.concurrency,
            config=config,
            on_result=_progress,
        )
    finally:
        closer = getattr(client, "aclose", None)
        if closer is not None:
            await closer()
    print(file=sys.stderr)

    meta: dict[str, Any] = {
        "suite": suite.name,
        "provider": args.provider,
        "repeat": args.repeat,
        "cases": len(cases),
        "started_at": started.isoformat(timespec="seconds"),
    }
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    out = args.out or OUT_DIR / f"{suite.name}-{args.provider}-{stamp}.json"
    write_report(out, meta=meta, results=results)
    print(format_summary(meta, summarize(results)))
    print(f"Reporte: {out}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "list":
        return _list()
    if args.command == "compare":
        print(format_comparison(load_report(args.left), load_report(args.right)))
        return 0
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
