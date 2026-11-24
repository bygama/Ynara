"""Reconocer un hecho en lo que quedó guardado, y un texto pegado en vez de extraído.

Trabaja sobre texto en palabras sueltas (``evals.textcheck.plain``). No entiende el
idioma: una raíz dice que el hecho está nombrado, no que esté bien dicho.
"""

from __future__ import annotations

import json
import re
from typing import Any

from evals.cases import FactMatcher
from evals.textcheck import plain

# Palabras que delatan un texto pegado: la primera persona del usuario, la segunda del
# asistente y sus acuses de recibo. Un hecho bien extraído va en tercera persona.
_COPY_MARKERS = frozenset(
    "me mi mis soy estoy tengo vivo voy quiero sos estas tenes vivis tu tus vos "
    "anotado guardado corregido borrado hecho listo dale perfecto entendido recuerdo".split()
)


def write_text(write: dict[str, Any]) -> str:
    """El texto de una escritura en palabras sueltas: contra eso se comparan las raíces.

    ``Any``: el ``value`` de una preferencia es JSON del modelo, de forma libre.
    """
    if write["layer"] == "semantic":
        return plain(str(write.get("content", "")))
    value = json.dumps(write.get("value"), ensure_ascii=False)
    return plain(f"{write.get('key', '')} {value}")


def has_root(root: str, text: str) -> bool:
    """``root`` empieza una palabra de ``text``.

    Si lleva un dígito tiene que ser la palabra entera («29» no reconoce «2029»), con
    o sin la unidad pegada («17hs», «45min»).
    """
    token = plain(root)
    tail = r"(?:hs?|min)?(?![a-z0-9])" if any(ch.isdigit() for ch in token) else ""
    return re.search(rf"(?<![a-z0-9]){re.escape(token)}{tail}", text) is not None


def matches(matcher: FactMatcher, text: str) -> bool:
    if not all(has_root(root, text) for root in matcher.contains_all):
        return False
    return not matcher.contains_any or any(has_root(root, text) for root in matcher.contains_any)


def is_copy(text: str, source: str, *, fragments: bool = True) -> bool:
    """La escritura es ``source`` pegado, no un hecho extraído de él.

    Pegado literal (entero o, con ``fragments``, un tramo que es casi todo el texto), o
    con unas pocas palabras cambiadas: trae la mayoría de sus palabras y conserva su
    voz, la primera persona del usuario o la segunda del asistente. Una copia pasada a
    tercera persona no se distingue de una extracción: ese límite queda.
    """
    pasted = plain(source)
    if not pasted or not text:
        return False
    # Con espacios a los lados, para comparar palabras enteras: «hecho» no está en «hechos».
    whole, inside = f" {pasted} ", f" {text} "
    if whole in inside or (fragments and inside in whole and len(text) >= 0.6 * len(pasted)):
        return True
    words = pasted.split()
    got = set(text.split())
    shared = sum(1 for word in words if word in got)
    return shared >= 0.8 * len(words) and any(w in _COPY_MARKERS and w in got for w in words)
