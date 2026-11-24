"""Suite ``memory_extraction``: qué guarda el extractor de memoria después de un turno.

Llama al mismo motor que usa el worker de consolidación
(``AgentMemoryEngine.consolidate``) y aplica las ops con ``apply_ops`` sobre una memoria
vacía que vive en el proceso: se corrige lo que de verdad habría quedado guardado, no
lo que el modelo quiso decir. No hay base de datos ni embeddings.

``consolidate`` está hecho para que el worker nunca se caiga: un error permanente del
proveedor y un JSON inválido vuelven igual que «no hay nada que guardar». Acá eso
escondería justo lo que se mide, así que el cliente va envuelto y la suite mira la
respuesta cruda.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from app.llm.clients.base import LLMClient
from app.llm.config import LlmRuntimeConfig
from app.llm.errors import LlmError, LlmTransientError, ModelNotServedError
from app.llm.memory_engine import AgentMemoryEngine, _strip_reasoning_and_fences, apply_ops
from app.llm.schemas import CompletionResult
from app.schemas.memory import ProceduralMemoryUpsert, SemanticMemoryCreate
from evals.cases import DATA_DIR, FactMatcher, MemoryExtractionCase, load_cases
from evals.harness import CallRecord, Verdict, record_from_result
from evals.matching import has_root, is_copy, matches, write_text

INVALID_FORMAT_FLAG = "formato_invalido"
INVALID_OPS_FLAG = "ops_invalidas"
NOOP_WITHOUT_LAYER_FLAG = "noop_sin_capa"
DROPPED_OPS_FLAG = "ops_descartadas"
UPDATE_WITHOUT_TARGET_FLAG = "update_sin_destino"
DELETE_FLAG = "borrado"
OVERWRITTEN_KEY_FLAG = "clave_pisada"
LENGTH_CAP_FLAG = "tope_tokens"
DUPLICATE_FLAG = "duplicado"
SEVERAL_FACTS_FLAG = "varios_hechos_por_op"
OTHER_LAYER_FLAG = "otra_capa"
MESSAGE_COPY_FLAG = "copia_del_mensaje"
REPLY_COPY_FLAG = "copia_de_la_respuesta"
PROMPT_COPY_FLAG = "copia_del_prompt"
NO_CONTEXT_FLAG = "sin_contexto"

# Datos del ejemplo que trae el prompt de extracción: ningún turno del set los nombra,
# así que si aparecen guardados el modelo copió el ejemplo.
PROMPT_EXAMPLE_ROOTS = ("pediatra", "carlos")

_SINK_ID = UUID("00000000-0000-4000-8000-00000000e7a1")


class _RecordingClient:
    """Delega en el cliente real y guarda la respuesta o el error de la última llamada."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.result: CompletionResult | None = None
        self.error: Exception | None = None

    async def complete(self, **kwargs: Any) -> CompletionResult:  # kwargs: los de LLMClient
        try:
            self.result = await self._inner.complete(**kwargs)
        except Exception as exc:
            self.error = exc
            raise
        return self.result


class _SemanticSink:
    """Memoria semántica vacía: anota las altas; no hay nada que actualizar ni borrar."""

    def __init__(self, writes: list[dict[str, Any]]) -> None:
        self._writes = writes

    async def add(self, payload: SemanticMemoryCreate) -> SimpleNamespace:
        self._writes.append({"layer": "semantic", "content": payload.content})
        return SimpleNamespace(id=_SINK_ID)

    async def update(self, target_id: UUID, content: str) -> None:
        return None

    async def delete(self, target_id: UUID) -> bool:
        return False


class _ProceduralSink:
    """Memoria procedural vacía. Como el store real, una ``key`` repetida pisa el valor."""

    def __init__(self, writes: list[dict[str, Any]]) -> None:
        self._writes = writes

    async def upsert(self, payload: ProceduralMemoryUpsert) -> SimpleNamespace:
        self._writes[:] = [
            write
            for write in self._writes
            if write["layer"] != "procedural" or write["key"] != payload.key
        ]
        self._writes.append({"layer": "procedural", "key": payload.key, "value": payload.value})
        return SimpleNamespace(id=_SINK_ID)

    async def delete(self, key: str) -> bool:
        return False


