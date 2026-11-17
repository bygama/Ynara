"""Tests de regresion de los system prompts por modo (M5).

Sin DB ni red: los prompts son strings estaticos. Verificamos invariantes
clave, no el texto entero palabra por palabra (snapshot liviano):

- ``load_prompt`` devuelve un string no vacio para los 5 modos.
- Cada prompt incluye los fragmentos compartidos de identidad / voz / seguridad,
  con la inferencia contada con honestidad: modelos de proveedores externos y,
  ante preguntas de privacidad, la politica de privacidad.
- Ningun system que ve el modelo promete inferencia local ni que los datos "no
  salen" de un perimetro (ADR-028: la inferencia es por API).
- El protocolo de crisis de Bienestar sigue entero.
- Los modos Gemma (estudio, bienestar, vida) NO mencionan tools ni escritura de
  memoria; los Qwen (productividad, memoria) si habilitan tools.
- ``load_prompt`` con un modo invalido levanta ``ValueError``.
- Coherencia: todos los miembros de ``Mode`` tienen prompt.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.api.v1.admin.playground import _PLAYGROUND_DEFAULT_SYSTEM
from app.enums import Mode
from app.llm.memory_engine import _EXTRACTION_SYSTEM, _SUMMARY_SYSTEM
from app.llm.prompts import load_prompt
from app.llm.prompts.bienestar import SYSTEM_PROMPT as BIENESTAR_PROMPT
from app.llm.prompts.datetime_context import build_now_preamble
from app.llm.prompts.estudio import SYSTEM_PROMPT as ESTUDIO_PROMPT
from app.llm.prompts.loader import _MODE_PROMPTS
from app.llm.prompts.memoria import SYSTEM_PROMPT as MEMORIA_PROMPT
from app.llm.prompts.productividad import SYSTEM_PROMPT as PRODUCTIVIDAD_PROMPT
from app.llm.prompts.shared import (
    IDENTITY_FRAGMENT,
    SAFETY_FRAGMENT,
    VOICE_FRAGMENT,
)
from app.llm.prompts.vida import SYSTEM_PROMPT as VIDA_PROMPT
from app.workflows.agent_pass import _AGENT_PASS_SYSTEM_BODY

# Modos conversacionales Gemma: solo leen memoria, no escriben, no llaman tools.
_GEMMA_MODES = (Mode.ESTUDIO, Mode.BIENESTAR, Mode.VIDA)
# Modos agente Qwen: leen y escriben memoria, llaman tools.
_QWEN_MODES = (Mode.PRODUCTIVIDAD, Mode.MEMORIA)

# SYSTEM_PROMPT de modo (sin los fragmentos compartidos) para asserts de modo.
_MODE_ONLY_PROMPTS = {
    Mode.PRODUCTIVIDAD: PRODUCTIVIDAD_PROMPT,
    Mode.ESTUDIO: ESTUDIO_PROMPT,
    Mode.BIENESTAR: BIENESTAR_PROMPT,
    Mode.VIDA: VIDA_PROMPT,
    Mode.MEMORIA: MEMORIA_PROMPT,
}

# Promesas de inferencia local que dejaron de ser ciertas con la inferencia por API
# (ADR-028): datos que "no salen" de un perimetro, on-prem, servidor, infraestructura
# o modelo propios. "local" suelto no se prohibe: Bienestar habla de "lineas de ayuda
# locales" y el preambulo de fecha, de "hora local".
_ON_PREM_PROMISE = re.compile(
    r"per[ií]metro|on[- ]?prem|no\s+sal(?:e|en)\s+de|en\s+tu\s+servidor"
    r"|servidor(?:es)?\s+propios?|infraestructura\s+propia|modelos?\s+propios?"
    r"|localmente|wrapper",
    re.IGNORECASE,
)

# Todo system que ve el modelo: los prompts de modo ensamblados, la pasada del agente,
# la extraccion y el resumen de memoria, el default del playground del panel y el
# preambulo de fecha/hora.
_MODEL_FACING_SYSTEMS = [
    *(pytest.param(load_prompt(mode), id=f"modo-{mode.value}") for mode in Mode),
    pytest.param(_AGENT_PASS_SYSTEM_BODY, id="pasada-agente"),
    pytest.param(_EXTRACTION_SYSTEM, id="extraccion-memoria"),
    pytest.param(_SUMMARY_SYSTEM, id="resumen-episodico"),
    pytest.param(_PLAYGROUND_DEFAULT_SYSTEM, id="playground-panel"),
    pytest.param(
        build_now_preamble(datetime(2026, 7, 21, 18, 30, tzinfo=timezone(timedelta(hours=-3)))),
        id="preambulo-fecha",
    ),
]

# Protocolo de crisis de Bienestar, textual (whitespace normalizado): la redaccion de
# los fragmentos compartidos no puede debilitarlo.
_BIENESTAR_CRISIS_PROTOCOL = (
    "Protocolo de crisis: si el usuario menciona ideación suicida, autolesión o una "
    "crisis aguda, le acercás información de líneas de ayuda locales y te corrés. "
    "Nunca te ofrecés como reemplazo de un profesional."
)


@pytest.mark.parametrize("mode", list(Mode))
def test_load_prompt_returns_non_empty_string(mode: Mode) -> None:
    prompt = load_prompt(mode)
    assert isinstance(prompt, str)
    assert prompt.strip()


@pytest.mark.parametrize("mode", list(Mode))
def test_prompt_includes_shared_fragments(mode: Mode) -> None:
    """Todo prompt antepone identidad + voz + seguridad."""
    prompt = load_prompt(mode)
    assert IDENTITY_FRAGMENT in prompt
    assert VOICE_FRAGMENT in prompt
    assert SAFETY_FRAGMENT in prompt


@pytest.mark.parametrize("mode", list(Mode))
def test_prompt_carries_shared_invariants(mode: Mode) -> None:
    """Invariantes de voz del producto presentes en cada prompt ensamblado."""
    prompt = load_prompt(mode).lower()
    # El wrap de linea parte las frases: las de varias palabras se buscan normalizadas.
    normalized = " ".join(prompt.split())
    # Identidad: marca y al menos un pilar nombrado.
    assert "ynara" in prompt
    assert "productividad" in prompt
    assert "memoria" in prompt
    # Voz: rioplatense / voseo, sin moralizar.
    assert "rioplatense" in prompt
    assert "voseo" in prompt
    # Honestidad: modelos de proveedores externos, la privacidad remite a la politica
    # de privacidad y, ante la falta de info, decirlo antes que inventar.
    assert "modelos de lenguaje de proveedores externos" in normalized
    assert "política de privacidad" in normalized
    assert "inventar" in prompt


@pytest.mark.parametrize("system", _MODEL_FACING_SYSTEMS)
def test_system_prompts_make_no_on_prem_promises(system: str) -> None:
    """Ningun system promete inferencia local ni que los datos no salen de un perimetro."""
    assert _ON_PREM_PROMISE.findall(system) == []


@pytest.mark.parametrize(
    "promise",
    [
        "Los datos del usuario son suyos y no salen del perímetro.",
        "La inferencia corre on-prem.",
        "Tus mensajes no salen de este equipo.",
        "Todo corre en tu servidor.",
        "No sos un wrapper de otro modelo.",
        "Es modelo propio sobre infraestructura propia.",
    ],
)
def test_on_prem_promise_pattern_catches_old_wording(promise: str) -> None:
    """Control del patron: atrapa la redaccion de antes de la inferencia por API."""
    assert _ON_PREM_PROMISE.search(promise) is not None


def test_bienestar_keeps_crisis_protocol() -> None:
    """El protocolo de crisis de Bienestar llega entero al prompt ensamblado."""
    normalized = " ".join(load_prompt(Mode.BIENESTAR).split())
    assert _BIENESTAR_CRISIS_PROTOCOL in normalized


def test_every_mode_has_a_prompt() -> None:
    """Coherencia: el mapa del loader cubre todos los miembros de ``Mode``."""
    assert set(_MODE_PROMPTS) == set(Mode)
    assert set(_MODE_ONLY_PROMPTS) == set(Mode)


@pytest.mark.parametrize("mode", _GEMMA_MODES)
def test_gemma_modes_do_not_enable_tools_or_memory_writes(mode: Mode) -> None:
    """Gemma solo lee: el prompt de modo no menciona tools ni escritura."""
    mode_prompt = _MODE_ONLY_PROMPTS[mode].lower()
    assert "tool" not in mode_prompt
    assert "escrib" not in mode_prompt
    # Refuerzo: estos modos se declaran como conversacion cerrada. Normalizamos
    # el whitespace porque el wrap de linea parte la frase ("...y no\nejecutas").
    normalized = " ".join(mode_prompt.split())
    assert "no ejecutás acciones externas" in normalized


@pytest.mark.parametrize("mode", _QWEN_MODES)
def test_qwen_modes_enable_tools(mode: Mode) -> None:
    """Qwen es agente: el prompt de modo habilita tools (la escritura de
    memoria la cubren test_productividad/test_memoria por separado)."""
    mode_prompt = _MODE_ONLY_PROMPTS[mode].lower()
    assert "tool" in mode_prompt


def test_productividad_mentions_memory_write() -> None:
    """Productividad (Qwen) declara que puede escribir memoria."""
    assert "escrib" in PRODUCTIVIDAD_PROMPT.lower()


def test_memoria_mentions_memory_write() -> None:
    """Memoria (Qwen) declara que puede escribir / borrar memoria."""
    text = MEMORIA_PROMPT.lower()
    assert "escrib" in text or "borrar" in text


def test_load_prompt_rejects_invalid_mode() -> None:
    """Un modo fuera del enum levanta ``ValueError``."""
    with pytest.raises(ValueError, match="system prompt"):
        load_prompt("modo-inexistente")  # type: ignore[arg-type]


def test_load_prompt_is_cached() -> None:
    """El loader cachea: misma entrada, mismo objeto string."""
    first = load_prompt(Mode.VIDA)
    second = load_prompt(Mode.VIDA)
    assert first is second
