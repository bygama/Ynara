"""Capa LLM de Ynara.

- ``router`` decide qué alias de modelo usar según el modo activo.
- ``prompts`` contiene los prompt templates por modo.
- ``tools`` contiene las tools que puede llamar el alias ``agente``.
- ``clients`` habla con los proveedores de IA, solo desde ``clients/providers/``.

Reglas (ADR-028 D3):
- ``conversacional`` → modos estudio, bienestar y vida. Solo lee memoria.
- ``agente`` → modos productividad y memoria, y la consolidación. Lee y escribe
  memoria (la escritura va asíncrona vía Celery) y llama tools.
- Los datos de usuario salen solo a los proveedores aprobados y solo para la
  inferencia (regla #4 de AGENTS.md).
"""
