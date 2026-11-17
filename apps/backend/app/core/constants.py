"""Constantes transversales del backend, sin dependencias del proyecto.

Este módulo es la **fuente única de verdad** de valores que comparten capas que
no deberían acoplarse entre sí (p. ej. la capa de clientes LLM y la capa de
modelos SQLAlchemy). Vive en ``app/core`` y **no importa nada del proyecto**
(ni SQLAlchemy ni los modelos sagrados), para que cualquier capa pueda
importarlo sin arrastrar dependencias pesadas ni crear ciclos de imports.
"""

from __future__ import annotations

# Dimensión del embedding denso de bge-m3 (ADR-008/ADR-009). Fuente única de
# verdad: la importan tanto la capa LLM (``app/llm/clients/embedding.py``) como
# la capa de modelos (``app/models/memory.py``, ``Vector(EMBEDDING_DIM)``). El
# valor 1024 es estable hasta un ADR nuevo; al estar centralizado, cambia en un
# solo lugar (con su gate sagrado, regla #3, por el impacto en la tabla).
EMBEDDING_DIM = 1024

# Embeddings por API (ADR-028 D4). Son los defaults que comparten ``Settings``
# (``EMBEDDING_MODEL`` y ``EMBEDDING_BATCH_SIZE``) y el ``ApiEmbeddingClient``:
# - ``DEFAULT_EMBEDDING_MODEL``: bge-m3 (ADR-008), que da vectores de ``EMBEDDING_DIM``.
#   Si el proveedor elegido lo publica con otro id, se configura por entorno.
# - ``DEFAULT_EMBEDDING_BATCH_SIZE``: máximo de textos por request. Es conservador,
#   porque hay proveedores que aceptan pocos textos por request; una lista más larga se
#   parte en varios requests.
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_EMBEDDING_BATCH_SIZE = 32

# Alias lógicos de modelo (ADR-028 D3). El código no conoce modelos concretos: pide un
# alias y el cliente del proveedor activo lo traduce al id de su modelo (el mapa
# ``models`` de cada entrada de ``LLM_PROVIDERS``).
# - ``conversacional``: modos estudio, bienestar y vida. Solo lee memoria, sin tools.
# - ``agente``: modos productividad y memoria, y la consolidación. Lee y escribe
#   memoria (la escritura va asíncrona por Celery) y llama tools.
MODEL_ALIAS_CONVERSATIONAL = "conversacional"
MODEL_ALIAS_AGENT = "agente"
MODEL_ALIASES: tuple[str, ...] = (MODEL_ALIAS_CONVERSATIONAL, MODEL_ALIAS_AGENT)
