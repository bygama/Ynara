# Pendientes de Ynara — checklist de auditoría

> Entregable de trabajo de la auditoría del 2026-11-09.
>
> Versión pública. Los hallazgos de seguridad sin arreglar se reservaron: figuran
> con su ID y la marca *detalle reservado*, y se gestionan por el canal privado de
> `SECURITY.md`.
>
> **Base auditada:** `main` en `642c743` (2026-08-15), igual a `origin/main`.
> **Método:** lectura de código por tres agentes read-only (conexión front-back,
> deck, docs/CI) + verificaciones mecánicas + recorrido del deck en el navegador.
> **Alcance:** monorepo completo. No se corrigió nada: esto es solo el inventario.
>
> **Ampliado el 2026-11-11:** secciones 9 (llevar la demo a la tesis), 10 (la voz
> de Ynara) y 11 (promesas terminadas), y un «Por dónde empezar» actualizado.
> El mismo día se sumó la sección 12: auditoría de seis áreas con verificación
> (55 ítems nuevos, 24 correcciones a ítems existentes y LOGO-01).
> Después se sumó la sección 13, salida al mercado: VPS con GPU dedicada y deploy
> completo, cobro con Mercado Pago, legal, calidad de la IA y beta cerrada.

Cómo leerlo:

- Cada ítem tiene un ID para referenciarlo en issues, commits o PRs.
- **[V]** = verificado leyendo código o ejecutando. **[I]** = inferido, hay que confirmarlo.
- Marcá `[x]` cuando esté resuelto y mergeado.

---

## 0. Estado verificado hoy

Ya está hecho; queda como línea de base.

- [x] Copia local al día con GitHub (`git pull --ff-only`, 2 commits no funcionales).
- [x] `pnpm install --frozen-lockfile` — OK.
- [x] Lint (Biome, 728 archivos) — 0 errores, 16 warnings.
- [x] Typecheck de todo el workspace — 7 de 7 tareas OK.
- [x] Tests unitarios JS — 5 de 5 paquetes OK (web: 86 archivos, 498 tests).
- [x] Build de producción de `apps/landing` — OK, incluye `/presentacion`.
- [x] Deck recorrido a 1920×1080: 32 láminas navegables, sin imágenes rotas, sin
      elementos fuera de pantalla, sin errores de consola.
- [x] Los 4 hallazgos HIGH de la auditoría de backend del 2026-06-23 están
      corregidos en el código (AR-01, LLMC-001, DOC-07, API-002).
- [x] Los 52 endpoints del backend coinciden uno a uno con `ENDPOINTS.md`, y
      ningún cliente llama a un endpoint inexistente.

---

## 1. Presentación (`apps/landing`, ruta `/presentacion`)

### 1.1 Se nota en la defensa

- [ ] **DECK-01 · El deck no recuerda la lámina.** Al recargar o volver del botón
      del cierre arranca en la lámina 1. No hay hash ni ruta por lámina.
      `src/components/deck/Deck.tsx:24`. Fix: sincronizar la lámina activa con `#n`. [V]
- [ ] **DECK-02 · Nota de orador visible al jurado.** La lámina de la demo (21)
      muestra "Plan B: captura fija si no responde en 5 s", y esa captura no existe.
      `src/content/deck.ts:478`, `slides/Slide13.tsx:54`. Fix: sacar el texto o
      implementar el respaldo real. [V]
- [ ] **DECK-03 · Sección Promoción partida en dos.** El rótulo va Lanzamiento
      (13–16) → Promoción (17–19) → Lanzamiento (20–21) → Promoción (22–29).
      `src/content/deck.ts:131-205`, `slides/index.ts:55-63`. Fix: reordenar o
      reetiquetar el merch. [V]
- [ ] **DECK-04 · "Multidispositivo" duplicado.** Figura como feature actual del
      plan Premium y como promesa del roadmap. `src/content/deck.ts:401` y `:596`. [V]
- [ ] **DECK-05 · Falta contenido técnico.** El deck presenta "tres pilares" y
      nunca menciona los 5 modos, los 2 modelos locales (Gemma 4 12B, Qwen 3.5-9B),
      Ollama ni la memoria de 3 capas. Estudio y Vida no aparecen.
      `src/content/deck.ts:277-286`, `slides/Slide02.tsx`. Fix: sumar una lámina
      de arquitectura. [V]
- [ ] **DECK-06 · Balance del deck.** Promoción ocupa 11 de 32 láminas; hay 8
      láminas seguidas de solo imagen (22–29); la solución (02) se muestra antes
      que el problema (03). Decisión de narrativa, no bug. [I]
- [ ] **DECK-07 · Afirmaciones de privacidad de la home.** "Privada y on-prem",
      "nada sale del perímetro", "Self-hosted" chocan con el MVP real: base en
      Supabase gestionado (ADR-005) y web en Vercel (ADR-027). Solo la inferencia
      corre en hardware propio. `src/content/ynara.ts:13,117,124,218`. [V]

### 1.2 Menores de contenido

- [ ] **DECK-08 · Orden del merch.** Sale 2/3 → 3/3 → 1/3; el caption que abre
      ("La marca que se toca.") cae en la tercera. `slides/index.ts:59-61`,
      `slides/Slide16.tsx:7-11`, `deck.ts:563`. [V]
- [ ] **DECK-09 · Viabilidad.** US$70 / ~15 suscriptores supone USD 4,67 por
      cabeza; con el piso de USD 4 dan 18, sin comisión de Mercado Pago.
      `deck.ts:416-417` vs `:395`. [I]
- [ ] **DECK-10 · Moneda.** "USD 4–6" y "US$70" en láminas distintas.
      `deck.ts:395` vs `:416`. [V]
- [ ] **DECK-11 · "Empezás gratuito".** La home dice "Empezás gratis".
      `deck.ts:384` vs `ynara.ts:157-158`. [V]
- [ ] **DECK-12 · Minúscula inicial** en el primer ítem de Slide03. `deck.ts:294`. [V]
- [ ] **DECK-13 · Horario ambiguo en la demo.** "a las 7" → "19hs" puede parecer
      un bug. `ynara.ts:136-137`. [I]

### 1.3 Robustez al proyectar

- [ ] **DECK-14 · Click accidental avanza la lámina.** Confirmado en vivo.
      `Deck.tsx:138-142`. Fix: dejar solo teclado y swipe. [V]
- [ ] **DECK-15 · El CTA del cierre saca del deck** (`/#descargar`) y Enter sobre
      el link avanza en vez de navegar. `slides/Slide18.tsx:34`, `Deck.tsx:59`. [V]
- [ ] **DECK-16 · Fondo estático con reduced-motion.** El campo WebGL dibuja un
      solo frame y no cambia entre láminas. `components/field/CascadeField.tsx:418-424`. [V]
- [ ] **DECK-17 · Proyectar con build, no con dev.** `next/font/google` baja las
      fuentes en dev; sin internet falla. Usar `pnpm build && pnpm start`.
      `src/app/fonts.ts:1`. [I]
- [ ] **DECK-18 · Imágenes pesadas.** Seis PNG de 1,3 a 2,4 MB en `public/promo/`
      con `loading="lazy"`. Pasar a WebP/JPG 1920px y `eager` en el deck. [V]
- [ ] **DECK-19 · Assets sin uso.** 10 archivos (~5,1 MB) en `public/promo/`:
      `tp3-01.jpg` y `tp3-13…tp3-21.png`. [V]
- [ ] **DECK-20 · Probar en el proyector real.** Falta ver la lámina 27 (cartel de
      calle), la resolución 1366×768, el fallback sin WebGL y el rendimiento en
      una laptop modesta. [sin verificar]
- [ ] **DECK-21 · No hay índice ni agenda de secciones**, ni fullscreen propio
      (depende de F11). Opcional. [V]

### 1.4 Docs y código de la landing

- [ ] **DECK-22 · Conteo de láminas desactualizado.** La doc dice 30 y
      `presentacion/page.tsx:5-6,14` dice 18; son 32 en 10 secciones.
      `apps/landing/AGENTS.md:11,68`, `README.md:31`, `deck.ts:2,30`. [V]
- [ ] **DECK-23 · Doc de motores WebGL incorrecta.** El fondo del deck es
      `CascadeField` (el mismo de la home); `living-field/` es Canvas2D y lo usan
      4 láminas. `apps/landing/AGENTS.md:69,78-79`. [V]
- [ ] **DECK-24 · Comentarios stale** en Slide06a, Slide06b, Slide07, Slide18,
      `deck.ts:421-423` y `living-field/LivingField.tsx:42-44`. [V]
- [ ] **DECK-25 · Paleta "LOCKED" desalineada.** El brief dice azul como único
      acento; el deck usa violeta, Índigo y Azul violáceo, que existen como tokens
      pero no figuran en `REBUILD-BRIEF.md:72-83`. Hex sueltos: `#6e92cc`
      (`living-field/field/climate.ts:20,21,24`) y `#14171f` (`CascadeField.tsx:489`). [V]
- [ ] **DECK-26 · Páginas internas de la landing entran al build público.** Revisar
      la configuración de indexación y decidir si se excluyen del build. [V]
- [ ] **DECK-27 · Landing sin tests ni CI propia** (ver CI-01). [V]

---

## 2. Conexión del ecosistema (apps ↔ backend)

### 2.1 Rompe o va a romper

- [ ] **CON-01 · CORS del admin.** El admin corre en `:3002` y el default de
      `CORS_ORIGINS` solo trae `:3000` y `:8081`. Con mocks apagados, todo
      `/v1/admin/*` falla en dev. `apps/backend/app/core/config.py:272-276`,
      `apps/admin/package.json:8`. [V]
- [ ] **CON-02 · Nadie usa el refresh token ni maneja el 401.** El endpoint de refresh
      existe, pero web y mobile descartan el `refresh_token`. Cuando el access
      vence quedan con sesión muerta sin volver al login. [V]
- [ ] **CON-03 · Memoria sin paginar.** El cliente llama `GET /v1/memory` sin
      `limit`/`offset`; el backend corta en 50 por capa. No se vio UI de "cargar más".
      `packages/core/src/features/memory/api.ts:33`, `apps/backend/app/api/v1/memory.py:160-176`. [V/I]

### 2.2 Backend sin cliente (features huérfanas)

- [ ] **CON-04 · Recordatorios.** El CRUD `/v1/reminders` no tiene cliente, schema
      Zod ni pantalla. La tool `reminder` del chat sí escribe, así que el usuario
      no ve lo que el asistente agendó. `apps/backend/app/api/v1/reminders.py`. [V]
- [ ] **CON-05 · Dispositivos y push.** `POST/DELETE /v1/devices` sin consumidor;
      `expo-notifications` está instalado y no se usa. [V]
- [ ] **CON-06 · Sesiones.** `GET /v1/sessions`, detalle y `close` sin consumidor.
      El historial es solo local, no se sincroniza entre dispositivos y las sesiones
      nunca se cierran. No hay endpoint de turnos de una sesión. [V]
- [ ] **CON-07 · Modos hardcodeados.** `GET /v1/modes` sin consumidor; la lista
      vive en `packages/core/src/features/modes/index.ts:238-244`. Cambiar un modo
      implica tocar cuatro lugares. [V]

### 2.3 Front sin backend (maquetas)

- [ ] **CON-08 · Avisos / anticipaciones (web).** Sin endpoint; devuelve `[]` con
      mocks apagados y el contador sale de datos canned.
      `apps/web/src/features/today/anticipations.ts:26-35`, `components/AppNav.tsx:18-20`. [V]
- [ ] **CON-09 · Check-in matinal (web).** Estado local, sin backend. [V]
- [ ] **CON-10 · Paywall / plan (web).** Maquetado, sin pago ni plan real.
      `TuView.tsx:305,486`. [V]
- [ ] **CON-11 · Recuperar contraseña (mobile).** Maqueta, no hay endpoint.
      `apps/mobile/src/features/auth/ForgotPasswordScreen.tsx:12`. [V]
- [ ] **CON-12 · OAuth de mobile.** Simulado solo con mocks; con el flag apagado
      muestra "próximamente". `apps/mobile/src/features/auth/WelcomeScreen.tsx`. [V]

### 2.4 Contratos desalineados

- [ ] **CON-13 · `UserOut.email`.** Nullable en el backend, obligatorio en Zod.
      Hoy no se dispara. `packages/shared-schemas/src/user.ts:198`. [V]
- [ ] **CON-14 · `TokenOut.refresh_token`.** Opcional en el backend, obligatorio en
      core. `packages/core/src/features/auth/schemas.ts:19`. [V]
- [ ] **CON-15 · Códigos de error del chat.** El cliente busca una clave `error`
      que el backend nunca manda (usa `detail`); un 429 o 401 cae al mensaje
      genérico. `packages/core/src/features/chat/api.ts`, `shared-schemas/src/chat-copy.ts:24-29`. [V]

### 2.5 Paridad web vs mobile

- [ ] **CON-16 · Agenda en mobile es solo lectura**; en web se crea, edita, mueve
      y borra. [V]
- [ ] **CON-17 · Mobile no muestra el razonamiento** aunque el hook lo acumula. [I]
- [ ] **CON-18 · Mobile no tiene** avisos, check-in, paywall ni lista de sesiones. [V]

### 2.6 Limpieza

- [ ] **CON-19 · Hallazgo de web.**
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **CON-20 · Mocks muertos en web.** Handlers para `/v1/auth/signup` y
      `/v1/auth/login` (no existen); `POST /v1/onboarding` no tiene mock y sale al
      backend real. `apps/web/src/lib/api.mocks.ts:42,64,257`. [V]
- [ ] **CON-21 · Comentarios "PROVISIONAL / sin backend"** sobre endpoints que ya
      existen (agenda, búsqueda de memoria, Hoy, reminder, onboarding). [V]
- [ ] **CON-22 · Admin arranca con mocks por default** (`"true"`), al revés que
      web y mobile. [V]

---

## 3. Producción e infraestructura

- [ ] **INF-01 · Deploy del backend.** `deploy-backend.yml` es un placeholder
      (`echo "TODO: SSH a VPS…"`), sin smoke test ni rollback, y apunta a una VPS
      que contradice ADR-027. [V]
- [ ] **INF-02 · Celery beat en producción.** El código está listo, pero no hay
      despliegue ni monitoreo de última corrida; si no corre, decay y retención
      no se ejecutan y nada avisa (WW-07). [V]
- [ ] **INF-03 · `infra/prod` incompleto.** Faltan `cloudflared.yml` (o su
      equivalente Tailscale), `backup-db.sh` con cron y `postgres-tuning.conf`.
      `infra/prod/README.md:57`. [V]
- [ ] **INF-04 · Serving vLLM real** (GPU de 24 GB+): reconciliar
      `infra/vllm/start-vllm.sh` y medir VRAM. Hoy funciona con Ollama. [V]
- [ ] **INF-05 · Streaming real del chat.** El SSE trocea la respuesta ya
      generada; no hay primer token temprano. Diferido por ADR-024 (SCAL-01). [V]
- [ ] **INF-06 · Mobile: `eas.json` con datos TODO** (`appleId`, `ascAppId`,
      `appleTeamId`, service account de Google). Sin `eas submit`. [V]
