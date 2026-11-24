"""Control de las etiquetas de la suite de extracción de memoria.

Un caso mal etiquetado mide mal sin avisar. Estos tests corrigen los ejemplos de cada
caso con sus propias etiquetas y fijan lo que aprueba un extractor sin modelo.
"""

from __future__ import annotations

import pytest

from app.llm.config import load_llm_config
from evals.cases import MemoryExtractionCase
from evals.matching import has_root, matches
from evals.suites import SUITES
from evals.suites.memory_extraction import (
    PROMPT_EXAMPLE_ROOTS,
)
from evals.textcheck import plain
from tests.evals.memory_helpers import (
    CASES,
    NOOP_OP,
    add_op,
    as_op,
    bucket_for,
    ops_json,
    pref_op,
    verdict_for,
)

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
    assert len(CASES) >= 200
    with_facts = [case for case in CASES if case.expect.facts]
    assert len(with_facts) >= 100


def test_cases_use_only_modes_that_consolidate() -> None:
    """El worker solo consolida los turnos de los modos cuyo alias escribe memoria."""
    config = load_llm_config()
    for case in CASES:
        assert config.model_for_mode(case.mode.value).writes_memory, case.id


def test_tags_are_known_and_sin_info_means_nothing_to_save() -> None:
    for case in CASES:
        assert case.tags and set(case.tags) <= _KNOWN_TAGS, case.id
        empty = not case.expect.facts and not case.expect.optional
        if "sin_info" in case.tags:
            assert empty, case.id
        elif empty:
            # Sin el tag, un turno sin nada para guardar es uno con algo que NO se guarda.
            assert case.expect.forbidden, case.id


def test_expected_roots_come_from_the_user_message() -> None:
    """Un hecho esperado u opcional lo dijo el usuario: su mensaje lo cumple."""
    for case in CASES:
        for matcher in [*case.expect.facts, *case.expect.optional]:
            assert matches(matcher, plain(case.user)), (case.id, matcher)


def test_forbidden_without_a_reason_is_what_only_the_assistant_said() -> None:
    """Sin ``flag``, lo prohibido está en la respuesta y no en el mensaje del usuario.

    Con ``flag`` es al revés: algo que el usuario dijo y que igual no se guarda.
    """
    for case in CASES:
        for matcher in case.expect.forbidden:
            if matcher.flag is None:
                assert matches(matcher, plain(case.assistant)), (case.id, matcher)
                assert not matches(matcher, plain(case.user)), (case.id, matcher)
            else:
                assert matches(matcher, plain(case.user)), (case.id, matcher)


def test_no_case_mentions_the_prompt_example() -> None:
    """Si un turno nombrara un dato del ejemplo del prompt, copiarlo no se notaría."""
    for case in CASES:
        turn = plain(f"{case.user} {case.assistant}")
        assert not any(has_root(root, turn) for root in PROMPT_EXAMPLE_ROOTS), case.id


def test_cases_that_can_go_wrong_carry_a_wrong_example() -> None:
    for case in CASES:
        delicate = case.expect.forbidden or any(m.flag for m in case.expect.optional)
        assert bool(case.wrong) or not delicate, case.id
        assert bool(case.ideal) == bool(case.expect.facts), case.id


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
async def test_the_ideal_extraction_passes_and_each_wrong_one_does_not(
    case: MemoryExtractionCase,
) -> None:
    """Las etiquetas reconocen una extracción correcta y no dejan pasar una mala."""
    ideal = [as_op(write) for write in case.ideal] or [NOOP_OP]
    verdict = await verdict_for(case, ops_json(*ideal))
    assert verdict.bucket == "pass", verdict.details
    for wrong in case.wrong:
        assert await bucket_for(case, *ideal, add_op(wrong)) != "pass", wrong


async def test_trivial_extractors_do_not_pass() -> None:
    """Líneas base sin modelo: pegar el mensaje, pegar la respuesta y no guardar nada.

    Pegar una parte del turno no aprueba ningún caso. No guardar nada aprueba solo los
    turnos donde eso es lo correcto: los que no tienen un hecho para guardar.
    """
    for case in CASES:
        assert await bucket_for(case, add_op(case.user)) != "pass", case.id
        assert await bucket_for(case, add_op(case.assistant)) != "pass", case.id
        expected = "fail" if case.expect.facts else "pass"
        assert await bucket_for(case, NOOP_OP) == expected, case.id


async def test_keeping_only_acknowledged_replies_does_not_beat_saving_nothing() -> None:
    """Guardar la respuesta cuando empieza con «Anotado» y, si no, nada: sin modelo."""
    acknowledged = ("anotado", "guardado", "corregido", "hecho", "listo", "lo recuerdo")
    passed = 0
    for case in CASES:
        keep = plain(case.assistant).startswith(acknowledged)
        op = add_op(case.assistant) if keep else NOOP_OP
        passed += await bucket_for(case, op) == "pass"
    assert passed <= sum(1 for case in CASES if not case.expect.facts)


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
    case = next(case for case in CASES if case.id == case_id)
    verdict = await verdict_for(case, ops_json(*(add_op(fact) for fact in _ATOMIC[case_id])))
    assert verdict.bucket == "pass", verdict.details


async def test_the_key_the_prompt_teaches_is_not_a_copy() -> None:
    """``idioma_preferido`` es la key del ejemplo del prompt: usarla bien aprueba."""
    by_id = {case.id: case for case in CASES}
    trato = pref_op("idioma_preferido", trato="vos")
    variante = pref_op("idioma_preferido", idioma="español", variante="rioplatense")
    assert "usted" in by_id["mx-106"].user
    assert "rioplatense" in by_id["mx-116"].user
    assert await bucket_for(by_id["mx-106"], trato) == "pass"
    assert await bucket_for(by_id["mx-116"], variante) == "pass"