async def extract(client: LLMClient, case: MemoryExtractionCase) -> CallRecord:
    """Corre la extracción de un turno y devuelve lo que habría quedado en memoria."""
    recorder = _RecordingClient(client)
    engine = AgentMemoryEngine(recorder)  # type: ignore[arg-type]
    try:
        extraction = await engine.consolidate(
            user_msg=case.user, model_response=case.assistant, mode=case.mode.value
        )
    except LlmTransientError as exc:
        # Una falla transitoria del proveedor, o la respuesta degradada del cliente.
        degraded = recorder.result is not None and recorder.result.finish_reason == "degraded"
        return CallRecord(status="error", error_type="degraded" if degraded else type(exc).__name__)
    # ``consolidate`` se traga todo lo que no es transitorio. Como en ``call_model``: un
    # modelo mal configurado o un bug cortan la corrida, y el resto de los errores del
    # proveedor se anota como error de llamada.
    error = recorder.error
    if error is not None:
        if isinstance(error, ModelNotServedError) or not isinstance(error, LlmError):
            raise error
        return CallRecord(status="error", error_type=type(error).__name__)
    if recorder.result is None:
        return CallRecord(status="error", error_type="sin_respuesta")

    writes: list[dict[str, Any]] = []
    await apply_ops(
        extraction.ops,
        semantic_store=_SemanticSink(writes),
        procedural_store=_ProceduralSink(writes),
    )
    record = record_from_result(recorder.result)
    record.memory_ops = [asdict(op) for op in extraction.ops]
    record.memory_writes = writes
    return record


def raw_items(text: str) -> list[Any] | None:
    """Los ítems de la lista JSON de la respuesta; ``None`` si no es una lista.

    Usa la misma limpieza que el motor, para decir lo mismo que diría el worker. Los
    ítems son JSON del modelo, de forma libre.
    """
    try:
        data = json.loads(_strip_reasoning_and_fences(text))
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def _label(matcher: FactMatcher) -> str:
    return "/".join([*matcher.contains_all, *matcher.contains_any])


def _mechanical_flags(record: CallRecord, raw: list[Any]) -> tuple[list[str], list[str]]:
    """Marcas que no dependen de las etiquetas, y los motivos para que lo mire alguien."""
    parsed = record.memory_ops
    ops = [op for op in parsed if op.get("op") != "NOOP"]
    updates = [op for op in ops if op.get("op") == "UPDATE" and op.get("layer") == "semantic"]
    deletes = [op for op in ops if op.get("op") == "DELETE"]
    upserts = [
        op
        for op in ops
        if op.get("layer") == "procedural"
        and op.get("op") in ("ADD", "UPDATE")
        and op.get("key")
        and isinstance(op.get("value"), dict)
    ]
    overwritten = len(upserts) - len({op["key"] for op in upserts})
    # Un NOOP sin ``layer`` no pasa el parseo del motor, pero tampoco pierde nada.
    raw_noops = sum(1 for item in raw if isinstance(item, dict) and item.get("op") == "NOOP")
    bare_noops = raw_noops - (len(parsed) - len(ops))

    flags: list[str] = []
    reasons: list[str] = []
    if record.finish_reason == "length":
        flags.append(LENGTH_CAP_FLAG)
    if bare_noops:
        flags.append(NOOP_WITHOUT_LAYER_FLAG)
    if len(raw) - len(parsed) > bare_noops:
        # ``_parse_ops`` descartó ítems (op o capa que no existen): el worker ni los ve.
        flags.append(INVALID_OPS_FLAG)
        reasons.append("emitió ítems que el motor descarta")
    if updates:
        # El extractor no recibe la memoria que ya existe: no hay hecho que actualizar.
        flags.append(UPDATE_WITHOUT_TARGET_FLAG)
        reasons.append("pidió actualizar un hecho que no existe")
    if deletes:
        flags.append(DELETE_FLAG)
    if overwritten:
        flags.append(OVERWRITTEN_KEY_FLAG)
        reasons.append("varias preferencias con la misma key: queda solo la última")
    if len(ops) - len(updates) - len(deletes) - overwritten > len(record.memory_writes):
        flags.append(DROPPED_OPS_FLAG)
        reasons.append("emitió ops que no dejaron nada en memoria")
    return flags, reasons


def _review_writes(
    case: MemoryExtractionCase, record: CallRecord, texts: list[str]
) -> tuple[list[str], list[str]]:
    """Marcas y motivos de revisión de cada escritura, según las etiquetas del caso."""
    expect = case.expect
    known = [*expect.facts, *expect.optional]
    flags: list[str] = []
    reasons: list[str] = []
    unknown = 0
    for write, text in zip(record.memory_writes, texts, strict=True):
        fact_count = sum(1 for m in expect.facts if matches(m, text))
        delicate = [m.flag for m in expect.optional if m.flag and matches(m, text)]
        # Lo delicado queda marcado siempre; manda a revisar solo si la escritura no trae
        # además un hecho esperado («dejó el banco y ahora trabaja en…»).
        flags.extend(delicate)
        if fact_count > 1:
            flags.append(SEVERAL_FACTS_FLAG)
        if is_copy(text, case.user):
            flags.append(MESSAGE_COPY_FLAG)
            reasons.append("pegó el mensaje del usuario en vez de extraer el hecho")
        elif is_copy(text, case.assistant, fragments=False):
            flags.append(REPLY_COPY_FLAG)
            reasons.append("pegó la respuesta del asistente en vez de extraer el hecho")
        elif write["layer"] == "semantic" and len(text.split()) < 3:
            flags.append(NO_CONTEXT_FLAG)
            reasons.append("guardó una o dos palabras sueltas")
        if fact_count:
            continue
        if delicate:
            reasons.append(f"guardó algo que el prompt no define: {delicate}")
        elif not any(matches(m, text) for m in known):
            unknown += 1
    if unknown:
        reasons.append(f"{unknown} escritura(s) que las etiquetas no prevén")
    if len(set(texts)) < len(texts):
        # ``apply_ops`` no deduplica: en la base quedarían dos filas iguales.
        flags.append(DUPLICATE_FLAG)
        reasons.append("guardó lo mismo más de una vez")
    return flags, reasons


