# SKILL: Agregar un modo nuevo al producto

## Cuándo usar

Cuando el producto requiere un modo nuevo (más allá de los 5
actuales: productividad, estudio, bienestar, vida, memoria).

## Pre-requisitos

- ADR aprobado que justifica el modo nuevo.
- Definición clara de:
  - Alias de modelo
    ([ADR-028](../../docs/architecture/adrs/ADR-028-inferencia-por-api.md)):
    `conversacional` (solo lee memoria, sin tools) o `agente` (lee y
    escribe memoria, llama tools). El código pide el alias, nunca un
    modelo concreto: cada proveedor de `LLM_PROVIDERS` lo traduce.
  - Capas de memoria activas.
  - Tools habilitadas.
  - Tono operativo.

## Paso a paso

1. **ADR**. Crear ADR-XXX en `docs/architecture/adrs/` explicando el
   nuevo modo (qué problema resuelve, por qué no entra en uno
   existente).
2. **Config**. Agregar entrada en `ynara.config.json[modes]` con
   `model` (el alias), `memory_layers`, `tools_enabled` y `tone`.
   `load_llm_config()` frena el arranque si un modo `conversacional`
   habilita tools, o si habilita `memory` sin la capa `semantic`.
3. **Documentación de producto**. Agregar sección en
   `docs/product/MODES.md` con descripción, ejemplos, reglas duras.
4. **Tono**. Definir tono en `docs/product/TONE-OF-VOICE.md` con
   ejemplos comparados (mal/bien).
5. **Router LLM**. Actualizar `apps/backend/app/llm/router.py` para
   reconocer el modo.
6. **Prompt**. Crear `apps/backend/app/llm/prompts/<modo>.py` con el
   prompt template y registrarlo en `loader.py`.
   `apps/backend/tests/llm/test_prompts.py` corre sobre cada modo: el
   prompt cuenta la inferencia con honestidad (modelos de proveedores
   externos) y no promete inferencia local.
7. **Tools (si aplica)**. Si el modo habilita tools nuevas, seguir
   `skills/add-llm-tool/SKILL.md`.
8. **Frontend**. Agregar entrada del modo en la UI (web y mobile). Si
   el modo usa el alias `agente`, sumarlo a `AGENT_MODES` en
   `packages/core/src/features/chat/constants.ts`: un test de web lo
   compara con los modos `agente` de `ynara.config.json`.
9. **Tests**. Tests de regresión de tono + tests de routing.
10. **PR**. Review humano del equipo + aprobación de @MateoGs013.

## Checklist

- [ ] ADR aprobado.
- [ ] `ynara.config.json` actualizado.
- [ ] `docs/product/MODES.md` actualizado.
- [ ] `docs/product/TONE-OF-VOICE.md` actualizado.
- [ ] Router LLM reconoce el modo.
- [ ] Prompt template creado.
- [ ] UI muestra el modo.
- [ ] Tests pasando.
- [ ] PR aprobado por al menos un CODEOWNER.
