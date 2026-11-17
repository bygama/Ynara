"""Clientes de inferencia LLM.

- ``base`` define los Protocols (``LLMClient``, ``ToolCallParser``).
- ``providers`` implementa los clientes HTTP de los proveedores de IA (ADR-028):
  ``OpenAICompatClient`` contra la API de Chat Completions y ``ApiEmbeddingClient``
  contra la de Embeddings. Es el unico lugar del backend que habla con los
  proveedores (regla #4b).
- ``parsers`` normaliza las tool calls del formato OpenAI que devuelven los
  proveedores.
- ``fakes`` provee un cliente programable para tests del router / pool.
- ``circuit`` implementa el ``CircuitBreaker`` por instancia (M3).
- ``pool`` agrupa clientes y rutea por modelo (``ClientPool`` +
  ``RoutingStrategy``).
- ``resilient`` envuelve el pool con retry + breaker + respaldo entre
  proveedores (``ResilientClient``).
- ``embedding`` define el Protocol ``EmbeddingClient`` y el
  ``FakeEmbeddingClient`` determinista que consumen los wrappers de memoria (M7).
- ``reranker`` define el Protocol ``Reranker`` (reordena el top-K ANN) con el
  ``FakeReranker`` passthrough, el reranker de todos los entornos (ADR-028 D5).
- ``factory`` centraliza la decision Fakes (dev/test) vs. clientes reales por
  settings (``LLM_BACKEND`` / ``EMBEDDING_BACKEND``; production exige ``api`` en
  los dos), sin hardcodear los Fakes en el lifespan.

El router (``router.py``) solo conoce el Protocol ``LLMClient``, nunca la
implementacion concreta (ADR-009 D1).
"""
