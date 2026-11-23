"""Detección por patrones de «dije que lo hice» y de «desde acá no puedo».

Lo usan los correctores para una pregunta acotada: ¿el texto afirma que una acción se
hizo (o promete hacerla)? No pretende entender español. Distingue una afirmación
``fuerte`` (patrón claro, sin negar ni ofrecer) de una ``débil`` (aparece el verbo en
pasado o participio, sin más contexto); los correctores mandan lo débil a revisar.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

Claim = Literal["fuerte", "debil", "ninguna"]

# Raíces de los verbos, ya normalizadas (minúsculas, sin tildes).
SCHEDULING_STEMS = r"(?:agend|anot|program|guard|record|registr|apunt)"
EDITING_STEMS = r"(?:cancel|borr|elimin|cambi|modific|reprogram|mand|envi|mov)"

# Fórmulas de cortesía con «no» o «sin» que no niegan nada.
_FILLERS = re.compile(
    r"\bno\s+(?:hay|pasa)\s+(?:drama|problema|nada)\b"
    r"|\bno\s+te\s+(?:preocupes|hagas\s+problema)\b"
    r"|\bsin\s+(?:drama|problema)s?\b"
    r"|\bcomo\s+no\b"
)
_NEGATIONS = frozenset({"no", "nunca", "tampoco", "sin", "ni"})
# Ofrecimientos y consejos: el verbo aparece, pero nadie dice que la acción se hizo.
_HEDGE = re.compile(
    r"\b(?:queres\s+que|si\s+queres|podes|podrias|para\s+que|conviene|deberias"
    r"|tenes\s+que|te\s+recomiendo|te\s+sugiero)\b"
)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")
_WORD = re.compile(r"\w+")

# Dice que no puede, manda a otro modo o le pide al usuario que lo haga.
_REDIRECTS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bno\s+(?:\w+\s+){0,2}puedo\b",
        r"\bno\s+tengo\s+(?:acceso|forma|manera|como)\b",
        r"\bmodo\s+productividad\b",
        r"\bpas\w*\s+al?\s+(?:modo\s+)?productividad\b",
        r"\b(?:cambi|pas)\w*\s+(?:a|al|de)\s+modo\b",
        r"\b(?:en|desde)\s+este\s+modo\s+no\b",
        rf"\bno\s+(?:lo\s+|la\s+|te\s+lo\s+)?{SCHEDULING_STEMS}\w*",
        rf"\b{SCHEDULING_STEMS}(?:alo|ala|alos|alas|atelo|atela)\b",
        rf"\b{SCHEDULING_STEMS}\w*\s+vos\b",
    )
)


def normalize(text: str) -> str:
    """Minúsculas y sin tildes, para comparar palabras."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _strong_patterns(stems: str) -> tuple[re.Pattern[str], ...]:
    done = rf"(?P<v>{stems}(?:ad[oa]s?|e|i))"
    return tuple(
        re.compile(pattern)
        for pattern in (
            rf"\b(?:listo|hecho|perfecto|dale|joya|de una|ok|okey)\b[^.!?\n]{{0,40}}\b{done}\b",
            rf"\b(?:ya|te|lo|la|los|las|se|me)\s+(?:lo\s+|la\s+|los\s+|las\s+|te\s+)?"
            rf"(?P<v>{stems}(?:e|o|i))\b",
            rf"\b(?:quedo|queda|esta|quedaron|estan|deje|tengo)\s+(?P<v>{stems}ad[oa]s?)\b",
            rf"\b(?P<v>{stems}ad[oa]s?)\s+(?:para|el|la|a\s+las|manana|hoy|en)\b",
            rf"^\W*(?P<v>{stems}ad[oa]s?)\b",
            r"\bte\s+(?:lo\s+)?(?P<v>recuerdo|aviso)\b",
            r"\bte\s+(?:lo\s+)?voy\s+a\s+(?P<v>recordar|avisar)\b",
            rf"\b(?:voy|vamos)\s+a\s+(?P<v>{stems}ar\w*)\b",
        )
    )


def _is_negated_or_hedged(sentence: str, verb_start: int) -> bool:
    """El verbo viene negado (hasta tres palabras antes) o dentro de un ofrecimiento."""
    before = sentence[:verb_start]
    if _HEDGE.search(before):
        return True
    return bool(_NEGATIONS.intersection(_WORD.findall(before)[-3:]))


def claim_level(text: str, stems: str = SCHEDULING_STEMS) -> Claim:
    """Qué tan claro afirma ``text`` que hizo (o va a hacer) una acción de ``stems``."""
    strong = _strong_patterns(stems)
    weak = re.compile(rf"\b(?P<v>{stems}(?:e|i|ad[oa]s?))\b")
    level: Claim = "ninguna"
    for raw in _SENTENCE.findall(normalize(text)):
        sentence = _FILLERS.sub(" ", raw).strip()
        is_question = sentence.endswith("?")
        for pattern in strong:
            for match in pattern.finditer(sentence):
                if _is_negated_or_hedged(sentence, match.start("v")):
                    continue
                if not is_question:
                    return "fuerte"
                level = "debil"
        for match in weak.finditer(sentence):
            if not _is_negated_or_hedged(sentence, match.start("v")):
                level = "debil"
    return level


def redirects(text: str) -> bool:
    """El texto dice que no puede, o deriva la acción a otro modo o al usuario."""
    normalized = normalize(text)
    return any(pattern.search(normalized) for pattern in _REDIRECTS)