- [ ] **INF-07 · `deploy-web.yml` es un placeholder.** [V]
- [ ] **INF-08 · Runbook y deploy docs incompletos.** `docs/operations/RUNBOOK.md:14,136,162`,
      `DEPLOY.md:113`. [V]
- [ ] **INF-09 · Remoto git.** Alinear el remoto local con el repo canónico
      (`git remote set-url`).

---

## 4. CI y calidad

- [ ] **CI-01 · Landing sin CI.** `apps/landing/**` no está en los paths de ningún
      workflow; nunca se buildea en CI. [V]
- [ ] **CI-02 · E2E de Playwright fuera de CI** (issue 184). Existen
      `playwright.config.ts` y 6 specs, sin workflow ni script `e2e`. [V]
- [ ] **CI-03 · Contract-guards no corre en PRs solo-frontend** (issue 175). [V]
- [ ] **CI-04 · `ci-docs-catalog.yml` con paths incompletos.** No dispara con
      `app/models/**`, `app/llm/tools/**`, `MODELS.md` ni `TOOLS.md`, así que los
      checks estrictos no corren cuando cambia lo que vigilan. [V]
- [ ] **CI-05 · Mobile sin CI** (excluido a propósito). [V]
- [ ] **CI-06 · `deploy-mobile.yml` desalineado:** node 20 y pnpm fijado, el resto
      usa node 22. [V]
- [ ] **CI-07 · 16 warnings de lint** (non-null assertions, entre otros). [V]
- [ ] **CI-08 · Build scripts ignorados por pnpm** (`msw`, `sharp`): falta
      `pnpm.onlyBuiltDependencies`. [V]
- [ ] **CI-09 · Onboarding (issue 180).** El código ya navega a
      `/hoy?welcome=true`, pero el issue sigue abierto y los e2e no se corrieron.
      Correrlos y cerrar el issue. [V/sin verificar]
- [ ] **CI-10 · Tokens sin variante Noche** (issue 183): `--color-overlay` y
      `--color-error-soft`. [sin verificar]
- [ ] **CI-11 · Backend sin probar en esta máquina.** Falta `uv`; no se corrieron
      `pytest` ni `ruff`. [sin verificar]
- [ ] **CI-12 · Menores de workspace:** `Makefile build-mobile` falla (no hay
      script `build`), `package.json` tiene un campo `workspaces` que pnpm ignora,
      TODO viejo en `pnpm-workspace.yaml:5-6`, el doctor no chequea
      `apps/admin/.env.example`. [V]

---

## 5. Documentación desactualizada

### 5.1 README y AGENTS raíz

- [ ] **DOC-01 · Conteos del README.** Dice 15 ADRs, 3 apps, 5 packages, 8 tools,
      ~500 archivos; son 27, 5, 3 (core, shared-schemas, ui), 10 y 1294.
      `README.md:39-44`. [V]
- [ ] **DOC-02 · `packages/shared-types` no existe** y figura en `README.md:41,245`
      y `AGENTS.md:192`. [V]
- [ ] **DOC-03 · Deploy.** `README.md:167` y `SECURITY.md:72` dicen Cloudflare
      Tunnel; ADR-027 dice Tailscale Funnel. [V]
- [ ] **DOC-04 · Tool `task` omitida** en Productividad: `README.md:51`,
      `docs/product/MODES.md:11,23` y la tabla de routing de `AGENTS.md`. [V]
- [ ] **DOC-05 · Árbol del README** sin `apps/admin`, `apps/landing`,
      `docs/planning`, `brand`, `compliance`; scripts y e2e desactualizados.
      `README.md:227-271`. [V]
- [ ] **DOC-06 · Expo 53+** en README y AGENTS; el real es `~54.0.0`. [V]
- [ ] **DOC-07 · `AGENTS.md:197`** dice "15 ADRs, ADR-012 numerado dos veces"; son
      27 sin duplicados. `AGENTS.md:195` y `README.md:248` atribuyen `core` a
      ADR-012 (es ADR-016). [V]
- [ ] **DOC-08 · `DESIGN.md` descrito como placeholder** (`AGENTS.md:173`,
      `apps/mobile/AGENTS.md:14-15`); tiene 763 líneas. [V]
- [ ] **DOC-09 · Restos de Codex/Gemini.** `AGENTS.md:351`, badge de `README.md:11`,
      `docs/conventions/GLOSSARY.md:101`, `scripts/ynara-doctor.sh:117`. [V]

### 5.2 Índices y planes

- [ ] **DOC-10 · Índice de ADRs sin ADR-027.** `docs/architecture/README.md:17-45`;
      `apps/backend/docs/README.md:19` también habla de 15. [V]
- [ ] **DOC-11 · Planes obsoletos.** `WEB-FULL-FUNCTIONALITY-ROADMAP.md` y
      `FRONTEND-APP-BUILD-PLAN.md` §2 marcan como pendiente lo ya hecho. Actualizar
      o archivar. [V]
- [ ] **DOC-12 · Planes parcialmente stale.** `CALENDAR-PLAN.md` y
      `RESPUESTAS-CONTRATO-CHAT.md`. [V]
- [ ] **DOC-13 · Planes históricos sin archivar.** `BACKEND-MEMORY-ROADMAP.md`,
      `LLM-INFERENCE-INTEGRATION.md`, `SESION-2026-05-30-CAPA-LLM.md`,
      `FRONTEND-CHAT-PLAN.md`, `FRONTEND-REDESIGN-PLAN.md`. [V]
- [ ] **DOC-14 · 11 links rotos** en `docs/planning/archive/FRONTEND-ONBOARDING-PLAN.md`
      (líneas 20, 21, 262, 296, 734–740). [V]
- [ ] **DOC-15 · AGENTS por app.** `apps/web/AGENTS.md:56,68` (dice `packages/ui`
      vacío y "Vitest o Jest"), `apps/mobile/AGENTS.md` (no menciona `@ynara/core`),
      `packages/config/README.md` (lo llama paquete sin `package.json`). [V]
- [ ] **DOC-16 · Config con parámetros de vLLM** (`awq_marlin`, `fp8`) cuando el
      serving real es Ollama. `ynara.config.json`, `ynara-config.schema.json:54`. [V]

---

## 6. Deuda de backend (auditoría 2026-06-23)

- [ ] **BE-01 · `moat()` y `overview()` de más de 50 líneas** (SVC-03).
      `app/services/admin_metrics.py`. [V]
- [ ] **BE-02 · HNSW sin `ef_search` tuneado** (MEM-SACRED-02). Diferido por
      ADR-025; gate regla #3. [V]
- [ ] **BE-03 · Código muerto.** `ToolExecutionError` y `MemoryRetrievalError`
      nunca se lanzan (`app/llm/errors.py:101,114`; el docstring de `router.py:60`
      miente), `OpenAIToolCallParser.accumulate()` sin llamadas (`parsers.py:57`),
      `UserCreate` sin instanciar (`schemas/user.py:40`), `UserBase` en `__all__`. [V]
- [ ] **BE-04 · IDs sin descripción.** CORE-001/002/003, AR-05, ADMIN-001 y SVC-06
      figuran en la auditoría sin detalle; no se pudieron verificar. [sin verificar]
- [ ] **BE-05 · Idempotencia para `acks_late=True`** (STORES-01), necesaria si se
      reactiva la pasada async del agente. [V]

---

## 7. Fuera del repo

- [ ] **EXT-01 · Documento escrito de la tesis.** No está en esta carpeta; los
      wireframes se referencian en `Desktop\Uni\Tesis-Ynara`. No se auditó.
- [ ] **EXT-02 · Carpeta `Desktop\Ynara\Ynara-Landing`.** Copia idéntica a
      `apps/landing` (difiere un comentario) con `node_modules` roto. Candidata a borrar.
- [ ] **EXT-03 · Carpeta `Desktop\Ynara\Ynara-mockup`.** Mockup Vite de junio, sin
      git. Decidir si se archiva.
- [ ] **EXT-04 · Datos del deck sin respaldo en el repo:** nombres del equipo,
      costo de US$70, conversión a ARS y datos de Nerdearla 2026. Revisarlos a mano.

---

## 8. Front: diseño, usabilidad y estructura

Revisión del 2026-11-09 contra `IDENTITY.md` y `DESIGN.md` v4, con la web
corriendo en claro, Noche y móvil (375 px). **No se vio:** el chat con una
conversación, la agenda con eventos, la memoria con datos, los pasos del
onboarding después de la cuenta, ni la app móvil nativa.

