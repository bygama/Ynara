"""Resumen de una corrida, archivo de reporte y comparación lado a lado."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evals.harness import CaseResult

BUCKETS = ("pass", "fail", "review", "error", "truncated")
_BUCKET_LABEL = {
    "pass": "aprobados",
    "fail": "fallados",
    "review": "a revisar",
    "error": "errores de llamada",
    "truncated": "cortados por tope de tokens",
}
# De peor a mejor, para quedarse con el peor intento de un caso corrido varias veces:
# una respuesta mala entre tres es un caso malo.
_WORST_FIRST = ("fail", "error", "truncated", "review", "pass")


def _percentile(values: Sequence[float], percent: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 1)
    return round(statistics.quantiles(values, n=100, method="inclusive")[percent - 1], 1)


def summarize(results: Sequence[CaseResult]) -> dict[str, Any]:
    """Números de una corrida.

    ``pass_rate`` es sobre lo que el modelo llegó a contestar (aprobados, fallados y a
    revisar). Los errores de llamada y los cortes por tope de tokens van aparte: son
    del proveedor o del presupuesto de tokens, no de la calidad de la respuesta.

    ``worst_by_case`` y ``cases_with_flag`` cuentan casos, no llamadas: cada caso con
    su peor intento, y en cuántos casos apareció cada marca en algún intento.
    ``flags_by_tag`` reparte las marcas por tag (por modo, por tipo de caso).
    """
    buckets = Counter(result.verdict.bucket for result in results)
    answered = buckets["pass"] + buckets["fail"] + buckets["review"]
    ok_records = [r.record for r in results if r.record.status == "ok"]
    latencies = [record.latency_ms for record in ok_records]
    by_tag: dict[str, Counter[str]] = {}
    flags_by_tag: dict[str, Counter[str]] = {}
    worst: dict[str, str] = {}
    case_flags: dict[str, set[str]] = {}
    for result in results:
        bucket = result.verdict.bucket
        for tag in result.tags:
            by_tag.setdefault(tag, Counter())[bucket] += 1
            if result.verdict.flags:
                flags_by_tag.setdefault(tag, Counter()).update(result.verdict.flags)
        previous = worst.get(result.case_id, bucket)
        worst[result.case_id] = min(previous, bucket, key=_WORST_FIRST.index)
        case_flags.setdefault(result.case_id, set()).update(result.verdict.flags)
    worst_counts = Counter(worst.values())
    with_flag = Counter(flag for flags in case_flags.values() for flag in flags)
    return {
        "total": len(results),
        "buckets": {name: buckets[name] for name in BUCKETS},
        "pass_rate": round(buckets["pass"] / answered, 4) if answered else None,
        "errors": dict(Counter(r.record.error_type for r in results if r.record.status == "error")),
        "flags": dict(Counter(flag for r in results for flag in r.verdict.flags)),
        "by_tag": {tag: dict(counts) for tag, counts in sorted(by_tag.items())},
        "flags_by_tag": {tag: dict(counts) for tag, counts in sorted(flags_by_tag.items())},
        "worst_by_case": {name: worst_counts[name] for name in BUCKETS},
        "cases_with_flag": dict(sorted(with_flag.items())),
        "models": sorted({record.model_name for record in ok_records if record.model_name}),
        "prompt_tokens": sum(record.prompt_tokens for record in ok_records),
        "completion_tokens": sum(record.completion_tokens for record in ok_records),
        "with_reasoning": sum(1 for record in ok_records if record.has_reasoning),
        "latency_ms_p50": _percentile(latencies, 50),
        "latency_ms_p95": _percentile(latencies, 95),
    }


def write_report(path: Path, *, meta: dict[str, Any], results: Sequence[CaseResult]) -> None:
    """Guarda la corrida entera: resumen más cada respuesta, para revisar a mano."""
    payload = {
        "meta": meta,
        "summary": summarize(results),
        "results": [result.model_dump(mode="json") for result in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def format_summary(meta: dict[str, Any], summary: dict[str, Any]) -> str:
    """Resumen de una corrida en texto, para la terminal."""
    rate = summary["pass_rate"]
    lines = [
        f"Suite {meta.get('suite')} · proveedor {meta.get('provider')} · "
        f"modelos {', '.join(summary['models']) or 'sin dato'}",
        f"Casos corridos: {summary['total']}",
    ]
    lines += [f"  {_BUCKET_LABEL[name]}: {summary['buckets'][name]}" for name in BUCKETS]
    lines.append(
        "Aprobación sobre lo contestado: " + ("sin dato" if rate is None else f"{rate:.1%}")
    )
    if summary["errors"]:
        lines.append(f"Errores por tipo: {summary['errors']}")
    if summary["flags"]:
        lines.append(f"Marcas: {summary['flags']}")
    worst = summary.get("worst_by_case")
    if worst and meta.get("repeat", 1) > 1:
        shown = ", ".join(f"{_BUCKET_LABEL[name]} {worst[name]}" for name in BUCKETS if worst[name])
        lines.append(f"Peor intento de cada caso: {shown}")
        if summary.get("cases_with_flag"):
            lines.append(f"Casos con cada marca en algún intento: {summary['cases_with_flag']}")
    lines.append(
        f"Tokens: {summary['prompt_tokens']} de entrada, {summary['completion_tokens']} de "
        f"salida · con razonamiento: {summary['with_reasoning']}"
    )
    lines.append(
        f"Latencia: p50 {summary['latency_ms_p50']} ms, p95 {summary['latency_ms_p95']} ms"
    )
    return "\n".join(lines)


def load_report(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _case_buckets(report: dict[str, Any]) -> dict[str, list[str]]:
    by_case: dict[str, list[str]] = {}
    for result in report["results"]:
        by_case.setdefault(result["case_id"], []).append(result["verdict"]["bucket"])
    return by_case


def format_comparison(left: dict[str, Any], right: dict[str, Any]) -> str:
    """Dos corridas de la misma suite lado a lado, y los casos donde no coinciden."""
    name_l = str(left["meta"].get("provider"))
    name_r = str(right["meta"].get("provider"))
    sum_l, sum_r = left["summary"], right["summary"]

    def _rate(summary: dict[str, Any]) -> str:
        return "sin dato" if summary["pass_rate"] is None else f"{summary['pass_rate']:.1%}"

    rows: list[tuple[str, str, str]] = [
        ("aprobación sobre lo contestado", _rate(sum_l), _rate(sum_r)),
        *(
            (_BUCKET_LABEL[name], str(sum_l["buckets"][name]), str(sum_r["buckets"][name]))
            for name in BUCKETS
        ),
        ("tokens de entrada", str(sum_l["prompt_tokens"]), str(sum_r["prompt_tokens"])),
        ("tokens de salida", str(sum_l["completion_tokens"]), str(sum_r["completion_tokens"])),
        ("latencia p50 (ms)", str(sum_l["latency_ms_p50"]), str(sum_r["latency_ms_p50"])),
        ("latencia p95 (ms)", str(sum_l["latency_ms_p95"]), str(sum_r["latency_ms_p95"])),
    ]
    flags_l, flags_r = sum_l.get("flags", {}), sum_r.get("flags", {})
    rows += [
        (f"marca {flag}", str(flags_l.get(flag, 0)), str(flags_r.get(flag, 0)))
        for flag in sorted(flags_l.keys() | flags_r.keys())
    ]
    width = max(len(label) for label, _, _ in rows)
    lines = [
        f"Suite {left['meta'].get('suite')}",
        f"{'':<{width}}  {name_l:>14}  {name_r:>14}",
        *(f"{label:<{width}}  {a:>14}  {b:>14}" for label, a, b in rows),
    ]
    cases_l, cases_r = _case_buckets(left), _case_buckets(right)
    differing = sorted(
        case_id
        for case_id in cases_l.keys() & cases_r.keys()
        if sorted(cases_l[case_id]) != sorted(cases_r[case_id])
    )
    lines.append(f"Casos con distinto resultado: {len(differing)}")
    lines += [
        f"  {case_id}: {name_l}={'/'.join(cases_l[case_id])} {name_r}={'/'.join(cases_r[case_id])}"
        for case_id in differing
    ]
    only = sorted(cases_l.keys() ^ cases_r.keys())
    if only:
        lines.append(f"Casos que están en una sola corrida: {len(only)}")
    return "\n".join(lines)
