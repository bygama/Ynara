"""Suite de extracción de memoria: la llamada al motor y el corrector.

Todo corre con ``FakeLlmClient`` o con un espía: nunca se llama a un proveedor real.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.constants import MODEL_ALIAS_AGENT
from app.llm.clients.fakes import FakeLlmClient
from app.llm.errors import LlmAuthError, LlmRateLimitError, ModelNotServedError
from app.llm.schemas import CompletionResult
from evals.cases import FactMatcher, MemoryExtractionCase
from evals.harness import CallRecord, run_suite
from evals.matching import has_root, is_copy
from evals.report import summarize
from evals.suites.memory_extraction import (
    DELETE_FLAG,
    DROPPED_OPS_FLAG,
    DUPLICATE_FLAG,
    INVALID_FORMAT_FLAG,
    INVALID_OPS_FLAG,
    LENGTH_CAP_FLAG,
    MESSAGE_COPY_FLAG,
    NO_CONTEXT_FLAG,
    NOOP_WITHOUT_LAYER_FLAG,
    OTHER_LAYER_FLAG,
    OVERWRITTEN_KEY_FLAG,
    PROMPT_COPY_FLAG,
    REPLY_COPY_FLAG,
    SEVERAL_FACTS_FLAG,
    UPDATE_WITHOUT_TARGET_FLAG,
    extract,
    raw_items,
)
from evals.textcheck import plain
from tests.evals.memory_helpers import (
    NOOP_OP,
    SUITE,
    add_op,
    bucket_for,
    make_case,
    make_result,
    ops_json,
    pref_op,
    verdict_for,
)

# ---------------------------------------------------------------------------
# La llamada: el mismo motor que usa el worker
# ---------------------------------------------------------------------------


class _Spy:
    def __init__(self, text: str) -> None:
        self.calls: list[dict[str, Any]] = []
        self._text = text

    async def complete(self, **kwargs: Any) -> CompletionResult:
        self.calls.append(kwargs)
        return make_result(self._text)


async def test_extract_calls_the_consolidation_engine_as_the_worker_does() -> None:
    case = make_case(facts=[{"contains_all": ["renata"]}])
    spy = _Spy(ops_json(add_op("El usuario se llama Renata."), NOOP_OP))

    record = await extract(spy, case)  # type: ignore[arg-type]

    sent = spy.calls[0]
    # Lo que fija ``AgentMemoryEngine.consolidate``: no es el turno del chat.
    assert sent["model"] == MODEL_ALIAS_AGENT
    assert sent["temperature"] == 0.1
    assert sent["max_tokens"] == 512
    assert sent["thinking"] is False
    assert "tools" not in sent
    assert case.user in sent["messages"][-1].content
    assert case.assistant in sent["messages"][-1].content
    assert record.status == "ok"
    assert record.model_name == "org/agent"
    assert record.prompt_tokens == 400
    assert [op["op"] for op in record.memory_ops] == ["ADD", "NOOP"]
    assert record.memory_writes == [{"layer": "semantic", "content": "El usuario se llama Renata."}]


async def test_what_consolidate_hides_gets_its_own_bucket() -> None:
    """Un error permanente o un JSON cortado no pueden pasar por «nada que guardar»."""
    cases = [make_case() for _ in range(5)]
    fake = FakeLlmClient(models=frozenset({MODEL_ALIAS_AGENT}))
    fake.queue_error(LlmAuthError("credencial rechazada"))
    fake.queue_error(LlmRateLimitError("rate limited"))
    fake.queue_result(make_result("La IA está pausada", finish_reason="degraded"))
    fake.queue_result(
        make_result('[{"op": "ADD", "layer": "semantic", "cont', finish_reason="length")
    )
    fake.queue_result(make_result("No hay nada para guardar en este turno."))

    results = await run_suite(fake, SUITE, cases, concurrency=1)

    assert [r.verdict.bucket for r in results] == ["error", "error", "error", "truncated", "fail"]
    assert [r.record.error_type for r in results[:3]] == [
        "LlmAuthError",
        "LlmRateLimitError",
        "degraded",
    ]
    assert results[4].verdict.flags == [INVALID_FORMAT_FLAG]
    summary = summarize(results)
    assert summary["buckets"] == {"pass": 0, "fail": 1, "review": 0, "error": 3, "truncated": 1}
    assert summary["flags"] == {INVALID_FORMAT_FLAG: 1}


async def test_model_not_served_and_bugs_stop_the_run() -> None:
    """Como en las otras suites: un error que no es del proveedor no se anota, se propaga."""
    for error in (ModelNotServedError(MODEL_ALIAS_AGENT), TypeError("bug del runner")):
        fake = FakeLlmClient(models=frozenset({MODEL_ALIAS_AGENT}))
        fake.queue_error(error)
        with pytest.raises(type(error)):
            await run_suite(fake, SUITE, [make_case()])


async def test_a_complete_answer_at_the_token_cap_is_still_graded() -> None:
    case = make_case(facts=[{"contains_all": ["renata"]}])
    verdict = await verdict_for(
        case, ops_json(add_op("El usuario se llama Renata.")), finish_reason="length"
    )
    assert (verdict.bucket, verdict.flags) == ("pass", [LENGTH_CAP_FLAG])


def test_raw_items_follow_the_engine_cleanup() -> None:
    noop = [{"op": "NOOP", "layer": "semantic"}]
    assert raw_items("[]") == []
    assert raw_items('```json\n[{"op": "NOOP", "layer": "semantic"}]\n```') == noop
    assert raw_items('<think>veamos</think>\n[{"op": "NOOP", "layer": "semantic"}]') == noop
    assert raw_items("") is None
    assert raw_items('{"op": "NOOP", "layer": "semantic"}') is None
    assert raw_items("Acá van las operaciones: []") is None


async def test_prose_cut_at_the_token_cap_is_an_invalid_format() -> None:
    """Cortado sin haber empezado la lista: no era JSON, con tope o sin él."""
    verdict = await verdict_for(
        make_case(), "Veamos el turno: el usuario dice", finish_reason="length"
    )
    assert (verdict.bucket, verdict.flags) == ("fail", [INVALID_FORMAT_FLAG])


async def test_malformed_items_do_not_stop_the_run() -> None:
    """Un ``op`` que no es texto se descarta como cualquier otro ítem inválido."""
    case = make_case(facts=[{"contains_all": ["renata"]}])
    text = ops_json({"op": ["ADD"], "layer": "semantic"}, add_op("El usuario se llama Renata."))
    verdict = await verdict_for(case, text)
    assert (verdict.bucket, verdict.flags) == ("review", [INVALID_OPS_FLAG])


# ---------------------------------------------------------------------------
# El corrector
# ---------------------------------------------------------------------------


async def test_a_turn_with_nothing_to_save() -> None:
    case = make_case()
    assert await bucket_for(case, NOOP_OP) == "pass"
    assert (await verdict_for(case, "[]")).bucket == "pass"
    assert await bucket_for(case, add_op("El usuario saludó.")) == "fail"


async def test_expected_facts() -> None:
    case = make_case(facts=[{"contains_all": ["renata"]}, {"contains_all": ["rosario"]}])
    two_ops = (add_op("El usuario se llama Renata."), add_op("La usuaria vive en ROSARIO."))
    assert await bucket_for(case, *two_ops) == "pass"
    # No guardó nada: es lo único que falla sin lugar a dudas.
    assert await bucket_for(case, NOOP_OP) == "fail"
    # Guardó algo que no se reconoce: puede ser el hecho dicho de otra forma.
    assert await bucket_for(case, add_op("El usuario se llama Renata.")) == "review"
    assert await bucket_for(case, *two_ops, add_op("El usuario es ordenado.")) == "review"
    # Un hecho por op (regla 2 del prompt): juntos aprueban, pero queda la marca.
    together = await verdict_for(
        case, ops_json(add_op("El usuario se llama Renata y vive en Rosario."))
    )
    assert (together.bucket, together.flags) == ("pass", [SEVERAL_FACTS_FLAG])
    twice = await verdict_for(case, ops_json(*two_ops, two_ops[0]))
    assert (twice.bucket, twice.flags) == ("pass", [DUPLICATE_FLAG])


async def test_pasting_the_turn_is_not_extracting() -> None:
    case = MemoryExtractionCase.model_validate(
        {
            "id": "mx-test",
            "user": "Guardá esto: soy enfermera y trabajo de noche en una clínica de Lanús.",
            "assistant": "Anotado: sos enfermera y trabajás de noche en una clínica de Lanús.",
            "expect": {"facts": [{"contains_all": ["enfermer"]}]},
        }
    )
    pasted = await verdict_for(case, ops_json(add_op(case.user)))
    quoted = await verdict_for(case, ops_json(add_op(f"El usuario dijo: «{case.user}»")))
    # Con un par de palabras cambiadas sigue siendo el mensaje, en primera persona.
    reworded = "Según el usuario: soy enfermera y trabajo de noche en una clínica de Lanús"
    tweaked = await verdict_for(case, ops_json(add_op(reworded)))
    for verdict in (pasted, quoted, tweaked):
        assert (verdict.bucket, verdict.flags) == ("review", [MESSAGE_COPY_FLAG])
    reply = await verdict_for(case, ops_json(add_op(case.assistant)))
    assert (reply.bucket, reply.flags) == ("review", [REPLY_COPY_FLAG])
    # El mismo hecho en tercera persona no es una copia, aunque comparta casi todo.
    extracted = "La usuaria es enfermera y trabaja de noche en una clínica de Lanús."
    assert await bucket_for(case, add_op(extracted)) == "pass"
    assert not is_copy(plain(extracted), case.user)


def test_a_one_word_reply_is_not_found_inside_other_words() -> None:
    assert is_copy(plain("Hecho."), "Hecho.")
    assert not is_copy(plain("El usuario pidió guardar tres hechos."), "Hecho.")


async def test_loose_words_are_not_a_fact() -> None:
    case = make_case(facts=[{"contains_all": ["renata"]}, {"contains_all": ["rosario"]}])
    verdict = await verdict_for(case, ops_json(add_op("Renata"), add_op("Rosario")))
    assert (verdict.bucket, verdict.flags) == ("review", [NO_CONTEXT_FLAG])


async def test_forbidden_content_fails_even_with_the_fact_saved() -> None:
    case = make_case(
        facts=[{"contains_all": ["renata"]}],
        forbidden=[{"contains_any": ["yoga", "meditacion"]}],
    )
    saved = add_op("El usuario se llama Renata.")
    assert await bucket_for(case, saved) == "pass"
    assert await bucket_for(case, saved, add_op("Al usuario le conviene la meditación.")) == "fail"


async def test_a_forbidden_fact_reports_why() -> None:
    case = make_case(forbidden=[{"contains_all": ["arritmia"], "flag": "dato_sensible"}])
    verdict = await verdict_for(case, ops_json(add_op("El usuario tiene arritmia.")))
    assert (verdict.bucket, verdict.flags) == ("fail", ["dato_sensible"])


async def test_optional_facts() -> None:
    plain_optional = make_case(
        facts=[{"contains_all": ["renata"]}], optional=[{"contains_all": ["rosario"]}]
    )
    name = add_op("El usuario se llama Renata.")
    assert await bucket_for(plain_optional, name) == "pass"
    assert await bucket_for(plain_optional, name, add_op("El usuario vive en Rosario.")) == "pass"

    # Con marca, guardarlo no aprueba ni falla: lo mira una persona.
    delicate = make_case(optional=[{"contains_all": ["rosario"], "flag": "hipotesis"}])
    assert await bucket_for(delicate, NOOP_OP) == "pass"
    verdict = await verdict_for(delicate, ops_json(add_op("El usuario piensa mudarse a Rosario.")))
    assert (verdict.bucket, verdict.flags) == ("review", ["hipotesis"])
    assert await bucket_for(delicate, add_op("El usuario juega al tenis.")) == "review"

    # Si la misma escritura trae un hecho esperado, lo delicado es parte del hecho:
    # aprueba, pero la marca queda para contarlo.
    both = make_case(
        facts=[{"contains_all": ["rosario"]}],
        optional=[{"contains_all": ["cordoba"], "flag": "desactualizado"}],
    )
    moved = await verdict_for(
        both, ops_json(add_op("El usuario dejó Córdoba y ahora vive en Rosario."))
    )
    assert (moved.bucket, moved.flags) == ("pass", ["desactualizado"])
    assert (
        await bucket_for(both, add_op("Vive en Rosario ahora."), add_op("Vive en Córdoba."))
        == "review"
    )


async def test_layer_is_reported_but_does_not_fail() -> None:
    case = make_case(facts=[{"contains_any": ["cort", "brev"], "layer": "procedural"}])
    as_preference = await verdict_for(case, ops_json(pref_op("estilo_respuesta", longitud="breve")))
    as_fact = await verdict_for(case, ops_json(add_op("El usuario prefiere respuestas cortas.")))
    assert (as_preference.bucket, as_preference.flags) == ("pass", [])
    assert (as_fact.bucket, as_fact.flags) == ("pass", [OTHER_LAYER_FLAG])


async def test_deletes_fail_unless_the_turn_asks_to_forget() -> None:
    """Sobre la memoria vacía de la prueba un borrado no hace nada; en producción sí."""
    by_key = {"op": "DELETE", "layer": "procedural", "key": "idioma_preferido"}
    by_id = {"op": "DELETE", "layer": "semantic", "target_id": "no-es-un-uuid"}
    for op in (by_key, by_id):
        verdict = await verdict_for(make_case(), ops_json(op))
        assert (verdict.bucket, verdict.flags) == ("fail", [DELETE_FLAG])
        assert await bucket_for(make_case(may_delete=True), op) == "review"
    assert await bucket_for(make_case(may_delete=True), NOOP_OP) == "pass"


async def test_an_update_has_nothing_to_update() -> None:
    """El extractor no recibe la memoria: un UPDATE semántico no deja nada guardado."""
    case = make_case(facts=[{"contains_all": ["renata"]}])
    update = {
        "op": "UPDATE",
        "layer": "semantic",
        "content": "El usuario se llama Renata.",
        "target_id": "11111111-1111-4111-8111-111111111111",
    }
    alone = await verdict_for(case, ops_json(update))
    assert alone.bucket == "fail"
    assert alone.flags == [UPDATE_WITHOUT_TARGET_FLAG]
    assert "actualizar" in alone.details[0]
    with_add = await verdict_for(case, ops_json(add_op("El usuario se llama Renata."), update))
    assert with_add.bucket == "review"


async def test_ops_that_leave_nothing_never_pass_unnoticed() -> None:
    nothing = make_case()
    invalid = await verdict_for(
        nothing,
        ops_json(
            {"op": "add", "layer": "semantic", "content": "x"}, {"op": "ADD", "layer": "episodic"}
        ),
    )
    assert (invalid.bucket, invalid.flags) == ("review", [INVALID_OPS_FLAG])
    dropped = await verdict_for(
        nothing,
        ops_json(
            {"op": "ADD", "layer": "procedural", "key": "tono", "value": "informal"},
            {"op": "ADD", "layer": "semantic", "content": ""},
        ),
    )
    assert (dropped.bucket, dropped.flags) == ("review", [DROPPED_OPS_FLAG])


async def test_a_noop_without_a_layer_loses_nothing() -> None:
    """El motor lo descarta, pero no había nada que guardar: no cambia el resultado."""
    bare = await verdict_for(make_case(), ops_json({"op": "NOOP"}))
    assert (bare.bucket, bare.flags) == ("pass", [NOOP_WITHOUT_LAYER_FLAG])
    case = make_case(facts=[{"contains_all": ["renata"]}])
    with_fact = await verdict_for(
        case, ops_json(add_op("El usuario se llama Renata."), {"op": "NOOP"})
    )
    assert (with_fact.bucket, with_fact.flags) == ("pass", [NOOP_WITHOUT_LAYER_FLAG])


async def test_preferences_with_the_same_key_overwrite_each_other() -> None:
    """Como en el store real: de dos upserts con la misma key queda el último."""
    case = make_case(
        facts=[
            {"contains_any": ["cort", "brev"], "layer": "procedural"},
            {"contains_all": ["emoji"], "layer": "procedural"},
        ]
    )
    same_key = ops_json(
        pref_op("preferencias", longitud="corta"), pref_op("preferencias", emojis=False)
    )
    fake = FakeLlmClient(models=frozenset({MODEL_ALIAS_AGENT}))
    fake.queue_result(make_result(same_key))
    record = await extract(fake, case)
    assert record.memory_writes == [
        {"layer": "procedural", "key": "preferencias", "value": {"emojis": False}}
    ]
    verdict = SUITE.grade(case, record)
    assert (verdict.bucket, verdict.flags) == ("review", [OVERWRITTEN_KEY_FLAG])
    own_keys = (pref_op("largo_respuesta", longitud="corta"), pref_op("emojis", usar=False))
    assert await bucket_for(case, *own_keys) == "pass"


async def test_copying_the_prompt_example_fails() -> None:
    case = make_case(facts=[{"contains_all": ["renata"]}])
    saved = add_op("El usuario se llama Renata.")
    copied = await verdict_for(
        case, ops_json(saved, add_op("El usuario trabaja como medico pediatra."))
    )
    assert (copied.bucket, copied.flags) == ("fail", [PROMPT_COPY_FLAG])
    # También pegado a un hecho esperado.
    glued = await verdict_for(
        case, ops_json(add_op("El usuario se llama Renata y es médico pediatra."))
    )
    assert (glued.bucket, glued.flags) == ("fail", [PROMPT_COPY_FLAG])
    # La key del ejemplo es la que el prompt enseña: usarla no es copiar.
    other = make_case(facts=[{"contains_any": ["rioplatense"], "layer": "procedural"}])
    assert await bucket_for(other, pref_op("idioma_preferido", variante="rioplatense")) == "pass"


def test_roots_match_word_starts_and_whole_numbers() -> None:
    assert has_root("enfermer", plain("Es enfermera."))
    assert has_root("lucia", plain("Su hermana se llama Lucía."))
    assert has_root("mar del plata", plain("Vive en Mar del Plata."))
    assert has_root("idioma", plain("idioma_preferido"))
    assert not has_root("tilo", plain("estilo_respuesta"))
    assert not has_root("la uba", plain("Viajó a Cuba."))
    assert has_root("29", plain("Tiene 29 años."))
    assert not has_root("29", plain("Viajó en 2029."))
    assert has_root("123.456.789", plain("DNI 123.456.789"))
    # Un número con la unidad pegada es el mismo número.
    assert has_root("17", plain("Sale a las 17hs."))
    assert has_root("45", plain("Reuniones de 45min."))
    assert not has_root("45", plain("Son 450 metros."))


def test_roots_are_read_inside_keys_and_values() -> None:
    case = make_case(facts=[{"contains_all": ["lucia"]}, {"contains_any": ["rioplatense"]}])
    record = CallRecord(
        status="ok",
        text="[1, 2]",
        memory_ops=[{"op": "ADD"}, {"op": "ADD"}],
        memory_writes=[
            {"layer": "semantic", "content": "La hermana del usuario se llama Lucía."},
            {"layer": "procedural", "key": "variante_idioma", "value": {"v": "Rioplatense"}},
        ],
    )
    assert SUITE.grade(case, record).bucket == "pass"


def test_a_matcher_needs_roots_that_are_not_too_short() -> None:
    for bad in ({}, {"contains_all": ["uba"]}, {"contains_any": ["renata", "7"]}):
        with pytest.raises(ValueError, match="raíz"):
            FactMatcher(**bad)
    FactMatcher(contains_any=["29", "a las 7", "10k"])