Criterio: no se cambia la marca. Se cambia la composición de las pantallas y la
navegación para que cumplan lo que `DESIGN.md` ya promete ("editorial,
des-encajonado, calma con alma").

### 8.1 Lo que se mantiene (no tocar)

- [x] Space Grotesk para display y DM Sans para texto.
- [x] Paleta azul→violeta sobre marfil y Noche, con CTA plano.
- [x] Gradiente solo en fondo vivo, logo y orbe, con el test `gradient-guard`.
- [x] Fondo vivo de nodos e hilos como expresión de "memoria y conexión".
- [x] Accesibilidad de base: alto contraste, reducción de movimiento, tamaño de texto.

### 8.2 Pantallas

- [ ] **UI-01 · Hoy es una pila de tarjetas idénticas.** Cuatro avisos con el
      mismo formato, cada uno con avatar "Ynara" y botón primario lleno;
      contradice `DESIGN.md` §12 (des-encajonado) y §13 (grid de cards
      idénticas). Cambio: una sola anticipación destacada y el resto como lista
      con hilos finos y acciones de texto.
      `apps/web/src/features/today/components/HoyView.tsx`. [V]
- [ ] **UI-02 · El botón primario cambia de color según el modo** (azul, violeta,
      índigo) y hay cuatro primarios en pantalla. Cambio: un solo primario azul
      por vista; el color de modo queda como punto o tinte plano. [V]
- [ ] **UI-03 · La entrada al chat es un título y un botón** sobre media pantalla
      vacía; hace falta un click extra para escribir. Cambio: campo de texto
      directo, tres o cuatro sugerencias y las conversaciones recientes debajo
      (`DESIGN.md` §10 ya lo pide para el estado vacío). `/chat`. [V]
- [ ] **UI-04 · Tema claro sin calidez.** El fondo vivo tiñe todo de gris
      azulado y el marfil (`#FAF9F5`) no se percibe. Cambio: bajar la intensidad
      del campo en claro. [V]
- [ ] **UI-05 · Escritorio desaprovechado.** A 1440 px el contenido es una
      columna de 640–680 px y el resto queda vacío. Cambio: en Hoy, segunda
      columna con la agenda del día a partir de `xl`. [V]
- [ ] **UI-06 · Entradas animadas que demoran el contenido.** El contenido
      aparece detrás de un fundido con stagger; debe verse de inmediato y
      animar solo como realce. `HeroReveal`, `anim-screen-in`. [V/I]
- [ ] **UI-07 · Transiciones entre pantallas sin cablear.**
      `apps/web/src/lib/viewTransition.ts` solo lo usa la página de pruebas
      (`DESIGN.md` §16 #8 sigue abierto). [V]
- [ ] **UI-08 · Errores y vacíos genéricos.** La misma caja "No pudimos traer…"
      en Hoy, Agenda y Memoria. Cambio: estados con voz de marca y una acción
      útil por vista. [V]
- [ ] **UI-09 · Íconos ambiguos.** El de "Tú" parece sincronizar y el orbe junto
      al saludo se lee como un botón de detener. [V]
- [ ] **UI-10 · "Ynara" repetido en cada aviso.** Avatar y nombre en las cuatro
      tarjetas; alcanza con un encabezado de sección. [V]

### 8.3 Navegación

- [ ] **UI-11 · Sidebar sobrecargado.** Cuatro destinos, lista de modos, tres
      desplegables y selector de tema. Cambio: cinco destinos (Hoy, Chat, Agenda,
      Memoria, Tú) y el resto fuera de la barra. `apps/web/src/components/AppNav.tsx`. [V]
- [ ] **UI-12 · Memoria escondida.** Es pilar de marca y está en un desplegable;
      en la barra móvil no existe. [V]
- [ ] **UI-13 · El modo se cambia en dos lugares** (lista del sidebar y chip del
      encabezado). Dejar un solo selector. [V]
- [ ] **UI-14 · Sin paleta de comandos ni atajos.** Buscar está en un
      desplegable. Cambio: ⌘K / Ctrl+K para buscar y saltar entre vistas. [V]
- [ ] **UI-15 · Sin ruta de login propia.** El usuario que vuelve cae en "Crear
      cuenta" (ver WEB-08 / M-53 en la auditoría de mercado). Cambio: grupo de
      rutas de autenticación separado del onboarding. [V]

### 8.4 CSS y técnica de front

- [ ] **UI-16 · CSS moderno sin aprovechar.** Un solo uso de `color-mix`/`oklch`,
      uno de `text-wrap`, ninguna container query. Cambio: `text-wrap: balance`
      en títulos y `pretty` en párrafos, container queries en tarjetas y filas,
      tintes de modo derivados con `color-mix`. [V]
- [ ] **UI-17 · Sin regresión visual.** `/test-ds` existe pero no hay snapshots
      en claro y Noche (`DESIGN.md` §16 #11). [V]
- [ ] **UI-18 · Indicador de errores de Next visible en desarrollo** ("1 issue")
      en todas las pantallas: revisar y resolver la advertencia. [V]

### 8.5 Estructura del proyecto

- [ ] **EST-01 · Tokens de color copiados en cuatro lugares:**
      `apps/web/src/app/globals.css` (104 variables), `apps/admin/src/app/globals.css`
      (106), `apps/landing/src/app/globals.css` (24) y `apps/mobile/src/lib/tokens.ts`.
      Cambio: un paquete único de tokens que genere CSS y TypeScript. [V]
- [ ] **EST-02 · Componentes duplicados entre web y admin.** Unos 15 archivos con
      el mismo nombre (Button, Card, ChipGroup, Sheet, Toast, LivingField,
      YnaraMark, YnaraOrb, YnaraWordmark, ModeChip, `modes.ts`…). Cambio: moverlos
      a `packages/ui`, que hoy solo tiene íconos y presets de motion. [V]
- [ ] **EST-03 · `DESIGN.md` desactualizado.** Dice que Agenda y Tú no existen,
      que el tema no sigue al sistema y que faltan tokens de duración; el código
      ya tiene las tres cosas (`stores/theme.ts` soporta `system`,
      `globals.css:176-181`). [V]
- [ ] **EST-04 · Voz de marca incompleta.** `IDENTITY.md:38-40` tiene tres de los
      cinco rasgos en TODO. [V]
- [ ] **EST-05 · Mock de registro roto.** El handler apunta a `/v1/auth/signup` y
      la app llama a `/v1/auth/register`: con mocks no se puede recorrer el
      onboarding (detalle de CON-20). [V]
- [ ] **EST-06 · El service worker de mocks solo se genera con `predev`.** Si se
      arranca `next dev` directo, los mocks fallan en silencio. Documentarlo o
      generarlo en `postinstall`. [V]
- [ ] **EST-07 · `TuView.tsx` tiene 490 líneas.** Partirlo en secciones.
      También `DayView.tsx` (399) y `AppNav.tsx` (316). [V]
- [ ] **EST-08 · Listas sin virtualizar** en chat y memoria (deuda ya anotada en
      `DESIGN.md` §16). [V]

---

## 9. Llevar el diseño de la demo a la tesis

Agregado el 2026-11-11. La demo (`../Ynara-demo`, fuera de este repo, HTML y JS
sin dependencias) es la dirección visual aprobada como base: marfil, una columna,
el diamante como firma, el día como hilo de nodos y la memoria como red. Sus
reglas están escritas en `../Ynara-demo/.claude/skills/ynara-interfaz/SKILL.md`.
Nada de esto está hecho en `apps/web` ni en `apps/mobile`.

Varios ítems de la sección 8 quedan absorbidos acá; se indica en cada uno.

### 9.1 Decisiones previas (humano)

- [ ] **TES-01 · Adoptar la dirección de la demo como `DESIGN.md` v5.** Reescribir
      `DESIGN.md` con: tres tamaños de texto, dos pesos, escala de espacios
      4·8·16·24·40·64, una columna de 560 px, estructura de aplicación y reglas
      de movimiento. Reemplaza EST-03. [V]
- [ ] **TES-02 · Traer la skill y los agentes al monorepo.** Copiar
      `ynara-interfaz`, `ynara-disenador` y `ynara-critico` a `.claude/` de este
      repo, y decidir si también entran `impeccable` y
      `make-interfaces-feel-better` (licencias Apache 2.0 y MIT). Por PR. [V]
- [ ] **TES-03 · Decidir dónde vive la demo.** Hoy no está en git. Opciones:
      `docs/prototypes/demo/` en este repo, repo aparte, o dejarla local. [V]
- [ ] **TES-04 · Agenda: qué vistas sobreviven.** La app tiene día, semana, mes y
      lista; la demo propone solo siete días con el hilo. Decidir antes de tocar
      `features/agenda`. [V]

### 9.2 Base compartida

- [ ] **TES-05 · Tokens nuevos dentro del paquete único de EST-01.** Sumar
      `--fs-sub`, la sombra en capas `--lift`, los colores de modo aclarados para
      Noche y el `theme-color` por tema. Hacerlo junto con EST-01 y EST-02 para
      no rediseñar dos veces. [V]
- [ ] **TES-06 · El diamante como componente.** Hoy `YnaraOrb` son dos anillos y
      un diamante. La demo usa solo el diamante con halo, en cinco estados
      (en espera, escuchando, pensando, hablando, interrumpida). Va a
      `packages/ui`. Cierra UI-09 (el orbe se leía como botón de detener). [V]
- [ ] **TES-07 · Fondo vivo ralo y fuera del texto.** `LivingField` tiñe todo de
      gris azulado. En la demo la red vive solo en los márgenes, centrada en el
      área de contenido, y ocupa la pantalla entera solo en la voz y la
      bienvenida. Cierra UI-04. [V]
- [ ] **TES-08 · Íconos con trazo de 1,5 px reales** y relleno suave en la
      sección activa. Revisar el set de `packages/ui/src/icons` (hoy trazo 2,2). [I]

### 9.3 Estructura y navegación

- [ ] **TES-09 · Estructura de aplicación.** Escritorio: barra lateral de 232 px
      con Hoy, Chat, Agenda, Memoria, «Buscar» y la cuenta al pie. Móvil: barra
      inferior flotante con el diamante al centro, que al tocarlo se convierte en
      campo de escritura. Absorbe UI-11, UI-12 y UI-14.
      `apps/web/src/components/AppNav.tsx`, `AppShell.tsx`, `nav-items.ts`. [V]
- [ ] **TES-10 · El modo se elige en el campo de escritura.** Menú que nace del
      selector en escritorio, bandeja en móvil, y una onda del color nuevo al
      cambiar (también cuando lo cambia Ynara). Absorbe UI-13 y UI-02. [V]
- [ ] **TES-11 · Campo de escritura global.** «Decile algo a Ynara…» disponible en
      todas las pantallas; al enviar abre un chat con ese texto. Absorbe UI-03. [V]
- [ ] **TES-12 · La cabecera y la navegación no se reconstruyen al navegar.** Solo
      entra el contenido. Absorbe UI-06 y UI-07. [V]

### 9.4 Pantallas

- [ ] **TES-13 · Hoy.** Saludo, el hilo del día con el diamante en el ahora, una
      sola anticipación dicha por Ynara dentro del hilo, cinco pendientes como
      mucho y una línea de cierre cuando está todo hecho. Absorbe UI-01 y UI-10.
      UI-05 (segunda columna en escritorio) queda descartado: una sola columna.
      `apps/web/src/features/today/components/HoyView.tsx`. [V]
- [ ] **TES-14 · Agenda.** Tira de siete días, hilo del día elegido y alta por
      texto («Dentista mañana 19 hs»). Depende de TES-04. Partir `DayView.tsx`
      (EST-07). [V]
- [ ] **TES-15 · Memoria como red.** Islas con el nombre de su tema, vínculos que
      dicen por qué existen, el recuerdo elegido se vuelve diamante y se lee ahí
      mismo. **Requiere backend:** una forma de vincular recuerdos (similitud por
      embeddings o tema) y de saber si un recuerdo lo dijo la persona o lo
      infirió Ynara, más un campo de confirmación. Toca tablas de memoria:
      **necesita aprobación humana y migración**. [I]
- [ ] **TES-16 · Chat.** Línea plegada «Usé N recuerdos» en lugar de chips,
      tarjeta por acción con deshacer, razonamiento plegado, selector de modo en
      el campo. Verificar que la respuesta del backend devuelva los IDs de los
      recuerdos usados. [I]
- [ ] **TES-17 · Tú.** Recortar a cuenta, apariencia, voz y datos. Partir
      `TuView.tsx` (EST-07). [V]
- [ ] **TES-18 · Hojas.** Entran por la derecha en escritorio y suben desde abajo
      en móvil. La de evento debería abrir en resumen y editar a pedido. [V]
- [ ] **TES-19 · Bienvenida** con la estética nueva (cuatro pasos, fondo pleno). [V]
- [ ] **TES-20 · Vacíos y errores con voz de marca.** Absorbe UI-08. Ver VOZ-11. [V]

### 9.5 Interacción y pulido

- [ ] **TES-21 · Capa de interacción.** Titular por palabras, hilo que se teje,
      red de memoria que se dibuja, pulso de la red cuando Ynara piensa, festejo
      al cerrar todo, diamante que viaja a Memoria al guardar. Con el filtro de
      frecuencia de la skill: lo cotidiano no anima. Usar los presets de motion
      de `packages/ui`. [V]
- [ ] **TES-22 · Pasada de pulido** con la lista de 15 controles de la skill:
      presión a 0,96, sin rótulo sobre los títulos, foco visible, zonas de toque
      de 44 px, movimiento reducido que conserva los fundidos, selección y
      barras de scroll con la paleta. Absorbe UI-16. [V]
- [ ] **TES-23 · Pruebas.** Actualizar `AppNav.test.tsx`, mantener
      `gradient-guard` y sumar capturas de regresión en claro y Noche (UI-17). [V]

### 9.6 Lo que viene después

- [ ] **TES-24 · App móvil nativa** (`apps/mobile`): misma estructura, con la
      barra inferior y el diamante al centro. No se revisó en esta ronda. [I]
- [ ] **TES-25 · Deck y landing** con capturas del producto nuevo. [V]
- [ ] **TES-26 · Admin** hereda tokens y componentes compartidos (EST-02). [I]

### 9.7 Pendientes de la propia demo

- [ ] **DEMO-01 · El README describe una versión anterior** (editorial, oscura). [V]
- [ ] **DEMO-02 · La hoja de evento abre directo en edición**, con cinco campos. [V]
- [ ] **DEMO-03 · La pantalla de voz tiene controles de más** («Ver el chat»,
      «Demo guiada», teclado, micrófono y cerrar). [V]
- [ ] **DEMO-04 · Tú sigue larga** (unas doce filas). [V]
- [ ] **DEMO-05 · Al sumar un pendiente se repinta toda la pantalla**, no la lista. [V]
- [ ] **DEMO-06 · Texto duplicado en respuestas con memoria:** la respuesta cita
      los recuerdos con fecha y «Usé N recuerdos» los repite. `js/brain.js`. [V]
- [ ] **DEMO-07 · El mismo «aparece desde abajo» en cinco componentes**
      (mensajes, tarjeta de memoria, hojas, bienvenida, avisos). [V]
- [ ] **DEMO-08 · Sin probar:** anchos de tablet (640–900 px), tema Noche después
      de los últimos cambios, Agenda, Memoria y Tú con la estructura nueva, la
      bandeja de modo en móvil, la bienvenida y el micrófono real. [V]
- [ ] **DEMO-09 · El detector de Impeccable no se corrió** (su lanzador baja un
      binario; lo ejecuta una persona si lo quiere). [V]

---

## 10. La voz de Ynara

Dos frentes distintos con el mismo nombre: la voz hablada (función) y la voz
escrita (tono de marca). Los dos están incompletos.

### 10.1 Voz hablada

Estado: **no existe en la tesis.** No hay reconocimiento ni síntesis de voz en
`apps/web`, `apps/mobile` ni `apps/backend` (búsqueda del 2026-11-11, sin
resultados). La auditoría de mercado la lista como idea de roadmap. La demo la
simula con la Web Speech API del navegador.

- [ ] **VOZ-01 · Decidir la arquitectura (ADR nuevo).** La Web Speech API manda
      el audio a los servidores del fabricante del navegador, lo que contradice
      «on-prem» (PROM-02), y no garantiza voces en español. Evaluar
      reconocimiento y síntesis locales en el servidor de inferencia. [I]
- [ ] **VOZ-02 · Backend.** Endpoints de audio a texto y de texto a audio, en
      streaming, documentados en `ENDPOINTS.md`. Sin guardar audio. [I]
- [ ] **VOZ-03 · Web: pantalla de voz.** El diamante sobre la red a pantalla
      completa, cinco estados, interrupción, subtítulos palabra por palabra,
      tarjetas de acción, manos libres y alternativa por teclado. Referencia: la
      demo (`js/voice.js`). [V]
- [ ] **VOZ-04 · Móvil nativo.** Permiso de micrófono, audio en segundo plano y
      la misma pantalla. [I]
- [ ] **VOZ-05 · Elegir cómo suena Ynara.** Timbre, acento rioplatense y
      velocidad que correspondan a «calma con alma». Hoy la demo usa la voz que
      tenga el dispositivo, que puede no ser en español. Probar con usuarios. [V]
- [ ] **VOZ-06 · Voz dentro del chat.** Los asistentes actuales muestran la voz
      en el mismo hilo, con puntos clave; evaluar si reemplaza o acompaña a la
      pantalla completa. [I]
- [ ] **VOZ-07 · Privacidad y legal.** Consentimiento explícito de micrófono, no
      guardar audio y decirlo en los términos (ver bloque legal de la auditoría
      de mercado). [I]
- [ ] **VOZ-08 · Accesibilidad.** Subtítulos siempre, estado anunciado a lectores
      de pantalla y camino completo sin voz. [V]
- [ ] **VOZ-09 · Medir.** Latencia hasta la primera palabra y tasa de error del
      reconocimiento en rioplatense, antes de prometerla (PROM-10). [I]

### 10.2 Voz escrita (tono)

- [ ] **VOZ-10 · Completar los cinco rasgos de la voz.** `IDENTITY.md:38-40`
      tiene tres en TODO. Es EST-04; lo define una persona. [V]
- [ ] **VOZ-11 · Reescribir los textos de interfaz** de `apps/web` con
      `TONE-OF-VOICE.md`: vacíos, errores, avisos y confirmaciones. La demo sirve
      de referencia («Listo, agendado…», «Todavía no hay nada acá»). [V]
- [ ] **VOZ-12 · Sacar lo que rompe el tono.** El emoji del paywall («Premium es
      demo por ahora 🔒») y el meta-relato del tipo «Pasé a modo X para esto». [V]
- [ ] **VOZ-13 · La voz por modo en los prompts.** Verificar que los prompts del
      backend reflejen `TONE-OF-VOICE.md` por modo y sumar evaluaciones que lo
      comprueben. [I]
- [ ] **VOZ-14 · Una sola fuente para los textos.** Hoy los textos están dentro
      de los componentes; evaluar un archivo de contenidos por pantalla. [I]

---

## 11. Promesas terminadas

Regla: **cada promesa pública termina de una de dos formas — cumplida en el
código, con una prueba que lo demuestre, o reescrita para decir lo que el
producto hace.** No queda ninguna a medias. Amplía M-16 y M-41 de la auditoría
de mercado.

- [ ] **PROM-00 · Inventario completo.** Recorrer landing, deck, README,
      onboarding, textos de la app y la demo, y listar cada frase que promete
      algo, con archivo y línea. Lo de abajo es lo ya encontrado. [V]

| ID | Promesa | Dónde | Hoy | Para darla por terminada |
|---|---|---|---|---|
| PROM-01 | Te avisa a tiempo | Producto entero | Los recordatorios se marcan como enviados sin entregarse; no hay push ni email (M-41) | Entrega real por Web Push, push móvil y correo; no marcar enviado sin entrega |
| PROM-02 | «Privada y on-prem», «nada sale del perímetro» | `apps/landing/src/content/ynara.ts:13,117,124`, `README.md:3,17` | Base en Supabase y web en Vercel | Alojar todo en infraestructura propia, o decir «inferencia on-prem» y dónde están los datos |
| PROM-03 | «Modelos propios» | `README.md:16,339` | Gemma y Qwen sin ajustar | Decir «modelos abiertos que servimos nosotros», o ajustarlos |
| PROM-04 | «Tu memoria está cifrada» | Landing y README | Alcance del cifrado a documentar | Acotar la frase a lo que está cifrado o completar el cifrado |
| PROM-05 | «Cita textual lo que recuerda. Nunca inventa.» | `ynara.ts:78,123` | Garantía absoluta sobre un modelo de lenguaje | Citas verificables con el ID del recuerdo (TES-16), evaluación de fidelidad y frase sin absolutos |
| PROM-06 | «Te frena antes del burnout», «lee tu energía» | `ynara.ts:88` | Solo hay un estado de ánimo en el onboarding | Construir los check-ins que lo sustenten o quitar la frase |
| PROM-07 | Se adelanta a lo que necesitás | Hoy, deck | Parte de lo que el asistente hace no se ve (CON-04 a CON-06) | Anticipación visible en Hoy (TES-13) con origen explicado |
| PROM-08 | Tus datos son tuyos | Tú, landing | El export solo trae memoria (M-14) | Export completo: perfil, conversaciones, eventos, tareas y recordatorios |
| PROM-09 | Podés corregir u olvidar cualquier recuerdo | Memoria, demo | **Incumplida.** Solo los hechos se pueden corregir, y el borrado total no alcanza todavía a todo lo conversado | Arreglo en curso (toca memoria: aprobación humana) |
| PROM-10 | Podés hablarle | Demo, roadmap | No existe en la tesis (sección 10) | VOZ-01 a VOZ-09, o no mostrarla como función |
| PROM-11 | Plan Premium | Deck (`src/content/deck.ts:401,596`), paywall | Es una maqueta | Cobro real o presentarlo como futuro en todos lados |
| PROM-12 | «Lo sensible se borra solo a los 180 días» | Demo; Tú en web y móvil | La retención existe en el backend y tiene prueba, pero solo aplica a los momentos y depende de que corra el proceso periódico (INF-02) y de que la capa episódica se llene (CON-06) | Explicar qué cuenta como sensible, mostrar en el detalle cuándo se borra y dejar INF-02 andando |
| PROM-13 | Ynara cambia de modo sola | Demo, onboarding | **No existe.** El modo lo manda el cliente y no puede cambiar dentro de una sesión (el backend responde 409) | Construir el enrutador de modos, o reescribir la promesa como «vos elegís el modo»; define TES-10 |
| PROM-15 | Disponible en iOS y Android | `apps/landing/src/content/ynara.ts:33,189-195`, `layout.tsx:29,39` | El producto que existe es la web | Decir que hoy se usa desde el navegador y que las apps vienen después (AUD-ID) |
| PROM-16 | «Trabajando local» sin conexión | `apps/web/src/features/today/components/OfflineBanner.tsx:20-24` | Sin conexión nada funciona | Texto cierto y aviso cuando una acción falla (AUD-UX) |
| PROM-14 | «El reconocimiento correría en infraestructura propia» | Demo, ajustes de voz | Depende de VOZ-01 | Cumplirlo o reescribirlo |

Verificado en código o en la auditoría: PROM-01 a PROM-06, PROM-08, PROM-09,
PROM-11 a PROM-13, PROM-15 y PROM-16. PROM-03 aparece además en `IDENTITY.md`
y en el prompt del asistente. Inferido, hay que confirmarlo: PROM-07 y PROM-14.

---

## 12. Auditoría de seis áreas (2026-11-11)

Hecha con un workflow de nueve agentes de solo lectura: un auditor por área
(front-end, back-end, sistema de diseño, identidad, UX y UI) y tres verificadores
que intentaron refutar cada hallazgo contra el código. De
85 hallazgos, 1 se descartó en la verificación y 5 se fusionaron por repetidos entre
áreas. Quedan **55 ítems nuevos** y **24 correcciones a ítems que ya estaban**.

Límite del método: todo sale de leer código y documentos. No se levantó ninguna
app, no se corrieron lint, typecheck ni tests, y no se leyó ningún `.env`. Lo que
solo se confirma viendo la app andar está marcado «falta verlo en ejecución».

Los que piden cambios sobre tablas de memoria (borrado de cuenta, purga de
turnos, origen y confirmación de un recuerdo) **necesitan aprobación humana
antes de implementarse**.

Pendiente aparte, fuera de esta auditoría:

- [ ] **LOGO-01 · Unificar el logo.** El dibujo del isotipo ya es el mismo en
      todas las apps; lo que difiere es el aire alrededor y que el nombre se
      compone con texto en cada app. No existe ningún archivo del logo completo
      con su área de respeto (`DESIGN.md` §11.1). Armar un juego único (isotipo,
      horizontal y vertical; color, una tinta y negativo) y reemplazar las
      versiones de web, admin, landing, mobile y la presentación. Antes, decidir
      el diamante: violeta en la versión a color (lo que usan las apps y la
      presentación de julio) o blanco (la hoja de Illustrator del 3 de julio). [V]

### 12.1 Front-end

La web está razonablemente cerrada (estados de carga/error, tipos sin `any`, sin
tests salteados, rutas internas consistentes, los clientes solo llaman endpoints
que existen). Lo flojo está en mobile: hay pendientes de
preparación para release, no hay espera de hidratación del store seguro y las
preferencias de accesibilidad no se guardan ni
se aplican. En lo compartido hay lógica de streaming triplicada, un store de
chat que persiste por cada token y chips de acciones de la web que no
reconocen los nombres reales de las tools del backend.

- [ ] **AUD-FE-01 · Hallazgo de mobile.** (alta)
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **AUD-FE-02 · Mobile: preparar la configuración para el primer build de
      release.** (media)
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **AUD-FE-03 · Mobile: no se espera la hidratación del store seguro antes de
      decidir a dónde ir.** (media) Esperar en el layout raíz de mobile a que el
      store termine de hidratarse (mismo patrón que la web) antes de ocultar el
      splash y montar la navegación. [V, falta verlo en ejecución]
      Requiere verlo en ejecución para saber cada cuánto ocurre.
- [ ] **AUD-FE-04 · Mobile: las preferencias de accesibilidad no se guardan ni se
      aplican.** (media) Persistir el store en AsyncStorage y aplicar
      textSize/highContrast en el componente Text y en los tokens; mientras
      tanto ocultar esos controles. Aclarar en la sección 8.1 que el tilde vale
      solo para web. [V]
      Evidencia: apps/mobile/src/stores/a11y.ts:4-8;
      apps/mobile/src/features/profile/TuScreen.tsx:66-71;
      docs/planning/PENDIENTES-2026-11.md:354
- [ ] **AUD-FE-05 · El store de chat persiste todo el historial por cada token y no
      tiene tope.** (media) Persistir solo al cerrar el stream (o con
      escritura diferida), poner tope de sesiones y mensajes guardados y
      envolver la escritura en try/catch. [V, falta verlo en ejecución]
      El impacto en fluidez requiere verlo en ejecución con historial largo.
- [ ] **AUD-FE-06 · Lógica de streaming del chat triplicada entre web, mobile y
      admin.** (media) Llevar a packages/core un runChatStream con fetch
      inyectable para web y mobile, usar el parser compartido en el admin y
      reemplazar apps/web/src/lib/chat.ts por imports desde core (incluido
      extractErrorCode). [V]
      Evidencia: apps/mobile/src/features/chat/useChatStream.ts:113,120,124;
      apps/admin/src/features/playground/hooks/usePlaygroundStream.ts:155-200;
      apps/web/src/lib/chat.ts:16-38;
      packages/core/src/features/chat/api.ts:14-32
- [ ] **AUD-FE-07 · Hallazgo de web/admin.** (media)
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **AUD-FE-08 · Cobertura de pruebas muy despareja: admin, mobile y packages/ui
      casi sin tests.** (media) Priorizar tests de los flujos de autenticación de mobile, del
      hook de stream de mobile (o el compartido), del login y el 401 del admin y
      un humo de Icon en packages/ui. Borrar o usar las carpetas tests/ vacías.
      [V]
      Evidencia: apps/admin/src/__tests__/ (3 archivos) y
      apps/admin/src/features/playground/hooks/usePlaygroundStream.test.ts;
      apps/mobile/src/features/{agenda,auth,chat}/*.test.ts;
      apps/web/tests/.gitkeep; apps/mobile/tests/.gitkeep
- [ ] **AUD-FE-09 · No hay reporte de errores en ninguna app, aunque los comentarios
      dicen que sí.** (baja) Decidir si la tesis lleva reporte de errores en los
      front; si no, corregir los comentarios y sacar las variables del
      .env.example. Agregar global-error.tsx y un error.tsx en onboarding. [V]
      Evidencia: apps/web/src/app/(app)/error.tsx:25;
      apps/admin/src/app/(panel)/error.tsx:16-17; apps/mobile/.env.example:17-18
- [ ] **AUD-FE-10 · Las páginas de prueba de la web entran al build de producción.**
      (baja) Devolver notFound() en esas páginas en producción, salvo que se
      necesiten para la regresión visual de UI-17, y dejarlo anotado. [V]

No se revisó en esta área:

- No corrí lint, typecheck ni tests: me basé en la línea de base de la sección 0
  del documento y en lectura de código.
- Nada se verificó en ejecución (regla de la auditoría): la frecuencia real del
  problema de hidratación en mobile y el impacto en fluidez de la persistencia
  por token quedan marcados como «requiere verlo en ejecución».
- apps/landing: solo revisión superficial (TODO, tipos, tests); el deck ya está
  cubierto en la sección 1 del documento.
- No comparé campo por campo los esquemas Zod de packages/shared-schemas contra
  los modelos Pydantic (CON-13 y CON-14 ya cubren los casos conocidos); solo
  verifiqué rutas y nombres de tools.
- Accesibilidad de la web a nivel de pantalla (orden de foco, lectores de
  pantalla): solo búsquedas por patrones (no encontré `div` con onClick ni
  Pressables sin rol, salvo WipeMemorySheet.tsx de mobile); falta una pasada
  manual.
- Vistas de Agenda y Memoria de la web en detalle (DayView, WeekView,
  MemoryDetail) y las pantallas internas del admin: no las leí completas.
- No leí ningún archivo .env (solo .env.example).

### 12.2 Back-end

El backend está más sólido que el resto del monorepo: autenticación con rotación
de refresh y revocación por familia, aislamiento por usuario en todos los
stores, límites de uso, cifrado por usuario, CI con Postgres real y piso de
cobertura 85%. Lo que falta está en los bordes: la memoria episódica depende de
un cierre de sesión que ningún cliente dispara, producción puede arrancar con
embeddings falsos sin avisar, y no existe borrado de cuenta. Además, tres
ítems inferidos del documento (PROM-12, PROM-13, TES-16) ya se pueden cerrar o
corregir leyendo el código.

- [ ] **AUD-BE-01 · Hallazgo de backend.** (alta)
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **AUD-BE-02 · Producción puede arrancar con embeddings y reranker falsos sin
      ningún aviso.** (alta) Agregar un fail-fast en Settings para production si
      embedding_backend es "fake" y decidir explícitamente el reranker (con
      Ollama no existe). [V]
      Evidencia: apps/backend/app/llm/clients/factory.py:56,109-116,129-136;
      apps/backend/app/core/config.py:132,141,335-396
- [ ] **AUD-BE-03 · Hallazgo de backend.** (media)
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **AUD-BE-04 · Sumar configuración de logs a la API.** (media) Agregar
      configuración de logging al arranque (formato estructurado, nivel por
      variable de entorno, identificador de pedido por middleware) respetando la
      regla de no loguear contenido. [V]
- [ ] **AUD-BE-05 · Hacer reproducible la imagen de producción y definir el paso de
      migraciones.** (media) Construir siempre desde el lockfile, fijar la
      versión de las herramientas de build y desplegar por SHA. Definir en el
      runbook (INF-08) el paso manual, con confirmación humana, de migración
      antes de levantar la API. [V]
- [ ] **AUD-BE-06 · Scripts operativos que son cáscaras: exportar datos, borrar
      memoria y sembrar la base no hacen nada.** (baja) Implementarlos contra
      /v1/memory/export y /v1/memory/wipe o borrarlos del repo, del Makefile y
      del README. El de reset es el más engañoso. Un seed real serviría para
      preparar la demo. [V]
      Evidencia: scripts/export-user-data.sh:16-22; scripts/reset-memory.sh:24-33;
      scripts/seed-db.sh:9-14; Makefile:128-135
- [ ] **AUD-BE-07 · .env.example del backend no lista todas las variables que el
      código lee.** (baja) Completar el ejemplo con todas las variables que lee
      el código; sumar el chequeo al doctor (CI-12). [V]

No se revisó en esta área:

- No se corrieron pytest, ruff ni `alembic check`: la regla era no instalar nada y
  esos comandos escriben cachés; sigue vigente CI-11. No se verificó si los
  modelos y las 15 migraciones están sincronizados.
- Archivos .env reales: no se leyeron por regla.
- Comportamiento en ejecución: si los logs de la app aparecen bajo gunicorn, y la
  calidad real de las respuestas con Ollama.
- Contenido de los prompts por modo contra TONE-OF-VOICE.md (VOZ-13) y la lógica
  interna de memory_engine.py, consolidation.py, decay.py y context.py: solo se
  revisó su cableado, no su corrección.
- Panel admin y stores secundarios: se revisaron por estructura, no en detalle.
- infra/vllm/start-vllm.sh y las units systemd de infra/prod: ya cubiertos por
  INF-04, no se re-auditaron.
- tests/e2e (Playwright) es de front; quedó fuera del foco de backend.

### 12.3 Diseño (sistema de diseño)

La web (apps/web) tiene una disciplina de tokens alta: cero hex fuera de
globals.css y del logo, reduced-motion con override y tema Noche completo en
superficies y tinta. El problema está en el sistema como sistema: los tokens
viven copiados en web, admin, landing y tres archivos de mobile, y ya
divergieron en valores (mobile y landing), en componentes (admin quedó atrás de
web) y en los presets de packages/ui, que nadie consume. Frente a la demo, las
distancias medibles son la escala tipográfica (8 utilities + 26 tamaños
arbitrarios contra 3 tamaños y 2 pesos), el ancho de columna (seis valores
sueltos contra 560 px) y el acento azul sin variante legible en Noche.

- [ ] **AUD-DS-01 · El azul de acento no tiene variante para Noche: links y anillo de
      foco quedan cerca de 2:1.** (alta) Agregar un token de acento para texto y
      foco re-declarado en html.theme-dark (web y admin) y migrar los links a
      ese token. Sumarlo a TES-05, que hoy solo contempla los colores de modo.
      [V]
      Evidencia: apps/web/src/app/globals.css:106 y 386-431 (sin --color-accent en
      Noche), 523-528 (foco). Texto en acento:
      apps/web/src/features/chat/components/Markdown.tsx:35;
      apps/admin/src/app/(panel)/page.tsx:126;
      apps/admin/src/features/moat/components/MoatScreen.tsx:89;
      apps/admin/src/features/overview/components/AuditPreview.tsx:75. Noche por
      defecto: apps/web/src/stores/theme.ts:25,
      apps/admin/src/stores/theme.ts:23. Contraste calculado: 2,08:1 y 1,88:1.
      Referencia: Ynara-demo/css/tokens.css:78 (--accent-text: #a9b9e6).
- [ ] **AUD-DS-02 · Mobile tiene otros valores de marca, no un espejo de web.**
      (media) Al hacer EST-01, generar los valores de mobile desde la misma
      fuente que web. Pasar los dos textos en ink-muted a ink-soft. Corregir
      DESIGN.md:169 dentro de EST-03/TES-01: el default real es Noche en web,
      admin y mobile (o decidir lo contrario y cambiar el código). [V]
      Evidencia: apps/mobile/src/global.css:8-42;
      apps/mobile/tailwind.config.js:72-80;
      apps/mobile/src/app/(tabs)/_layout.tsx:7,10;
      apps/mobile/src/features/chat/components/ActionCard.tsx:45;
      apps/mobile/src/features/chat/components/RecentsSheet.tsx:123;
      apps/web/src/app/globals.css:48-52 y 38-42. Tema por defecto:
      apps/mobile/src/app/_layout.tsx:24 y apps/web/src/stores/theme.ts:25
      (ambos Noche) contra DESIGN.md:169.
- [ ] **AUD-DS-03 · Los presets de motion de packages/ui no coinciden con los tokens y
      nadie los usa.** (media) Definir una sola tabla de duraciones (decidir 200
      o 250 para base junto con TES-01) y generar CSS y TS desde ahí, antes de
      TES-21. [V]
      Evidencia: packages/ui/src/motion/presets.ts:22-28;
      apps/web/src/app/globals.css:176-181; apps/admin/src/app/globals.css:101;
      apps/web/src/__tests__/ynara-ui-motion.test.ts;
      Ynara-demo/css/tokens.css:62-64.
- [ ] **AUD-DS-04 · La escala tipográfica se saltea con tamaños arbitrarios y cuatro
      pesos.** (media) Sumar a TES-01/TES-22 la limpieza concreta: reemplazar
      los text-[N] por utilities de la escala, eliminar texto bajo 12 px,
      unificar los rótulos en mayúsculas (o quitarlos, como pide la skill) y
      agregar un guard tipo gradient-guard que falle ante text-[..px] en
      components y features. [V]
      Evidencia: apps/web/src/components/AppNav.tsx:258 y :195;
      apps/web/src/features/agenda/components/MonthView.tsx:78,93;
      apps/web/src/features/today/components/AnticipationCard.tsx:60;
      HoyHub.tsx:47; CheckinSheet.tsx:77,119,137; RecapSheet.tsx:39,51;
      AvisosView.tsx:75; apps/web/src/app/globals.css:315-322 (.text-caption con
      tracking 0.06em); Ynara-demo/css/tokens.css:43-46; SKILL.md:51.
- [ ] **AUD-DS-05 · El ancho de la columna de contenido no es un token: seis valores
      distintos.** (baja) Crear un token de columna en web y usarlo en las
      vistas del grupo (app), como paso previo a TES-09: el cambio a 560 px pasa
      a ser una línea. [V]
      Evidencia: apps/web/src/features/today/components/HoyView.tsx:76;
      apps/web/src/features/memory/components/MemoryView.tsx:79;
      apps/web/src/features/agenda/components/AgendaView.tsx:106;
      apps/web/src/features/chat/components/ChatScreen.tsx:96;
      apps/web/src/features/onboarding/components/StepShell.tsx:67-68;
      apps/web/src/features/chat/components/EmptyConversation.tsx:27;
      apps/admin/src/app/globals.css:109-110; Ynara-demo/css/tokens.css:53.
- [ ] **AUD-DS-06 · Landing mantiene tokens de modo jade y ámbar y gradientes que
      DESIGN.md retiró.** (baja) Borrar de landing los tokens jade/ámbar y los
      gradientes por modo sin uso, corregir el brief (junto con DECK-25) y
      decidir si el degradado de texto de RevealText queda como excepción
      documentada de la landing. [V]
      Evidencia: apps/landing/src/app/globals.css:29-31, 43-49, 66-80, 83-101;
      apps/landing/REBUILD-BRIEF.md:78, 82-84;
      apps/landing/src/components/motion/RevealText.tsx:91; DESIGN.md:235,
      269-271.
- [ ] **AUD-DS-07 · Radios y sombras fuera de la escala en componentes de web.**
      (baja) Llevar esos radios a tokens (o sumar un --radius-2xl si el composer
      lo necesita) y resolver las dos sombras con shadow-soft/shadow-lifted. El
      recorte de espaciados entra en la pasada de TES-22. [V]
      Evidencia: apps/web/src/features/chat/components/ChatComposer.tsx:114;
      MessageBubble.tsx:82;
      apps/web/src/features/agenda/components/MonthView.tsx:78;
      apps/web/src/features/memory/components/SearchResultRow.tsx:34;
      MemoryDetailView.tsx:57;
      apps/web/src/features/agenda/components/EventFab.tsx:168;
      apps/web/src/features/today/components/AnticipationCard.tsx:34;
      apps/web/src/app/globals.css:150-154; DESIGN.md:374-380.
- [ ] **AUD-DS-08 · Tokens y utilities de gradiente sin uso en web y admin.** (baja)
      Corregir el comentario desactualizado, borrar .text-gradient-blue-base y
      los tokens de admin; si se eliminan también los de web hay que actualizar
      test-ds/page.tsx y globals.theme.test.ts en el mismo cambio. [V]
      Evidencia: apps/web/src/app/globals.css:84-87, 128-129, 341-353;
      apps/web/src/app/test-ds/page.tsx:33-34;
      apps/web/src/app/globals.theme.test.ts:113;
      apps/admin/src/app/globals.css:80-81; DESIGN.md:481-483.

No se revisó en esta área:

- Nada se vio en ejecución: los contrastes en Noche son cálculos a mano sobre los
  hex y hay que confirmarlos con una herramienta; el aspecto real de dots de
  modo, foco y overlay requiere verlo en ejecución.
- Ynara-demo/css/app.css y los JS de la demo no se leyeron en detalle; la
  comparación con la demo se hizo contra tokens.css y SKILL.md.
- Los CSS por lámina del deck y los shaders de landing (56 hex fuera de
  globals.css, varios off-palette como #692e87 o #232323) no se revisaron uno
  por uno; DECK-25 ya cubre parte.
- Componentes de apps/mobile más allá de tokens, tailwind.config, layouts y greps
  puntuales.
- No se corrieron lint, typecheck ni tests (gradient-guard, tabular-nums-guard).
- docs/brand/Ynara-Universo-de-Marca.html e IDENTITY.md no se contrastaron valor
  por valor contra los tokens.
- El uso de requestAnimationFrame sin chequeo de reduced-motion en DayView.tsx y
  ChatComposer.tsx no se abrió para confirmar si es animación o solo
  foco/scroll.

### 12.4 Identidad de marca

La voz rioplatense está bien sostenida en los textos de web, mobile y landing
(casi no hay tuteo en strings de interfaz), y la tipografía respeta Space
Grotesk + DM Sans en todas las apps. Los huecos de identidad están en otro lado:
la landing y el deck cuentan una marca distinta a IDENTITY.md (otros pilares,
otros colores por modo, un Bienestar que aconseja), el backend devuelve textos
sin tildes, con disculpa y de usted que llegan al usuario, y hay decisiones de
marca sin cerrar que se filtran al producto (género de Ynara, sección "Tú" en
una marca de voseo, tres taglines distintos). No encontré correos
transaccionales en el repo, así que no hay nada que auditar ahí.

- [ ] **AUD-ID-01 · La landing asigna los colores de modo distinto a DESIGN.md, web,
      admin, mobile y la demo.** (media) Alinear los acentos de la landing a
      DESIGN.md §3.5 (Memoria lavanda, Bienestar violeta) o documentar la
      excepción; resolverlo junto con DECK-25 y EST-01. [V]
      Evidencia: apps/landing/src/components/journey/VoiceChapter.tsx:15-20;
      apps/landing/src/components/journey/HorizontalModes.tsx:20-21;
      DESIGN.md:281-285; apps/web/src/app/globals.css:138-147;
      Ynara-demo/css/tokens.css:10-14; apps/landing/REBUILD-BRIEF.md:82-84.
- [ ] **AUD-ID-02 · La landing y el deck presentan pilares distintos a los de
      IDENTITY.md.** (media) Decisión humana (CODEOWNERS de IDENTITY.md): una
      sola versión de los pilares. O landing y deck adoptan los 4 de
      IDENTITY.md, o IDENTITY.md se actualiza. Resolverlo junto con DECK-05. [V]
      Evidencia: IDENTITY.md:18-29; apps/backend/app/llm/prompts/shared.py:26-34;
      apps/landing/src/content/ynara.ts:59-101 y statement/features de pricing
      (~156-164); apps/landing/src/content/deck.ts:277;
      packages/core/src/features/modes/index.ts:20-26. Relacionado: DECK-05.
- [ ] **AUD-ID-03 · El ejemplo de Bienestar de la landing rompe el tono que la marca
      define para ese modo.** (media) Reescribir el chat de Bienestar de la
      landing con una pregunta abierta en vez de un consejo y revisar las frases
      de «Aconseja» contra el «no es un coach» de IDENTITY.md (depende de la
      decisión sobre pilares). [V]
      Evidencia: apps/landing/src/content/ynara.ts:144-147 y 91-99;
      docs/product/TONE-OF-VOICE.md:38-40; IDENTITY.md:52.
- [ ] **AUD-ID-04 · El backend devuelve textos sin tildes, con disculpa y de usted, y
      la web los muestra tal cual.** (media) Corregir esos cuatro textos a voseo
      con tildes y sin disculpa (revisar los tests que comparen el string
      exacto), y que el cliente traduzca los detail de auth a copy propio en vez
      de mostrarlos crudos. Se puede resolver con CON-15 y VOZ-11. [V, falta
      verlo en ejecución]
      Evidencia: apps/backend/app/llm/router.py:112-115;
      apps/backend/app/llm/clients/resilient.py:60;
      apps/backend/app/api/v1/_http.py:28; apps/backend/app/core/deps.py:41;
      apps/web/src/features/onboarding/steps/AuthStep.tsx:301,305;
      docs/product/TONE-OF-VOICE.md:17-18. Si el fallback de router.py llega a
      pantalla en todos los casos requiere verlo en ejecución.
- [ ] **AUD-ID-05 · El género de Ynara no está definido y los textos lo mezclan.**
      (media) Sumar a IDENTITY.md una línea explícita sobre cómo habla Ynara de
      sí misma (femenino o formas sin género) y corregir chat-copy.ts:26. Entra
      en la sesión de identidad de VOZ-10. [V]
      Evidencia: packages/shared-schemas/src/chat-copy.ts:26;
      apps/backend/app/llm/prompts/shared.py:22 y 40; IDENTITY.md:9, 55-56;
      docs/product/TONE-OF-VOICE.md:47;
      apps/landing/src/components/journey/HorizontalModes.tsx:26.
- [ ] **AUD-ID-06 · La sección personal se llama «Tú» en una marca que define el
      voseo.** (media) Decidir el nombre antes de TES-09/TES-17 («Vos», «Cuenta»
      o similar). Si se mantiene «Tú» por decisión de marca, dejarlo escrito en
      TONE-OF-VOICE.md como excepción. [V]
      Evidencia: apps/web/src/components/nav-items.ts:24;
      apps/web/src/app/(app)/tu/page.tsx:5;
      apps/mobile/src/features/profile/TuScreen.tsx:179;
      apps/web/src/features/profile/components/TuView.tsx:301;
      apps/web/src/components/AppNav.tsx:307; Ynara-demo/js/main.js:91.
- [ ] **AUD-ID-07 · La landing promete solo iOS y Android, y el producto que existe es
      la web.** (media) Sumar una fila a la tabla de promesas (sección 11):
      decir que hoy se usa desde el navegador y que las apps vienen después,
      actualizar el dato estructurado y cambiar la frase del mail o armar una
      lista real. [V]
      Evidencia: apps/landing/src/content/ynara.ts:33, 189-190, 193, 195;
      apps/landing/src/app/layout.tsx:29, 39.
- [ ] **AUD-ID-08 · La app web no tiene metadatos para compartir ni color de tema, y
      hay tres taglines distintos.** (media) Elegir un tagline y un cierre
      oficiales, anotarlos en IDENTITY.md y usarlos en las tres apps; sumar a la
      web los datos para compartir y el theme-color por tema. [V]
      Evidencia: apps/web/src/app/layout.tsx:9-15; apps/web/public/;
      apps/web/src/config/site.ts:3; apps/landing/src/content/ynara.ts:10-11;
      apps/mobile/src/features/auth/WelcomeScreen.tsx:116.
- [ ] **AUD-ID-09 · TONE-OF-VOICE.md tiene tuteo y descuidos en sus propias reglas.**
      (baja) Corregir la línea 35 a voseo («Cuando explicás algo, dá un anclaje
      concreto y un ejemplo») por PR con la aprobación que pide el archivo. No
      tocar los identificadores de tono. [V]
      Evidencia: docs/product/TONE-OF-VOICE.md:35; ynara.config.json:17,23;
      docs/product/MODES.md:12-13; docs/product/TONE-OF-VOICE.md:68-69
      (aprobación requerida).
- [ ] **AUD-ID-10 · La imagen para compartir de la landing no usa la tipografía de
      marca.** (baja) Cargar Space Grotesk y DM Sans en el generador de la
      imagen para que título y tagline salgan con las fuentes de marca. [V,
      falta verlo en ejecución]
      Evidencia: apps/landing/src/app/opengraph-image.tsx:20 y 35-44. El resultado
      final requiere verlo en ejecución.
- [ ] **AUD-ID-11 · El panel admin mezcla inglés y español en la navegación y los
      títulos.** (baja) Al final, junto con TES-26: pasar las etiquetas a
      español («Resumen», «Registro de auditoría», «Estado del sistema») y sacar
      la exclamación. [V]
      Evidencia: apps/admin/src/components/shell/nav-items.ts:33, 48, 49, 51;
      apps/admin/src/app/(panel)/sistema/page.tsx:4;
      apps/admin/src/app/(panel)/audit/page.tsx:4;
      apps/admin/src/features/sharing/components/ShareTargetCard.tsx:47.

No se revisó en esta área:

- docs/brand/Ynara-Universo-de-Marca.html: solo se revisó por búsqueda de texto,
  no se leyó entero ni se vio renderizado; no pude contrastar el uso de
  diamante, nodo, vínculo y bifurcación contra la guía original.
- Uso de degradados en pantalla: me apoyé en que existe el test gradient-guard de
  web; no lo ejecuté ni revisé admin, mobile y landing archivo por archivo.
- apps/landing/src/content/deck.ts: solo se revisó por búsqueda; el deck ya tiene
  su sección (DECK-01 a DECK-27) y no lo releí completo.
- Textos de mobile y del onboarding: revisados por búsqueda de patrones de tuteo,
  emojis y frases genéricas, no pantalla por pantalla.
- Prompts por modo del backend (un archivo por modo): solo leí shared.py; la voz
  por modo queda cubierta por VOZ-13.
- Correos: no encontré plantillas ni envío de correo en el repo, así que no hay
  nada para auditar.
- Todo lo que depende de ver la app corriendo (contraste real, cómo se ve la
  imagen para compartir, qué texto de error llega a pantalla en cada caso): la
  tarea era solo lectura de código.
- README raíz: solo se buscaron las promesas; no se revisó su tono línea por
  línea.

### 12.5 UX (experiencia de uso)

Los recorridos principales (registrarse, conversar, agenda en web, ver y borrar
un recuerdo) se pueden completar, pero los de cuenta y datos tienen huecos
serios que el documento no lista: no se puede borrar la cuenta ni cambiar o
recuperar la contraseña, y el borrado total de memoria no alcanza a las
conversaciones.
Además hay varios lugares donde la interfaz afirma algo que no pasó (avisos
«resueltos por vos», acciones del chat mal rotuladas, «trabajando local» sin
conexión, enlace de recuperación «enviado»). Todo sale de lectura de código;
nada se comprobó con la app en ejecución.

- [ ] **AUD-UX-01 · No se puede borrar la cuenta ni cambiar contraseña o email.**
      (alta) Definir baja de cuenta (usuario, memoria, eventos, tareas,
      recordatorios, sesiones) y cambio de contraseña, con endpoint y pantalla
      en Tú. El borrado toca tablas de memoria: necesita aprobación humana antes
      de implementarse. Relacionarlo con PROM-08 («tus datos son tuyos»). [V]
      Evidencia: apps/backend/app/api/v1/auth.py:209,250,295,415,459;
      apps/backend/app/api/v1/users.py:21;
      apps/web/src/features/profile/components/TuView.tsx:445-451 (solo «Cerrar
      sesión»).
- [ ] **AUD-UX-02 · Las tareas solo se pueden tildar: no hay forma de crear, editar ni
      borrar un pendiente.** (media) Agregar alta rápida en Hoy y
      edición/borrado por fila, con POST y DELETE de tareas. Hoy un pendiente
      mal creado por el asistente (task.create_task) no se puede corregir ni
      sacar. [V]
      Evidencia: apps/backend/app/api/v1/tasks.py:74,123;
      packages/core/src/features/today/api.ts:45,89;
      apps/web/src/features/today/components/PriorityRow.tsx:28-44;
      apps/web/src/features/today/components/HoyEmptyState.tsx:28.
- [ ] **AUD-UX-03 · Volver al paso de cuenta después de registrarse deja un formulario
      de alta que ya no se puede completar.** (media) Si el borrador ya tiene
      sesión, que el paso de cuenta muestre «Ya entraste como …» con «Seguir», o
      no permitir volver a ese paso. Ofrecer también un camino claro a «Iniciar
      sesión». Resolverlo junto con UI-15. [V]
- [ ] **AUD-UX-04 · Las conversaciones no se pueden borrar ni nombrar, y se acumulan
      conversaciones vacías.** (media) Crear la sesión recién al enviar el
      primer mensaje y sumar borrar (con deshacer) y título por conversación.
      Enlazarlo con CON-06 y con TES-11 (campo global que abre chat con texto).
      [V]
      Evidencia: packages/core/src/features/chat/store.ts:68-138;
      apps/web/src/features/chat/components/ChatHome.tsx:27-30;
      apps/web/src/features/chat/useStartChat.ts:19-22;
      apps/web/src/features/chat/components/ChatHeader.tsx:27-31;
      apps/web/src/features/chat/components/SessionsList.tsx:29, :37-55;
      apps/backend/app/api/v1/sessions.py:82,125,158.
- [ ] **AUD-UX-05 · Chat: enlace a conversación inexistente sin aviso, «volver» que
      saca del chat y mensaje propio que desaparece al fallar.** (media) Mostrar
      un aviso en Hoy cuando la conversación no existe en este dispositivo,
      llevar «volver» a /chat y conservar visible el texto del usuario junto al
      error, con opción de editar antes de reintentar. [V]
      Evidencia: apps/web/src/app/(app)/chat/[sessionId]/ChatRoute.tsx:11-12, :30;
      apps/web/src/features/chat/components/ChatHeader.tsx:35-37;
      apps/web/src/features/chat/components/MessageBubble.tsx:41-53 y :73;
      packages/core/src/features/chat/store.ts:392-400.
- [ ] **AUD-UX-06 · Buscar sugiere términos de un dataset de prueba que un usuario
      real no tiene.** (media) Reemplazar las sugerencias fijas por temas reales
      del usuario o quitarlas con mocks apagados, y agregar «Reintentar» al
      error. Encaja en TES-20/UI-08. [V]
      Evidencia: apps/web/src/features/memory/components/BuscarView.tsx:13-14 y
      :124-128; apps/mobile/src/features/memory/BuscarScreen.tsx:12-13.
- [ ] **AUD-UX-07 · Sin conexión la app dice «trabajando local» pero nada funciona
      local, y hay fallas que no avisan.** (media) Cambiar el texto a algo
      cierto y mostrarlo en todo el shell. Agregar un aviso corto cuando falla
      mover un evento o tildar una tarea. Es además una promesa a sumar en
      PROM-00. [V, falta verlo en ejecución]
      Evidencia: apps/web/src/features/today/components/OfflineBanner.tsx:20-24, :36;
      apps/web/src/app/providers.tsx:17-25;
      apps/web/src/features/agenda/components/DayView.tsx:345-346;
      packages/core/src/features/agenda/api.ts:67-78;
      packages/core/src/features/today/api.ts:106-108. El comportamiento visual
      del evento al fallar requiere verlo en ejecución.
- [ ] **AUD-UX-08 · En web, si falla el cálculo previo al borrado total, el diálogo
      queda mudo y sin salida.** (baja) Mostrar el error del preview con
      «Reintentar», igual que en móvil, y ocultar o deshabilitar el campo de
      confirmación mientras no haya conteos. [V]
      Evidencia: apps/web/src/features/profile/components/WipeMemoryDialog.tsx:55-60,
      :74, :111, :122-140; contraste:
      apps/mobile/src/features/profile/components/WipeMemorySheet.tsx:117-130.
- [ ] **AUD-UX-09 · Lo que se cuenta en los primeros pasos no se puede ver ni cambiar
      después.** (baja) Decidir si el paso de modos se mantiene (hoy solo fija
      el modo inicial). Si queda, dar en Tú un lugar para revisar modos y «sobre
      vos». Resolverlo dentro de TES-17 y TES-19. [V]
      Evidencia: apps/web/src/features/onboarding/constants.ts:20-31;
      apps/web/src/features/profile/components/TuView.tsx:321-346;
      apps/web/src/hooks/useActiveMode.ts:25-31;
      apps/web/src/components/AppNav.tsx:199;
      apps/web/src/features/today/components/CheckinSheet.tsx (único otro uso de
      mood).

No se revisó en esta área:

- Nada se comprobó con la app en ejecución (regla de la auditoría): los efectos
  visuales descritos (salto del evento al fallar el arrastre, burbuja de error
  que tapa el mensaje) salen de leer el código.
- Los pasos del onboarding de la app móvil (apps/mobile/src/features/onboarding) y
  las pantallas Hoy y Agenda de móvil se revisaron solo por búsquedas puntuales,
  no archivo por archivo.
- apps/admin y apps/landing quedaron fuera: el foco eran los recorridos del
  usuario en web y móvil.
- La demo (Ynara-demo) se consultó solo en la tabla de navegación de su guía
  (SKILL.md:141-145); no se recorrieron sus pantallas para comparar una por una.
- No se corrieron lint, typecheck ni tests: no hacían falta para estos hallazgos.
- No se verificó si cambiar el plazo de retención se aplica a recuerdos ya
  guardados ni si el proceso periódico de borrado corre en algún entorno.

### 12.6 UI (interfaz)

La base de componentes web está cuidada (tokens, foco global, reduced-motion,
botones de 44 px), pero la interfaz se deshilacha en los bordes: controles
secundarios hechos a mano con zonas de toque de 24 a 36 px, tres umbrales
distintos de "escritorio" (640/768/1024) frente a los 900 px de la demo, y una
Agenda en grilla que corta los títulos. La app móvil es la más floja: el chat
muestra el markdown crudo, usa glifos y figuras geométricas en vez del set de
íconos, tiene colores hardcodeados con contraste bajo, solo tema oscuro y un
orden de pestañas distinto al de la web. Nada de esto está detallado en
PENDIENTES; TES-22, TES-18 y TES-08 lo rozan pero con descripciones genéricas o
inexactas.

- [ ] **AUD-UI-01 · El chat móvil muestra el markdown crudo y difiere de la web en
      burbuja, campo de escritura y botones.** (media) Sumar a TES-24 un ítem de
      paridad del chat móvil: renderizar markdown en la respuesta (lo más
      visible) y alinear burbuja, campo, placeholder y botón enviar/detener con
      la web o con la demo. [V]
      Evidencia: apps/mobile/src/features/chat/components/MessageBubble.tsx:20-21,
      :64, :86-92;
      apps/web/src/features/chat/components/MessageBubble.tsx:82-88, :115;
      apps/mobile/src/features/chat/components/ChatComposer.tsx:37, :43, :53-55,
      :66, :70; apps/web/src/features/chat/components/ChatComposer.tsx:114,
      :128, :148-164.
- [ ] **AUD-UI-02 · El campo de escritura del chat no tiene ningún indicador de foco,
      y los campos móviles no tienen estado de foco.** (media) Decisión humana:
      dar foco visible al contenedor del campo (focus-within que suba el borde)
      en lugar de anularlo, y agregar estado de foco a TextField, Textarea y
      campo del chat en móvil. Anotarlo como excepción explícita dentro de
      TES-22. [V, falta verlo en ejecución]
      Evidencia: apps/web/src/app/globals.css:534-541;
      apps/web/src/features/chat/components/ChatComposer.tsx:113-119, :132-139;
      apps/mobile/src/components/ui/TextField.tsx:19, :31-37. Cómo se percibe el
      foco con teclado requiere verlo en ejecución.
- [ ] **AUD-UI-03 · No existe variante destructiva de botón: 'Borrar para siempre' se
      vuelve azul al pasar el mouse y 'Sí, eliminar' es un primario azul.**
      (media) Agregar una variante «danger» al Button (dentro de EST-02, para
      web, admin y móvil) con sus estados y usarla en borrar memoria y eliminar
      evento; cerrar sesión puede quedar como secundario. [V, falta verlo en
      ejecución]
      Evidencia: apps/web/src/components/ui/Button.tsx:4, :33-34;
      apps/web/src/features/profile/components/WipeMemoryDialog.tsx:167-176;
      apps/web/src/features/agenda/components/EventEditSheet.tsx:245-253;
      apps/web/src/features/profile/components/LogoutDialog.tsx:24. El color
      efectivo en hover requiere verlo en ejecución.
- [ ] **AUD-UI-04 · Tres umbrales distintos de 'escritorio' (640, 768 y 1024 px)
      frente a los 900 px de la demo.** (media) Definir un solo corte
      móvil/escritorio como token (900 px si se adopta TES-01), usarlo en
      navegación, hojas y Agenda, y sumarlo como criterio de TES-09 con prueba
      en 640, 768 y 900 px. [V, falta verlo en ejecución]
      Evidencia: apps/web/src/components/AppNav.tsx:61, :146;
      apps/web/src/features/agenda/components/AgendaView.tsx:35;
      apps/web/src/components/ui/Sheet.tsx:89-94;
      Ynara-demo/.claude/skills/ynara-interfaz/SKILL.md:142. El aspecto entre
      768 y 1023 px requiere verlo en ejecución.
- [ ] **AUD-UI-05 · Agenda en grilla: los títulos se cortan en Semana y Mes porque la
      columna nunca supera 720 px.** (media) Atarlo a TES-04: si sobreviven
      Semana y Mes, darles un ancho propio mayor al de lectura y en móvil
      mostrar puntos por evento; si se adopta la tira de siete días de la demo,
      eliminar estas vistas. [V, falta verlo en ejecución]
      Evidencia: apps/web/src/features/agenda/components/AgendaView.tsx:60-62, :106;
      apps/web/src/features/agenda/components/WeekView.tsx:56-57, :64, :81;
      apps/web/src/features/agenda/components/MonthView.tsx:78. Medidas exactas:
      requiere verlo en ejecución.
- [ ] **AUD-UI-06 · Móvil: colores hardcodeados con contraste insuficiente en
      placeholders y pestañas inactivas.** (media) Centralizar placeholder y
      tintes de pestaña en tokens.ts con el valor de --ink-soft (#aeb4c9),
      reemplazar las cuatro copias y corregir el comentario. Encaja en EST-01.
      [V]
      Evidencia: apps/mobile/src/components/ui/TextField.tsx:14-15;
      apps/mobile/src/components/ui/Textarea.tsx:13;
      apps/mobile/src/features/memory/BuscarScreen.tsx:17;
      apps/mobile/src/features/chat/components/ChatComposer.tsx:15;
      apps/mobile/src/lib/tokens.ts:20-21;
      apps/mobile/src/app/(tabs)/_layout.tsx:8; apps/mobile/src/global.css:10.
- [ ] **AUD-UI-07 · Móvil no usa el set de íconos: pestañas dibujadas con figuras
      geométricas y glifos de texto como íconos.** (media) Agregar a packages/ui
      un renderer de íconos para React Native sobre el mismo registry y
      reemplazar figuras y glifos; en web cambiar «‹ ›» por el ícono chevron.
      Sumarlo a TES-08 y TES-24. Instalar la dependencia requiere aprobación.
      [V]
      Evidencia: apps/mobile/src/app/(tabs)/_layout.tsx:18-22, :41-52;
      packages/ui/src/icons/Icon.tsx:38-41, :53;
      apps/mobile/src/features/chat/components/ChatComposer.tsx:70;
      apps/mobile/src/features/chat/components/MessageBubble.tsx:91;
      apps/web/src/features/agenda/components/AgendaView.tsx:136, :155;
      apps/web/src/components/nav-items.ts:8-9.
- [ ] **AUD-UI-08 · La app móvil solo tiene tema oscuro y su tema claro, inalcanzable,
      usa blanco en vez de marfil.** (media) Agregar a TES-24: activar el tema
      claro en móvil siguiendo al sistema y con selector en Tú, revisar las
      superficies #ffffff contra el marfil de la demo, y tomar los tintes de la
      barra de pestañas de tokens por tema. [V]
      Evidencia: apps/mobile/src/global.css:6-8, :25-29;
      apps/mobile/src/app/_layout.tsx:24; apps/mobile/app.json:14;
      apps/mobile/src/app/(tabs)/_layout.tsx:7-11;
      apps/web/src/components/AppNav.tsx:24-27.
- [ ] **AUD-UI-09 · El orden de las pestañas no coincide entre web y móvil.** (baja)
      Unificar el orden en una sola definición compartida y resolverlo junto con
      TES-09 y TES-24 (Hoy, Chat, Agenda, Memoria). [V]
      Evidencia: apps/web/src/components/nav-items.ts:20-25;
      apps/mobile/src/app/(tabs)/_layout.tsx:14-15 y orden de Tabs.Screen en
      :37, :55, :87, :108.
- [ ] **AUD-UI-10 · Controles equivalentes con diseños distintos y estado 'cargando'
      resuelto solo cambiando el texto.** (baja) Dentro de EST-02: un solo
      Toggle (con tamaño compacto), un solo ChipGroup y una prop loading en
      Button que mantenga el ancho, muestre indicador y marque aria-busy;
      aplicarlo también a móvil y admin. [V]
      Evidencia: apps/web/src/components/ui/Toggle.tsx:31-33;
      apps/web/src/features/chat/components/ReasoningToggle.tsx:20-36;
      apps/web/src/components/ui/ChipGroup.tsx:111;
      apps/web/src/features/agenda/components/EventEditSheet.tsx:161-166, :252,
      :268; apps/web/src/components/AppNav.tsx:96-118;
      apps/web/src/components/ui/Button.tsx:6-11;
      apps/web/src/features/onboarding/steps/AuthStep.tsx:88.

No se revisó en esta área:

- Nada se vio en ejecución (regla de la auditoría): contrastes reales sobre el
  fondo vivo, brusquedad de animaciones, cortes de texto exactos y el
  comportamiento entre 640 y 900 px están inferidos del código y marcados como
  'requiere verlo en ejecución' donde corresponde.
- apps/admin se revisó solo en estructura (Sidebar, Button, grillas responsive);
  no se auditaron sus gráficos, tablas ni el playground pantalla por pantalla.
- apps/landing y el deck quedaron fuera del foco (ya cubiertos por la sección DECK
  del documento).
- En apps/mobile no se leyeron en detalle onboarding, Tú, Memoria, Hoy ni las
  hojas (BottomSheet, RecentsSheet); solo primitivos, pestañas y chat.
- En apps/web no se leyeron en detalle HoyView, TuView, MemoryView, DayView,
  ListView, EventFab, onboarding ni LivingField; los ítems UI-01 a UI-10 no se
  re-verificaron uno por uno.
- CI-10 (tokens --color-overlay y --color-error-soft sin variante Noche) no se
  comprobó: no se leyó el bloque html.theme-dark completo de globals.css.
- No se corrieron lint, typecheck ni tests; la auditoría fue solo lectura de
  archivos.
- El color base del botón 'Borrar para siempre' depende de cómo resuelve
  conflictos apps/web/src/lib/cn.ts, que no se leyó; el paso a azul en
  hover/presionado sí surge directo de las clases.

### 12.7 Correcciones a ítems que ya estaban

La descripción original queda en su sección; esto es lo que hay que cambiarle.
`ya_resuelto` no apareció ninguno: nada de lo listado antes está terminado.

| ID | Área | Qué cambia | Qué hacer | Evidencia |
|---|---|---|---|---|
| CI-10 | DS | CI-10 está a medias: --color-error-soft ya tiene variante Noche, --color-overlay no | Actualizar CI-10: sacar error-soft (ya resuelto), dejar overlay y pasarlo a [V]. Agregar la variante Noche del overlay en web y admin. | apps/web/src/app/globals.css:120, 420-421; apps/admin/src/app/globals.css:68, 171; apps/web/src/components/ui/Sheet.tsx:86; apps/admin/src/components/ui/Sheet.tsx:76; Ynara-demo/css/tokens.css:80. El efecto visual del overlay en Noche requiere verlo en ejecución. |
| CON-02 | FE | CON-02 está incompleto: el admin ya tiene resuelto el 401 y web y mobile no | Ampliar CON-02: el admin ya maneja el 401; llevar el mismo patrón a web y mobile. El refresh token puede ir en segunda etapa. | — |
| CON-06 | BE | La memoria episódica nunca se genera en la práctica: depende de un cierre de sesión que nadie dispara | Ampliar CON-06: la memoria episódica depende de un cierre de sesión que hoy no ocurre. Agregar un job que cierre sesiones inactivas (tabla operativa sessions) y encole la consolidación, o que el cliente llame a close. No toca tablas de memoria. | apps/backend/app/api/v1/sessions.py:200-221; apps/backend/app/workers/celery_app.py:102-130 |
| CON-08 | UX | Los avisos dicen «Lo dejé resuelto por vos» aunque ningún botón hace nada | Ampliar CON-08: además de no tener endpoint, la pantalla afirma acciones que no ocurrieron. Mientras sea maqueta, que aceptar abra el chat con el pedido precargado y que descartar diga «Descartado», con deshacer. Ver también VOZ-11. | apps/web/src/features/today/components/AnticipationCard.tsx:82-104; apps/web/src/features/today/components/AvisosView.tsx:66 y :104-106; apps/web/src/features/today/avisosStore.ts:10-13. |
| CON-11 | UX | Recuperar contraseña: en web no existe y en móvil dice que mandó un enlace que nunca sale | Ampliar CON-11: no hay recuperación en ninguna de las dos apps y móvil afirma un envío que no ocurre. Hasta que exista el endpoint, sacar el enlace en móvil o decir la verdad; sumar el recorrido en web cuando se implemente. | apps/mobile/src/features/auth/ForgotPasswordScreen.tsx:10-13 (comentario), :34-41 (mensaje), :59-63 (setSent sin llamada); apps/mobile/src/features/auth/WelcomeScreen.tsx:153-158; apps/backend/app/api/v1/auth.py sin endpoint de reset. |
| CON-21 | FE | CON-21 tiene más casos: comentarios que contradicen al backend y datos del onboarding guardados sin uso | Sumar estos tres archivos a CON-21 y evaluar dejar de persistir en el cliente los campos de sobre-vos que ninguna pantalla lee. | packages/core/src/features/onboarding/completion.ts:15-17,68-70,91-92; apps/backend/app/api/v1/onboarding.py:86; apps/backend/app/services/onboarding_seed.py:84-112; packages/core/src/stores/createUserStore.ts:31-33; apps/web/src/features/chat/components/MessageActions.tsx:44-46 |
| EST-01 | DS | EST-01 subestima las copias: mobile son tres archivos y web/admin ya no tienen el mismo set | Reescribir EST-01 con el alcance real: todas las categorías de token (color, radios, motion, tipografía, sombras, z-index), los tres archivos de mobile y la lista de diferencias web/admin a reconciliar antes de extraer el paquete. | apps/mobile/tailwind.config.js:55-89; apps/mobile/src/global.css:8-42; apps/mobile/src/lib/tokens.ts:12-21; apps/web/src/app/globals.css:170-173; apps/admin/src/app/globals.css:91-96, 217-218, 232-233. |
| EST-02 | DS | EST-02 habla de copias, pero los primitivos de admin ya divergieron y perdieron arreglos de web | Corregir EST-02: no es mover copias idénticas sino unificar versiones tomando web como base (7 archivos divergen, contando YnaraWordmark). Mientras tanto, portar a admin el botón de cierre del Sheet, el fix de ProgressDots y el de focus-visible. | apps/admin/src/components/ui/Sheet.tsx (sin aria-label Cerrar) vs apps/web/src/components/ui/Sheet.tsx:128; apps/admin/src/components/ui/ProgressDots.tsx:46 vs apps/web/src/components/ui/ProgressDots.tsx:52; apps/admin/src/app/globals.css:236-240 vs apps/web/src/app/globals.css:523-532; DESIGN.md:534. |
| EST-05 | FE | EST-05 está mal descrito: el registro sí está mockeado; lo que falta es el cierre del onboarding | Reescribir EST-05 (o fusionarlo en CON-20): el registro con mocks funciona; lo que falta es el handler de /v1/onboarding y el de logout. Agregar esos dos y borrar signup/login. | apps/web/src/lib/api.mocks.ts:42,64,94,128,154; packages/core/src/features/onboarding/completion.ts:94-96; apps/web/src/app/providers.tsx:101. Que el último paso se trabe sin backend requiere verlo en ejecución. |
| PROM-03 | ID | PROM-03 («modelos propios») está también en IDENTITY.md y en el prompt del asistente, no solo en el README | Ampliar PROM-03 con IDENTITY.md y el prompt. El cambio en IDENTITY.md requiere PR con sus CODEOWNERS; el del prompt impacta tests/llm/test_prompts.py. | IDENTITY.md:12-13, 53-54; apps/backend/app/llm/prompts/shared.py:39; README.md:16; apps/backend/AGENTS.md:25. |
| PROM-09 | BE | El borrado total no alcanza todavía a todo lo conversado | Marcar PROM-09 como verificado e incumplido. Detalle reservado: se gestiona por el canal privado de `SECURITY.md`. | reservada |
| PROM-09 | UX | «Corregir u olvidar cualquier recuerdo» no se cumple: solo se edita una capa y el borrado total deja las conversaciones | Reescribir PROM-09: el borrado de las 3 capas es real, pero solo los hechos se pueden corregir y el borrado total no alcanza todavía a todo lo conversado. Decidir con aprobación humana (toca memoria) cómo se completa o si el diálogo lo aclara. | reservada |
| PROM-12 | BE | PROM-12: la retención de 180 días para lo sensible sí existe en el backend | Pasar PROM-12 a "verificado en código", condicionado a que el beat corra en producción (INF-02) y a que la capa episódica se llene (CON-06). Aclarar que aplica a los momentos, no a hechos ni preferencias. | apps/backend/app/memory/config.py:96-97; apps/backend/app/models/user.py:34-43; apps/backend/app/workers/celery_app.py:118-121; apps/backend/tests/workflows/test_episodic_retention.py |
| PROM-12 | UX | La retención de «recuerdos sensibles» está en el producto, pero el usuario no sabe cuáles son ni cuándo vencen | Actualizar PROM-12: la retención existe en backend y la promesa está en Tú de web y móvil. Para cerrarla, explicar qué cuenta como sensible (lo conversado en Bienestar), mostrar en el detalle cuándo se borra y atarla a INF-02, porque sin el proceso periódico el borrado no ocurre. | apps/web/src/features/profile/components/TuView.tsx:385-397; apps/mobile/src/features/profile/TuScreen.tsx:207-211; apps/backend/app/workflows/consolidation.py:423-428; packages/core/src/features/memory/detail-presenter.ts:54-56. |
| PROM-13 | BE | PROM-13: el backend no cambia de modo solo; el modo lo manda el cliente y no puede cambiar dentro de una sesión | Pasar PROM-13 a "verificado: no existe". O se construye un enrutador de modos y se levanta el 409, o se reescribe la promesa como "vos elegís el modo". Afecta a TES-10. | apps/backend/app/llm/router.py:187-198; apps/backend/app/schemas/chat.py:58; apps/backend/app/api/v1/_sessions.py:53-57 |
| TES-06 | DS | TES-06: ya existe un componente Diamond en web y admin | Aclarar en TES-06 que el diamante con halo y cinco estados debe extender o reemplazar Diamond y YnaraOrb (y migrar sus cinco consumidores), para no terminar con tres componentes de diamante. | apps/web/src/components/ui/Diamond.tsx; apps/admin/src/components/ui/Diamond.tsx; usos en apps/web/src/components/ui/ModeSheet.tsx, features/memory/components/SearchResultRow.tsx, TimelineEntryRow.tsx, features/profile/components/PaywallSheet.tsx, features/today/components/RecapSheet.tsx; apps/web/src/components/ui/YnaraOrb.tsx:7. |
| TES-08 | UI | TES-08 describe mal el trazo de los íconos: hoy se dibujan más finos que 1,5 px, no más gruesos | Reescribir TES-08: el trazo real es ~0,9–1,4 px según tamaño; para 1,5 px reales calcular strokeWidth = 1,5 × 44 / size (o non-scaling-stroke), no variar el grosor en activo y pasar el ojo de contraseña al registry. | packages/ui/src/icons/Icon.tsx:4, :39-40, :46; apps/web/src/components/AppNav.tsx:78, :180-184; apps/web/src/components/ui/TextField.tsx:104-116. |
| TES-15 | BE | TES-15: confirmado que la memoria no guarda origen ni confirmación de cada recuerdo | Pasar TES-15 de inferido a "verificado: falta". Los vínculos por similitud se pueden calcular al vuelo sin migración; el campo de origen y la confirmación tocan tablas de memoria y requieren aprobación humana y migración. | apps/backend/app/models/memory.py:86; apps/backend/app/api/v1/memory.py:160,183,211,258,289,315,350 |
| TES-16 | BE | TES-16 / PROM-05: la respuesta del chat no devuelve qué recuerdos se usaron | Marcar TES-16 como verificado: falta backend. Exponer los IDs de los recuerdos recuperados en la respuesta y en el evento done del stream, y actualizar el schema Zod y ENDPOINTS.md. | apps/backend/app/schemas/chat.py:75-85 |
| TES-16 | UX | Las acciones que Ynara ejecuta en el chat se muestran con rótulos equivocados y sin detalle ni deshacer | Sumar a TES-16 un mapa de rótulos por nombre real de herramienta, compartido en packages/core, con el dato concreto y enlace a lo creado, y refresco de Agenda y Hoy tras una acción. Las cuatro herramientas de lectura (memory.search, calendar.list_events, task.list_tasks, reminder.list) no deben mostrarse como acción hecha. | apps/web/src/features/chat/components/MessageActions.tsx:18-39 y :44-46; apps/backend/app/llm/tools/memory.py:154,192,236,274; task.py:91,114; reminder.py:90,113; calendar.py:101,124; apps/backend/app/llm/tool_loop.py:202-211 (toda llamada va a actions); apps/mobile/src/features/chat/components/ActionCard.tsx:15-27. |
| TES-18 | UI | Las hojas no tienen animación de salida y usan la misma entrada en móvil y escritorio | Ampliar TES-18: agregar salida (inversa a la entrada) antes de cerrar el <dialog>, fundido del fondo y entrada distinta por ancho. Va junto con DEMO-07 (el mismo «aparece desde abajo» repetido). | apps/web/src/components/ui/Sheet.tsx:52-54, :88-93; apps/web/src/styles/motion.css:3-12, :110-125; apps/admin/src/components/ui/Sheet.tsx:78-83. La brusquedad del cierre requiere verlo en ejecución. |
| TES-22 | UI | TES-22 nombra las zonas de toque de 44 px sin inventario: hay al menos siete controles web por debajo | Reemplazar la mención genérica de TES-22 por esta lista y resolverla con min-h/min-w de 44 px o área extendida; en PriorityRow hacer que toda la fila alterne el check. | apps/web/src/features/today/components/PriorityRow.tsx:37; apps/web/src/components/ui/Toggle.tsx:31; apps/web/src/features/chat/components/ReasoningToggle.tsx:24; apps/web/src/features/chat/components/ChatHeader.tsx:55; apps/web/src/features/agenda/components/EventEditSheet.tsx:165; apps/web/src/components/ui/Button.tsx:40; apps/web/src/features/agenda/components/WeekView.tsx:52. Medidas reales: requiere verlo en ejecución. |
| TES-24 | FE | TES-24 se puede concretar: mobile está fijado en oscuro y no usa la iconografía compartida | Reemplazar "No se revisó" por puntos concretos: tema claro + selector, íconos desde packages/ui, Memoria como destino de primer nivel y pantalla de error/no encontrado. | apps/mobile/src/app/_layout.tsx:21-24; apps/mobile/src/app/(tabs)/_layout.tsx:7-11,14-23,41-84; listado de apps/mobile/src/app sin +not-found |
| VOZ-12 | ID | VOZ-12 ubica mal el meta-relato «Pasé a modo X»: está en la demo, no en la app | Separar VOZ-12 en dos: quitar el emoji del paywall (app) y definir cómo se avisa el cambio de modo sin meta-narración antes de TES-10; lo segundo va a la lista DEMO. | Ynara-demo/js/convo.js:115; Ynara-demo/js/voice.js:187; apps/web/src/features/profile/components/PaywallSheet.tsx:45. |

---

## 13. Salida al mercado (2026-11-11)

El checklist de las secciones 1 a 12 deja un producto de tesis sólido. Para una beta cerrada
y para abrirlo a cualquiera faltan cuatro cosas:
- cobrar de verdad, con **Mercado Pago**;
- correr en una **VPS con GPU dedicada**, con deploy completo;
- cumplir con lo legal;
- medir que la IA funciona bien, primero con usuarios reales en una beta.

Esta sección va después de la defensa. Amplía la sección 1 (bloqueantes) y la hoja de ruta de
`AUDITORIA-PRODUCTO-MERCADO-2026-11.md` (entre M-01 y M-71), y reemplaza la topología de ADR-027.
Se aparta de la «arquitectura mínima» de esa auditoría, que dejaba la inferencia en la PC hasta
los 1.000 usuarios: el ADR de VPS-01 justifica el costo con la estimación de VPS-02, y VPS-12
la confirma o reabre la decisión.

### 13.1 Infraestructura: VPS con GPU dedicada

- [ ] **VPS-01 · ADR nuevo de topología.**
  - Reemplaza ADR-027: deja la PC hogareña y pasa a una VPS con GPU dedicada (no
    compartida).
  - Decide qué corre en la VPS: backend, workers, Redis, inferencia y la web. La base se
    decide en VPS-06.
  - Una GPU alquilada no es «on-prem»: el ADR define cómo se dice ahora y actualiza la
    regla #4 de `AGENTS.md`, el `README.md` y PROM-02 (archivos de raíz: OK humano).
  - Actualiza `deploy-topology.md`, `DEPLOY.md` y `SECURITY.md` (DOC-03).
- [ ] **VPS-02 · Elegir proveedor.** Comparar al menos tres opciones según:
  - GPU dedicada de 24 GB o más (el modelo actual pide 16 GB como mínimo);
  - región con baja latencia para Argentina;
  - costo mensual y por hora;
  - ancho de banda;
  - snapshots y backups;
  - términos de privacidad.

  Dejar la comparación y la elección en el ADR.
- [ ] **VPS-03 · Aprovisionamiento reproducible.** El servidor se arma desde cero con un
      script o con infraestructura como código: usuarios, firewall, actualizaciones
      automáticas de seguridad, drivers de la GPU y runtime de contenedores. Incluye el Celery
      beat desplegado y monitoreado (INF-02).
      Junto con VPS-07 cierra M-28; junto con VPS-05, VPS-06 y VPS-09 cierra INF-03.
- [ ] **VPS-04 · Serving de modelos en la VPS.**
  - Ollama o vLLM (INF-04). La decisión actualiza ADR-014.
  - Modelos con versiones y alias fijados, y parámetros de contexto, permanencia y
    paralelismo versionados.
  - Degradación cuando falla un servicio auxiliar.
  - Precarga al arrancar y prueba de humo después de cada deploy.
  - Cierra M-39, M-40 y M-49.
- [ ] **VPS-05 · Dominio y TLS.**
  - Dominio propio para la API y la web, con certificados automáticos.
  - Ingress propio en lugar del túnel.
  - Orígenes permitidos con los dominios de producción.
  - Deploy de la landing en el dominio propio.
  - Junto con VPS-14 (buzón) cierra M-70.
- [ ] **VPS-06 · Base de datos.** Decidir entre seguir en Supabase (con recuperación a un
      punto en el tiempo y pooler) o Postgres con pgvector en la VPS. Si se muda, plan de
      migración de datos sin pérdida, ensayado antes en staging. Mueve tablas sagradas:
      tests y aprobación humana (regla #3). La decisión actualiza ADR-005.
- [ ] **VPS-07 · Deploy completo por CI.**
  - Merge a `main` → build de imágenes → migraciones → deploy → prueba de humo.
  - Rollback en un paso.
  - Sin pasos manuales.
  - Cierra INF-01, e INF-07 si VPS-01 decide mudar la web.
- [ ] **VPS-08 · Entorno de staging.** Una copia chica del entorno (puede ser sin GPU, con
      modelos de prueba) donde se prueba cada deploy y la migración de la base antes de
      producción.
- [ ] **VPS-09 · Backups y restauración.** Backups cifrados fuera de la VPS, con retención
      definida y una restauración completa ensayada y documentada. Custodia y rotación de la
      clave de cifrado. Cierra M-29 y M-30.
- [ ] **VPS-10 · Observabilidad y alertas.**
  - Métricas de API, workers y GPU.
  - Monitoreo de disponibilidad desde afuera.
  - Alertas que le lleguen a una persona.
  - Página de estado pública.
  - Primero cierra M-52 (que no se envíe contenido del usuario) y recién después suma
    observabilidad en los clientes.
  - Cierra M-33 y M-35, y se apoya en AUD-BE-04 y AUD-FE-09.
- [ ] **VPS-11 · Capacidad medida.**
  - Gestión de capacidad con respuesta honesta cuando la GPU está ocupada.
  - Prueba de carga con usuarios simultáneos.
  - Definir cuántos usuarios aguanta un servidor y cuándo se suma otro.
  - Cierra M-36 a M-38 (detalle reservado).
- [ ] **VPS-12 · Costo por usuario.** Planilla con el costo mensual de la VPS, la base, el
      email, Mercado Pago y el dominio, dividido por usuarios activos. Es la base del precio
      (COB-01).
- [ ] **VPS-13 · Runbook de operación.**
  - Qué hacer si se cae la GPU, la base, el disco o la VPS entera.
  - Objetivo de tiempo de recuperación (con una sola VPS sigue habiendo un punto único de
    falla, M-27).
  - Cómo escalar, quién responde y en cuánto tiempo.
  - Cierra INF-08.
- [ ] **VPS-14 · Email transaccional.** Proveedor y dominio verificados para la recuperación
      de contraseña, la verificación de email, los recibos y los avisos. Cierra M-03 y
      destraba M-04 y CON-11.

### 13.2 Cobro con Mercado Pago

- [ ] **COB-01 · Planes y precio.**
  - Especificar el plan gratuito y el Premium en un documento de producto: qué incluye
    cada uno y qué límites tiene.
  - El precio final en pesos, con impuestos incluidos, se fija después de VPS-11 y VPS-12.
  - Reemplaza los valores sueltos de hoy (M-26, PROM-11).
- [ ] **COB-02 · Integración con Mercado Pago.**
  - Suscripciones con cobro recurrente usando la API de suscripciones de Mercado Pago.
  - Credenciales de prueba y de producción separadas, sin entrar nunca al repo.
  - Junto con COB-03 y COB-08 cierra M-23.
- [ ] **COB-03 · Webhooks confiables.** Verificar la firma de cada notificación,
      procesarlas de forma idempotente y reconciliar con la API ante cualquier duda. El
      estado de la suscripción nunca se decide desde el cliente.
- [ ] **COB-04 · Plan y estado de suscripción en el modelo de usuario.** Cubre alta,
      período de prueba si lo hay, pago al día, pago fallido con período de gracia,
      cancelada y vencida. Lleva migración de Alembic y aprobación humana. Cierra M-24.
- [ ] **COB-05 · Límites por plan.** Cuotas de uso aplicadas en el backend según el plan,
      con mensajes claros cuando se alcanzan. Cierra M-25.
- [ ] **COB-06 · Pantallas de cobro.**
  - Elegir plan, pagar, ver el estado y el historial, y cambiar el medio de pago.
  - Reemplaza el paywall de maqueta (CON-10, M-26).
  - La baja y el arrepentimiento siguen COB-07 y no dependen de estas pantallas.
- [ ] **COB-07 · Requisitos de consumo en Argentina (Disposición 954/2025).**
  - Botón de arrepentimiento y «BOTÓN DE BAJA DE SERVICIO», a simple vista desde el
    primer acceso a la web y a la app, sin pedir registración ni trámites adicionales.
  - Código de identificación del trámite en 24 horas.
  - 10 días corridos para arrepentirse.
  - Precio final informado.
  - Avisar antes de cada renovación es una buena práctica, a confirmar con LEY-01.
  - Todo se valida con un abogado (LEY-01). Junto con COB-08 cierra M-17.
- [ ] **COB-08 · Facturación.** Emitir factura electrónica por cada cobro, con un servicio
      de facturación integrado o un proceso definido con el contador, y definir la razón
      social que figura en los términos (M-07).
- [ ] **COB-09 · Pruebas de punta a punta.**
  - Con el sandbox de Mercado Pago: alta, renovación, pago rechazado, cancelación,
    arrepentimiento y reintento del webhook.
  - Tests automáticos para los estados de COB-04.
- [ ] **COB-10 · Mobile.** Si las apps de las tiendas venden Premium, las reglas de Apple
      y Google pueden exigir sus propios sistemas de pago. Decidir antes de publicar:
      vender solo en la web o integrar las compras dentro de la app.
- [ ] **COB-11 · Pagos en el admin.** Vista de suscripciones, pagos, bajas y
      arrepentimientos para poder atenderlos. Cierra M-67.

### 13.3 Legal y privacidad

- [ ] **LEY-01 · Revisión con un abogado.**
  - Términos, política de privacidad, consentimiento para datos sensibles, requisitos de
    consumo y licencias de uso comercial de los modelos.
  - Tratamiento de datos personales según la Ley 25.326, incluido si corresponde inscribir
    la base ante el organismo de control.
- [ ] **LEY-02 · Términos y Política de Privacidad publicados.**
  - En web, landing y mobile.
  - El registro pide aceptarlos y guarda la versión aceptada.
  - Cierra M-07 y M-08.
- [ ] **LEY-03 · Consentimiento expreso para Bienestar.** Antes de guardar contenido
      emocional, con registro del consentimiento y forma de retirarlo. Cierra M-09.
- [ ] **LEY-04 · Edad mínima.** Definirla y pedirla en el registro. Cierra M-10.
- [ ] **LEY-05 · Proveedores que tratan datos.** Lista pública de quién procesa qué
      (VPS, base, web, email, observabilidad y Mercado Pago) y en qué país, con sus
      acuerdos de tratamiento de datos, transferencias internacionales e inscripción ante
      el organismo de control si corresponde. Cierra M-15.
- [ ] **LEY-06 · Aviso de IA.** Ynara es una IA, no un profesional de la salud: decirlo en
      el onboarding, en Bienestar y en el chat. Cierra M-11.
- [ ] **LEY-07 · Derechos del titular.** Borrar la cuenta, borrar todo y exportar todo,
      funcionando de punta a punta (se apoya en AUD-UX-01, PROM-08 y PROM-09). Cierra M-01,
      M-12 y M-14.

### 13.4 Seguridad del usuario y calidad de la IA

- [ ] **EVA-01 · Protocolo de crisis en todos los modos.**
  - Detección en el código y respuesta fija.
  - Recursos de ayuda por país, verificados con fuente oficial y visibles en la interfaz.
  - Sin instrucciones contradictorias.
  - Una prueba que confirme que el bloque de crisis nunca queda fuera del contexto.
  - Cierra M-18 a M-20. **Bloquea la beta.**
- [ ] **EVA-02 · Sistema de evaluación.**
  - Datasets y conjuntos de referencia para memoria, tool-calling y respuestas por modo,
    con una regresión que corra antes de cambiar un modelo o un prompt.
  - Cierra M-42 y corre los experimentos de la sección 6 de la auditoría de mercado.
- [ ] **EVA-03 · Reportar una respuesta.** Canal por mensaje para marcar una respuesta
      mala o peligrosa, con revisión desde el admin. Cierra M-21.
- [ ] **EVA-04 · Calidad de la memoria sobre EVA-02.** Medir y arreglar lo que la
      evaluación muestre de la consolidación y la extracción de memoria. Cierra M-43 a M-47.
      Toca tablas de memoria: aprobación humana.
- [ ] **EVA-05 · Confirmación en acciones sobre la memoria.** El asistente pide
      confirmación antes de borrar o cambiar recuerdos. Cierra M-22.

### 13.5 Beta cerrada

- [ ] **BETA-01 · Plan de beta.**
  - De 10 a 30 personas durante 3 a 6 semanas, con consentimiento informado.
  - Métricas definidas antes de empezar: uso diario, retención a 7 y 30 días, recuerdos
    corregidos, reportes y tiempo de respuesta.
  - Criterios de salida escritos.
  - La medición usa analítica con consentimiento (M-71).
- [ ] **BETA-02 · Correr la beta sobre la VPS.** Con cobro en sandbox o gratis, alertas
      activas y una persona de guardia. Requiere VPS-07, VPS-10, VPS-11 y todo el paso 4
      de 13.6.
- [ ] **BETA-03 · Informe de la beta.** Qué se usó, qué no, qué falló y qué se cambia antes
      de abrir. Decide si se lanza.
- [ ] **REV-01 · Revisión de seguridad externa.** Antes de abrir el registro al público,
      una persona o empresa de afuera intenta romperlo. Los hallazgos van por el canal
      privado de `SECURITY.md`.
- [ ] **SOP-01 · Soporte.** Un canal de contacto, respuestas a las preguntas frecuentes y
      un tiempo de respuesta comprometido. Cierra M-58.

### 13.6 Orden

1. **Después de la defensa:** VPS-01, VPS-02 y COB-01 (planes y límites, todavía sin
   precio). Son decisiones y destraban todo lo demás.
2. **VPS-03 a VPS-10 y VPS-14**, con el sistema andando en staging y en producción.
3. **VPS-11 a VPS-13**: capacidad, costo y runbook. Con eso se fija el precio de COB-01.
4. **Lo mínimo para invitar personas:** EVA-01, EVA-03, EVA-05, LEY-01 a LEY-07 y BETA-01.
5. **BETA-02**, con EVA-02 y EVA-04 durante la beta. En paralelo, COB-02 a COB-09 y COB-11
   en sandbox.
6. **Antes de abrir:** REV-01, BETA-03, COB-10 y SOP-01. Después, la apertura con
   cobro real.

---

## Por dónde empezar (actualizado el 2026-11-11)

**Primero, lo más urgente que encontró la sección 12** (datos y cosas que la
interfaz afirma y no pasan):

- AUD-FE-01: detalle reservado.
- AUD-BE-01: detalle reservado.
- AUD-BE-02: producción puede arrancar con embeddings falsos sin avisar.
- PROM-09: el borrado total no alcanza todavía a todo lo conversado.
- CON-06: la memoria episódica nunca se genera porque nadie cierra la sesión.
- AUD-UX-01 y CON-11: no se puede borrar la cuenta ni recuperar la contraseña,
  y móvil dice que mandó un enlace que no sale.
- CON-08 y TES-16: avisos «resueltos por vos» y acciones del chat mal rotuladas.
- AUD-DS-01: en Noche, enlaces y foco en azul quedan cerca de 2:1 de contraste.

Después, el orden que ya estaba:

1. **Promesas que se arreglan con texto** (PROM-02, PROM-03, PROM-05, PROM-06,
   PROM-11): son cambios chicos de copy y bajan el riesgo legal de inmediato.
   Antes, PROM-00 para no dejar ninguna afuera.
2. **Decisiones de TES-01 a TES-04:** sin ellas no conviene tocar pantallas.
3. **Base compartida** (EST-01, EST-02, TES-05, TES-06): tokens y componentes una
   sola vez.
4. **Estructura** (TES-09 a TES-12) y después **Hoy** (TES-13), que es lo que más
   cambia la percepción del producto.
5. **ADR de voz** (VOZ-01) en paralelo: define si la voz entra a la tesis o queda
   como demostración.
6. **PROM-01** (recordatorios que llegan): es la promesa central y la más cara.

## Orden sugerido

Lo de la sección 8 va después de la defensa: UI-01 a UI-05 y UI-11 a UI-13 son
los que más cambian la percepción del producto; EST-01 y EST-02 conviene
hacerlos antes para no rediseñar dos veces.

1. **Antes de la defensa:** DECK-01 a DECK-05, DECK-14, DECK-17 y DECK-20.
2. **Para una demo en vivo confiable:** CON-01, CON-02, INF-02.
3. **Para cerrar el producto:** CON-04 a CON-06 (lo que el asistente hace y el
   usuario no ve) y CON-03.
4. **Higiene:** CI-01, CI-02, DOC-01 a DOC-11.
5. **Después:** el resto de INF, BE y las maquetas de la sección 2.3.
6. **Después de la defensa:** la sección 13, en el orden de 13.6.
