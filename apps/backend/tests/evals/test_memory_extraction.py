"""Suite de extracción de memoria: llamada al motor, corrector y control de etiquetas.

Todo corre con ``FakeLlmClient`` o con un espía: nunca se llama a un proveedor real.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.constants import MODEL_ALIAS_AGENT
from app.llm.clients.fakes import FakeLlmClient
from app.llm.config import load_llm_config
from app.llm.errors import LlmAuthError, LlmRateLimitError, ModelNotServedError
from app.llm.schemas import CompletionResult
from evals.cases import FactMatcher, MemoryExtractionCase
from evals.harness import CallRecord, Verdict, grade_record, run_suite
from evals.report import summarize
from evals.suites import SUITES
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
    PROMPT_EXAMPLE_ROOTS,
    REPLY_COPY_FLAG,
    SEVERAL_FACTS_FLAG,
    UPDATE_WITHOUT_TARGET_FLAG,
    MemoryExtractionSuite,
    extract,
    has_root,
    is_copy,
    matches,
    raw_items,
)
from evals.textcheck import plain

_SUITE = MemoryExtractionSuite()
_CASES = _SUITE.load()
_NOOP_OP = {"op": "NOOP", "layer": "semantic"}


def _result(text: str, *, finish_reason: str = "stop") -> CompletionResult:
    return CompletionResult(
        text=text,
        finish_reason=finish_reason,
        prompt_tokens=400,
        completion_tokens=30,
        model_name="org/agent",
        latency_ms=180.0,
    )


def _ops(*ops: dict[str, Any]) -> str:
    return json.dumps(list(ops), ensure_ascii=False)


def _add(content: str) -> dict[str, Any]:
    return {"op": "ADD", "layer": "semantic", "content": content}


def _pref(key: str, **value: Any) -> dict[str, Any]:
    return {"op": "ADD", "layer": "procedural", "key": key, "value": value}


def _as_op(write: str | dict[str, Any]) -> dict[str, Any]:
    """Un ejemplo de ``ideal`` / ``wrong`` como op: texto = hecho, objeto = preferencia."""
    if isinstance(write, str):
        return _add(write)
    return {"op": "ADD", "layer": "procedural", **write}


def _case(**expect: Any) -> MemoryExtractionCase:
    return MemoryExtractionCase.model_validate(
        {
            "id": "mx-test",
            "user": "Me llamo Renata y vivo en Rosario.",
            "assistant": "Dale, Renata.",
            "expect": expect,
        }
    )


async def _verdict(case: MemoryExtractionCase, text: str, **kwargs: Any) -> Verdict:
    fake = FakeLlmClient(models=frozenset({MODEL_ALIAS_AGENT}))
    fake.queue_result(_result(text, **kwargs))
    return grade_record(_SUITE, case, await extract(fake, case))


async def _bucket(case: MemoryExtractionCase, *ops: dict[str, Any]) -> str:
    return (await _verdict(case, _ops(*ops))).bucket


# ---------------------------------------------------------------------------
# La llamada: el mismo motor que usa el worker
# ---------------------------------------------------------------------------


class _Spy:
    def __init__(self, text: str) -> None:
        self.calls: list[dict[str, Any]] = []
        self._text = text

    async def complete(self, **kwargs: Any) -> CompletionResult:
        self.calls.append(kwargs)
        return _result(self._text)


async def test_extract_calls_the_consolidation_engine_as_the_worker_does() -> None:
    case = _case(facts=[{"contains_all": ["renata"]}])
    spy = _Spy(_ops(_add("El usuario se llama Renata."), _NOOP_OP))

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
    cases = [_case() for _ in range(5)]
    fake = FakeLlmClient(models=frozenset({MODEL_ALIAS_AGENT}))
    fake.queue_error(LlmAuthError("credencial rechazada"))
    fake.queue_error(LlmRateLimitError("rate limited"))
    fake.queue_result(_result("La IA está pausada", finish_reason="degraded"))
    fake.queue_result(_result('[{"op": "ADD", "layer": "semantic", "cont', finish_reason="length"))
    fake.queue_result(_result("No hay nada para guardar en este turno."))

    results = await run_suite(fake, _SUITE, cases, concurrency=1)

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
            await run_suite(fake, _SUITE, [_case()])


async def test_a_complete_answer_at_the_token_cap_is_still_graded() -> None:
    case = _case(facts=[{"contains_all": ["renata"]}])
    verdict = await _verdict(
        case, _ops(_add("El usuario se llama Renata.")), finish_reason="length"
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
    verdict = await _verdict(_case(), "Veamos el turno: el usuario dice", finish_reason="length")
    assert (verdict.bucket, verdict.flags) == ("fail", [INVALID_FORMAT_FLAG])


async def test_malformed_items_do_not_stop_the_run() -> None:
    """Un ``op`` que no es texto se descarta como cualquier otro ítem inválido."""
    case = _case(facts=[{"contains_all": ["renata"]}])
    text = _ops({"op": ["ADD"], "layer": "semantic"}, _add("El usuario se llama Renata."))
    verdict = await _verdict(case, text)
    assert (verdict.bucket, verdict.flags) == ("review", [INVALID_OPS_FLAG])


# ---------------------------------------------------------------------------
# El corrector
# ---------------------------------------------------------------------------


async def test_a_turn_with_nothing_to_save() -> None:
    case = _case()
    assert await _bucket(case, _NOOP_OP) == "pass"
    assert (await _verdict(case, "[]")).bucket == "pass"
    assert await _bucket(case, _add("El usuario saludó.")) == "fail"


async def test_expected_facts() -> None:
    case = _case(facts=[{"contains_all": ["renata"]}, {"contains_all": ["rosario"]}])
    two_ops = (_add("El usuario se llama Renata."), _add("La usuaria vive en ROSARIO."))
    assert await _bucket(case, *two_ops) == "pass"
    # No guardó nada: es lo único que falla sin lugar a dudas.
    assert await _bucket(case, _NOOP_OP) == "fail"
    # Guardó algo que no se reconoce: puede ser el hecho dicho de otra forma.
    assert await _bucket(case, _add("El usuario se llama Renata.")) == "review"
    assert await _bucket(case, *two_ops, _add("El usuario es ordenado.")) == "review"
    # Un hecho por op (regla 2 del prompt): juntos aprueban, pero queda la marca.
    together = await _verdict(case, _ops(_add("El usuario se llama Renata y vive en Rosario.")))
    assert (together.bucket, together.flags) == ("pass", [SEVERAL_FACTS_FLAG])
    twice = await _verdict(case, _ops(*two_ops, two_ops[0]))
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
    pasted = await _verdict(case, _ops(_add(case.user)))
    quoted = await _verdict(case, _ops(_add(f"El usuario dijo: «{case.user}»")))
    # Con un par de palabras cambiadas sigue siendo el mensaje, en primera persona.
    reworded = "Según el usuario: soy enfermera y trabajo de noche en una clínica de Lanús"
    tweaked = await _verdict(case, _ops(_add(reworded)))
    for verdict in (pasted, quoted, tweaked):
        assert (verdict.bucket, verdict.flags) == ("review", [MESSAGE_COPY_FLAG])
    reply = await _verdict(case, _ops(_add(case.assistant)))
    assert (reply.bucket, reply.flags) == ("review", [REPLY_COPY_FLAG])
    # El mismo hecho en tercera persona no es una copia, aunque comparta casi todo.
    extracted = "La usuaria es enfermera y trabaja de noche en una clínica de Lanús."
    assert await _bucket(case, _add(extracted)) == "pass"
    assert not is_copy(plain(extracted), case.user)


def test_a_one_word_reply_is_not_found_inside_other_words() -> None:
    assert is_copy(plain("Hecho."), "Hecho.")
    assert not is_copy(plain("El usuario pidió guardar tres hechos."), "Hecho.")


async def test_loose_words_are_not_a_fact() -> None:
    case = _case(facts=[{"contains_all": ["renata"]}, {"contains_all": ["rosario"]}])
    verdict = await _verdict(case, _ops(_add("Renata"), _add("Rosario")))
    assert (verdict.bucket, verdict.flags) == ("review", [NO_CONTEXT_FLAG])


async def test_forbidden_content_fails_even_with_the_fact_saved() -> None:
    case = _case(
        facts=[{"contains_all": ["renata"]}],
        forbidden=[{"contains_any": ["yoga", "meditacion"]}],
    )
    saved = _add("El usuario se llama Renata.")
    assert await _bucket(case, saved) == "pass"
    assert await _bucket(case, saved, _add("Al usuario le conviene la meditación.")) == "fail"


async def test_a_forbidden_fact_reports_why() -> None:
    case = _case(forbidden=[{"contains_all": ["arritmia"], "flag": "dato_sensible"}])
    verdict = await _verdict(case, _ops(_add("El usuario tiene arritmia.")))
    assert (verdict.bucket, verdict.flags) == ("fail", ["dato_sensible"])


async def test_optional_facts() -> None:
    plain_optional = _case(
        facts=[{"contains_all": ["renata"]}], optional=[{"contains_all": ["rosario"]}]
    )
    name = _add("El usuario se llama Renata.")
    assert await _bucket(plain_optional, name) == "pass"
    assert await _bucket(plain_optional, name, _add("El usuario vive en Rosario.")) == "pass"

    # Con marca, guardarlo no aprueba ni falla: lo mira una persona.
    delicate = _case(optional=[{"contains_all": ["rosario"], "flag": "hipotesis"}])
    assert await _bucket(delicate, _NOOP_OP) == "pass"
    verdict = await _verdict(delicate, _ops(_add("El usuario piensa mudarse a Rosario.")))
    assert (verdict.bucket, verdict.flags) == ("review", ["hipotesis"])
    assert await _bucket(delicate, _add("El usuario juega al tenis.")) == "review"

    # Si la misma escritura trae un hecho esperado, lo delicado es parte del hecho:
    # aprueba, pero la marca queda para contarlo.
    both = _case(
        facts=[{"contains_all": ["rosario"]}],
        optional=[{"contains_all": ["cordoba"], "flag": "desactualizado"}],
    )
    moved = await _verdict(both, _ops(_add("El usuario dejó Córdoba y ahora vive en Rosario.")))
    assert (moved.bucket, moved.flags) == ("pass", ["desactualizado"])
    assert await _bucket(both, _add("Vive en Rosario ahora."), _add("Vive en Córdoba.")) == "review"


async def test_layer_is_reported_but_does_not_fail() -> None:
    case = _case(facts=[{"contains_any": ["cort", "brev"], "layer": "procedural"}])
    as_preference = await _verdict(case, _ops(_pref("estilo_respuesta", longitud="breve")))
    as_fact = await _verdict(case, _ops(_add("El usuario prefiere respuestas cortas.")))
    assert (as_preference.bucket, as_preference.flags) == ("pass", [])
    assert (as_fact.bucket, as_fact.flags) == ("pass", [OTHER_LAYER_FLAG])


async def test_deletes_fail_unless_the_turn_asks_to_forget() -> None:
    """Sobre la memoria vacía de la prueba un borrado no hace nada; en producción sí."""
    by_key = {"op": "DELETE", "layer": "procedural", "key": "idioma_preferido"}
    by_id = {"op": "DELETE", "layer": "semantic", "target_id": "no-es-un-uuid"}
    for op in (by_key, by_id):
        verdict = await _verdict(_case(), _ops(op))
        assert (verdict.bucket, verdict.flags) == ("fail", [DELETE_FLAG])
        assert await _bucket(_case(may_delete=True), op) == "review"
    assert await _bucket(_case(may_delete=True), _NOOP_OP) == "pass"


async def test_an_update_has_nothing_to_update() -> None:
    """El extractor no recibe la memoria: un UPDATE semántico no deja nada guardado."""
    case = _case(facts=[{"contains_all": ["renata"]}])
    update = {
        "op": "UPDATE",
        "layer": "semantic",
        "content": "El usuario se llama Renata.",
        "target_id": "11111111-1111-4111-8111-111111111111",
    }
    alone = await _verdict(case, _ops(update))
    assert alone.bucket == "fail"
    assert alone.flags == [UPDATE_WITHOUT_TARGET_FLAG]
    assert "actualizar" in alone.details[0]
    with_add = await _verdict(case, _ops(_add("El usuario se llama Renata."), update))
    assert with_add.bucket == "review"


async def test_ops_that_leave_nothing_never_pass_unnoticed() -> None:
    nothing = _case()
    invalid = await _verdict(
        nothing,
        _ops(
            {"op": "add", "layer": "semantic", "content": "x"}, {"op": "ADD", "layer": "episodic"}
        ),
    )
    assert (invalid.bucket, invalid.flags) == ("review", [INVALID_OPS_FLAG])
    dropped = await _verdict(
        nothing,
        _ops(
            {"op": "ADD", "layer": "procedural", "key": "tono", "value": "informal"},
            {"op": "ADD", "layer": "semantic", "content": ""},
        ),
    )
    assert (dropped.bucket, dropped.flags) == ("review", [DROPPED_OPS_FLAG])


async def test_a_noop_without_a_layer_loses_nothing() -> None:
    """El motor lo descarta, pero no había nada que guardar: no cambia el resultado."""
    bare = await _verdict(_case(), _ops({"op": "NOOP"}))
    assert (bare.bucket, bare.flags) == ("pass", [NOOP_WITHOUT_LAYER_FLAG])
    case = _case(facts=[{"contains_all": ["renata"]}])
    with_fact = await _verdict(case, _ops(_add("El usuario se llama Renata."), {"op": "NOOP"}))
    assert (with_fact.bucket, with_fact.flags) == ("pass", [NOOP_WITHOUT_LAYER_FLAG])


async def test_preferences_with_the_same_key_overwrite_each_other() -> None:
    """Como en el store real: de dos upserts con la misma key queda el último."""
    case = _case(
        facts=[
            {"contains_any": ["cort", "brev"], "layer": "procedural"},
            {"contains_all": ["emoji"], "layer": "procedural"},
        ]
    )
    same_key = _ops(_pref("preferencias", longitud="corta"), _pref("preferencias", emojis=False))
    fake = FakeLlmClient(models=frozenset({MODEL_ALIAS_AGENT}))
    fake.queue_result(_result(same_key))
    record = await extract(fake, case)
    assert record.memory_writes == [
        {"layer": "procedural", "key": "preferencias", "value": {"emojis": False}}
    ]
    verdict = _SUITE.grade(case, record)
    assert (verdict.bucket, verdict.flags) == ("review", [OVERWRITTEN_KEY_FLAG])
    own_keys = (_pref("largo_respuesta", longitud="corta"), _pref("emojis", usar=False))
    assert await _bucket(case, *own_keys) == "pass"


async def test_copying_the_prompt_example_fails() -> None:
    case = _case(facts=[{"contains_all": ["renata"]}])
    saved = _add("El usuario se llama Renata.")
    copied = await _verdict(case, _ops(saved, _add("El usuario trabaja como medico pediatra.")))
    assert (copied.bucket, copied.flags) == ("fail", [PROMPT_COPY_FLAG])
    # También pegado a un hecho esperado.
    glued = await _verdict(case, _ops(_add("El usuario se llama Renata y es médico pediatra.")))
    assert (glued.bucket, glued.flags) == ("fail", [PROMPT_COPY_FLAG])
    # La key del ejemplo es la que el prompt enseña: usarla no es copiar.
    other = _case(facts=[{"contains_any": ["rioplatense"], "layer": "procedural"}])
    assert await _bucket(other, _pref("idioma_preferido", variante="rioplatense")) == "pass"


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
    case = _case(facts=[{"contains_all": ["lucia"]}, {"contains_any": ["rioplatense"]}])
    record = CallRecord(
        status="ok",
        text="[1, 2]",
        memory_ops=[{"op": "ADD"}, {"op": "ADD"}],
        memory_writes=[
            {"layer": "semantic", "content": "La hermana del usuario se llama Lucía."},
            {"layer": "procedural", "key": "variante_idioma", "value": {"v": "Rioplatense"}},
        ],
    )
    assert _SUITE.grade(case, record).bucket == "pass"


def test_a_matcher_needs_roots_that_are_not_too_short() -> None:
    for bad in ({}, {"contains_all": ["uba"]}, {"contains_any": ["renata", "7"]}):
        with pytest.raises(ValueError, match="raíz"):
            FactMatcher(**bad)
    FactMatcher(contains_any=["29", "a las 7", "10k"])


# ---------------------------------------------------------------------------
# Control de las etiquetas
# ---------------------------------------------------------------------------

_KNOWN_TAGS = frozenset(
    {
        "asistente",
        "consentimiento",
        "correccion",
        "efimero",
        "error_del_asistente",
        "estudio",
        "habitos",
        "hipotetico",
        "identidad",
        "inyeccion",
        "largo",
        "literal",
        "lunfardo",
        "mascotas",
        "multiples",
        "negacion",
        "objetivos",
        "preferencias",
        "relaciones",
        "sensible",
        "sin_info",
        "terceros",
        "trabajo",
    }
)


def _ids(cases: list[MemoryExtractionCase]) -> list[str]:
    return [case.id for case in cases]


def test_the_suite_is_registered_and_sized() -> None:
    assert SUITES["memory_extraction"].name == "memory_extraction"
    assert len(_CASES) >= 200
    with_facts = [case for case in _CASES if case.expect.facts]
    assert len(with_facts) >= 100


def test_cases_use_only_modes_that_consolidate() -> None:
    """El worker solo consolida los turnos de los modos cuyo alias escribe memoria."""
    config = load_llm_config()
    for case in _CASES:
        assert config.model_for_mode(case.mode.value).writes_memory, case.id


def test_tags_are_known_and_sin_info_means_nothing_to_save() -> None:
    for case in _CASES:
        assert case.tags and set(case.tags) <= _KNOWN_TAGS, case.id
        empty = not case.expect.facts and not case.expect.optional
        if "sin_info" in case.tags:
            assert empty, case.id
        elif empty:
            # Sin el tag, un turno sin nada para guardar es uno con algo que NO se guarda.
            assert case.expect.forbidden, case.id


def test_expected_roots_come_from_the_user_message() -> None:
    """Un hecho esperado u opcional lo dijo el usuario: su mensaje lo cumple."""
    for case in _CASES:
        for matcher in [*case.expect.facts, *case.expect.optional]:
            assert matches(matcher, plain(case.user)), (case.id, matcher)


def test_forbidden_without_a_reason_is_what_only_the_assistant_said() -> None:
    """Sin ``flag``, lo prohibido está en la respuesta y no en el mensaje del usuario.

    Con ``flag`` es al revés: algo que el usuario dijo y que igual no se guarda.
    """
    for case in _CASES:
        for matcher in case.expect.forbidden:
            if matcher.flag is None:
                assert matches(matcher, plain(case.assistant)), (case.id, matcher)
                assert not matches(matcher, plain(case.user)), (case.id, matcher)
            else:
                assert matches(matcher, plain(case.user)), (case.id, matcher)


def test_no_case_mentions_the_prompt_example() -> None:
    """Si un turno nombrara un dato del ejemplo del prompt, copiarlo no se notaría."""
    for case in _CASES:
        turn = plain(f"{case.user} {case.assistant}")
        assert not any(has_root(root, turn) for root in PROMPT_EXAMPLE_ROOTS), case.id


def test_cases_that_can_go_wrong_carry_a_wrong_example() -> None:
    for case in _CASES:
        delicate = case.expect.forbidden or any(m.flag for m in case.expect.optional)
        assert bool(case.wrong) or not delicate, case.id
        assert bool(case.ideal) == bool(case.expect.facts), case.id


@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
async def test_the_ideal_extraction_passes_and_each_wrong_one_does_not(
    case: MemoryExtractionCase,
) -> None:
    """Las etiquetas reconocen una extracción correcta y no dejan pasar una mala."""
    ideal = [_as_op(write) for write in case.ideal] or [_NOOP_OP]
    verdict = await _verdict(case, _ops(*ideal))
    assert verdict.bucket == "pass", verdict.details
    for wrong in case.wrong:
        assert await _bucket(case, *ideal, _add(wrong)) != "pass", wrong


async def test_trivial_extractors_do_not_pass() -> None:
    """Líneas base sin modelo: pegar el mensaje, pegar la respuesta y no guardar nada.

    Pegar una parte del turno no aprueba ningún caso. No guardar nada aprueba solo los
    turnos donde eso es lo correcto: los que no tienen un hecho para guardar.
    """
    for case in _CASES:
        assert await _bucket(case, _add(case.user)) != "pass", case.id
        assert await _bucket(case, _add(case.assistant)) != "pass", case.id
        expected = "fail" if case.expect.facts else "pass"
        assert await _bucket(case, _NOOP_OP) == expected, case.id


async def test_keeping_only_acknowledged_replies_does_not_beat_saving_nothing() -> None:
    """Guardar la respuesta cuando empieza con «Anotado» y, si no, nada: sin modelo."""
    acknowledged = ("anotado", "guardado", "corregido", "hecho", "listo", "lo recuerdo")
    passed = 0
    for case in _CASES:
        keep = plain(case.assistant).startswith(acknowledged)
        op = _add(case.assistant) if keep else _NOOP_OP
        passed += await _bucket(case, op) == "pass"
    assert passed <= sum(1 for case in _CASES if not case.expect.facts)


# Un hecho por op (regla 2 del prompt de extracción): partir el turno en hechos sueltos
# tiene que aprobar igual que decirlos juntos.
_ATOMIC = {
    "mx-047": [
        "El usuario tiene dos hijos.",
        "Un hijo del usuario se llama Bruno y tiene 8 años.",
        "Una hija del usuario se llama Catalina y tiene 5 años.",
    ],
    "mx-050": ["La usuaria está casada con Marcos.", "La usuaria lleva doce años casada."],
    "mx-052": [
        "El usuario vive con su abuela Elvira.",
        "La abuela del usuario tiene 86 años.",
        "El usuario acompaña a su abuela a los médicos.",
    ],
    "mx-061": [
        "La suegra del usuario se llama Norma.",
        "La suegra del usuario cumple el 9 de mayo.",
    ],
    "mx-063": ["El abuelo del usuario se llamaba Anselmo.", "El abuelo del usuario falleció."],
    "mx-064": [
        "La mentora del usuario en el trabajo es Paula.",
        "El usuario se junta con su mentora una vez por mes.",
    ],
    "mx-092": ["El usuario quiere viajar a Japón.", "El usuario planea un viaje para 2028."],
    "mx-135": ["El usuario es hipertenso.", "El usuario no puede comer con sal."],
    "mx-187": ["El usuario juega al pádel.", "El usuario juega los jueves con amigos del barrio."],
    "mx-238": [
        "Los padres del usuario viven en Chivilcoy.",
        "El usuario visita a sus padres todos los meses.",
    ],
}


@pytest.mark.parametrize("case_id", sorted(_ATOMIC))
async def test_one_fact_per_op_passes(case_id: str) -> None:
    case = next(case for case in _CASES if case.id == case_id)
    verdict = await _verdict(case, _ops(*(_add(fact) for fact in _ATOMIC[case_id])))
    assert verdict.bucket == "pass", verdict.details


async def test_the_key_the_prompt_teaches_is_not_a_copy() -> None:
    """``idioma_preferido`` es la key del ejemplo del prompt: usarla bien aprueba."""
    by_id = {case.id: case for case in _CASES}
    trato = _pref("idioma_preferido", trato="vos")
    variante = _pref("idioma_preferido", idioma="español", variante="rioplatense")
    assert "usted" in by_id["mx-106"].user
    assert "rioplatense" in by_id["mx-116"].user
    assert await _bucket(by_id["mx-106"], trato) == "pass"
    assert await _bucket(by_id["mx-116"], variante) == "pass"
