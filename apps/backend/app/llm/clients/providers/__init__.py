"""Clientes de los proveedores de IA por API (regla #4b, ADR-028).

Este paquete es el único lugar del backend que habla HTTP con un proveedor de IA:
chat, agente, consolidación de memoria y embeddings, siempre con HTTP compatible con
OpenAI y sin SDK de ningún proveedor (``httpx``). Nada fuera de acá llama a un
proveedor: el resto del backend entra por ``app/llm/router.py`` y por los Protocols de
``app/llm/clients/`` (``LLMClient``, ``EmbeddingClient``), donde también viven los
clientes fake de dev y CI.

``openai_compat.OpenAICompatClient`` es el cliente de chat (implementa ``LLMClient``) y
``embeddings.ApiEmbeddingClient``, el de embeddings (implementa ``EmbeddingClient``).
Los módulos con ``_`` son helpers internos del paquete (mapeo de errores HTTP,
traducción y streaming del wire de Chat Completions, nombres de tools, razonamiento
inline); no se importan desde afuera.
"""