def grade(case: MemoryExtractionCase, record: CallRecord) -> Verdict:
    """Corrige lo que quedó guardado después de un turno.

    Es conservador, como el corrector de texto: las raíces no entienden el idioma. Falla
    solo lo que no admite otra lectura (formato inválido, contenido prohibido, un
    borrado que nadie pidió, nada guardado cuando había un hecho, algo guardado cuando
    no había nada). Aprueba solo si cada escritura se reconoce como un hecho esperado.
    Lo demás va a revisar.
    """
    raw = raw_items(record.text)
    if raw is None:
        cut_json = _strip_reasoning_and_fences(record.text).startswith("[")
        if record.finish_reason == "length" and cut_json:
            return Verdict(bucket="truncated", details=["tope de tokens: el JSON quedó cortado"])
        return Verdict(
            bucket="fail",
            details=["la respuesta no es una lista JSON: el worker descarta el turno"],
            flags=[INVALID_FORMAT_FLAG],
        )

    expect = case.expect
    flags, reasons = _mechanical_flags(record, raw)
    texts = [write_text(write) for write in record.memory_writes]
    layers = [str(write["layer"]) for write in record.memory_writes]

    def _fail(detail: str, *extra: str) -> Verdict:
        return Verdict(bucket="fail", details=[detail], flags=list(dict.fromkeys([*flags, *extra])))

    hits = [m for m in expect.forbidden if any(matches(m, text) for text in texts)]
    if hits:
        found = [_label(matcher) for matcher in hits]
        return _fail(f"guardó lo que no correspondía: {found}", *(m.flag for m in hits if m.flag))
    copied = [r for r in PROMPT_EXAMPLE_ROOTS if any(has_root(r, text) for text in texts)]
    if copied:
        return _fail(f"guardó un dato del ejemplo del prompt: {copied}", PROMPT_COPY_FLAG)
    if DELETE_FLAG in flags and not expect.may_delete:
        return _fail("pidió borrar memoria en un turno que no lo pide")
    if not expect.facts and not expect.optional and texts:
        return _fail(f"guardó {len(texts)} escritura(s) de un turno sin nada para guardar")
    if expect.facts and not texts:
        if UPDATE_WITHOUT_TARGET_FLAG in flags:
            return _fail("pidió actualizar un hecho que no existe: no queda nada guardado")
        return _fail("no guardó nada")

    write_flags, write_reasons = _review_writes(case, record, texts)
    flags.extend(write_flags)
    reasons.extend(write_reasons)
    missing: list[str] = []
    for matcher in expect.facts:
        found_in = {lay for lay, text in zip(layers, texts, strict=True) if matches(matcher, text)}
        if not found_in:
            missing.append(_label(matcher))
        elif matcher.layer is not None and matcher.layer not in found_in:
            flags.append(OTHER_LAYER_FLAG)
    if missing:
        reasons.append(f"no se reconoce {missing} en lo guardado; puede estar con otras palabras")
    if DELETE_FLAG in flags:
        reasons.append("pidió un borrado: hay que ver si corresponde")

    flags = list(dict.fromkeys(flags))
    if reasons:
        return Verdict(bucket="review", details=list(dict.fromkeys(reasons)), flags=flags)
    return Verdict(bucket="pass", flags=flags)


class MemoryExtractionSuite:
    name = "memory_extraction"
    description = "Turnos terminados: qué hechos y preferencias guarda el extractor de memoria"

    def load(self) -> list[MemoryExtractionCase]:
        return load_cases(DATA_DIR / "memory_extraction.jsonl", MemoryExtractionCase)

    async def call(
        self, client: LLMClient, case: MemoryExtractionCase, config: LlmRuntimeConfig
    ) -> CallRecord:
        return await extract(client, case)

    def grade(self, case: MemoryExtractionCase, record: CallRecord) -> Verdict:
        return grade(case, record)
