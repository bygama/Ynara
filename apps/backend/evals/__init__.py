"""Set de evaluación sintético de Ynara y su runner.

Vive fuera de ``app/``: no es código de producto, no corre en la CI y no cuenta en la
cobertura. Llama al modelo por el código del backend (``app/llm``), nunca con un
cliente HTTP propio ni con un SDK (regla #4b, ADR-028 D1). Solo datos sintéticos.
Ver ``README.md``.
"""
