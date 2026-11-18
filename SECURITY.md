# SECURITY.md — Política de seguridad de Ynara

## Reportar una vulnerabilidad

Si encontraste una vulnerabilidad, **no la reportes en un issue
público**. Usá la funcionalidad **Private Vulnerability Reporting
(PVR)** de GitHub, que es la versión privada y trackeable del flow
de issues para reportes de seguridad.

### Cómo reportar

1. Andá a
   [github.com/bygama/Ynara/security/advisories](https://github.com/bygama/Ynara/security/advisories/new).
2. Click en **"Report a vulnerability"**.
3. Completá:
   - Descripción de la vulnerabilidad.
   - Pasos para reproducirla.
   - Impacto estimado (qué datos / qué función queda expuesta).
   - Si conocés una mitigación, sugerirla.

Te respondemos dentro de **72 horas hábiles**. Si la vulnerabilidad
es crítica (data exfiltration, RCE, auth bypass), la prioridad
salta a inmediata.

> Si PVR no aparece habilitado, pedir a un admin del repo que lo
> active en `Settings → Code security and analysis → Private
> vulnerability reporting → Enable`.

### Issues normales

Para bugs no-sensibles, features, o cualquier otro tipo de tema
público, usar
[github.com/bygama/Ynara/issues](https://github.com/bygama/Ynara/issues)
con el template correspondiente.

## Principios

Ynara maneja datos personales sensibles (memoria conversacional,
agenda, recordatorios). La política de seguridad refleja eso:

1. **Datos del usuario y proveedores de IA** (regla #4 de `AGENTS.md`).
   - **Fuera de los logs (4a).** Nunca contenido del usuario (mensajes,
     memoria, agenda) ni PII en logs, mensajes de error, métricas ni
     Sentry. Los datos del usuario no van a terceros (APIs externas,
     analytics, servicios de IA), salvo a los proveedores de IA de abajo
     y solo para la inferencia, y a cada llamada va solo lo que esa
     llamada necesita.
   - **IA por API (4b).** Ynara responde con modelos de lenguaje de
     proveedores externos, consumidos por API: el chat, el agente, la
     consolidación de memoria y los embeddings
     ([ADR-028](./docs/architecture/adrs/ADR-028-inferencia-por-api.md)).
     El único lugar del backend que habla con ellos es
     `apps/backend/app/llm/clients/providers/`; web, mobile, admin y
     landing nunca llaman a una API de IA. Solo se trabaja con
     proveedores con contrato de tratamiento de datos (DPA y cláusulas
     modelo de la AAIP, Ley 25.326) y que no entrenen con los datos, y
     la lista de proveedores se publica en la política de privacidad
     antes de que alguno reciba datos reales. Hasta cerrar las
     compuertas de ADR-028, los proveedores reciben solo datos
     sintéticos.

2. **Secrets nunca en el código.** Variables de entorno solo.
   `.env*` está gitignored. Si detectás un secret commiteado:
   rotación inmediata + reporte. Las credenciales de los proveedores de
   IA (`LLM_API_KEY_<NAME>` y `EMBEDDING_API_KEY`) también son secretos
   de entorno: nunca van en el código, en `ynara.config.json` ni en
   logs, y se rotan sin tocar el código
   ([RUNBOOK](./docs/operations/RUNBOOK.md#rotar-la-credencial-de-un-proveedor-de-ia)).

3. **Autorización en FastAPI, no en la DB.** Durante fase MVP usamos
   Supabase como Postgres gestionado, pero prohibido usar Supabase
   RLS como mecanismo de autorización primaria. La autorización vive
   en la API (regla #5 de `AGENTS.md`).

4. **Tablas de memoria son sagradas.** `semantic_memory`,
   `episodic_memory`, `procedural_memory` y el audit trail inmutable
   `audit_log` requieren tests + 1 aprobación humana explícita para
   cualquier migración (regla #3 de `AGENTS.md`).

5. **JWT firmado con secret rotable.** El access token expira en 7 días
   por defecto (`JWT_EXPIRE_MINUTES`); el refresh token en 30 días
   (`JWT_REFRESH_EXPIRE_MINUTES`). El refresh es **single-use con
   reuse-detection a nivel familia** (claim `sid`): un retry dentro de la
   ventana de gracia (`AUTH_REFRESH_REUSE_GRACE_SECONDS`, 30s) es idempotente
   (converge en el sucesor canónico, no revoca nada); un reuse fuera del grace
   es un **breach** → revoca la **familia entera** (todos los access + refresh
   de esa sesión) → 401. `/v1/auth/logout` revoca la **sesión completa**
   (familia vía `sid`: refresh + access hermanos), además de los `jti`
   individuales (compat pre-#142). La revocación es **fail-open**: si Redis cae,
   degrada al baseline JWT-stateless (el token vale hasta su `exp`).

6. **TLS end-to-end.** En producción, tráfico Cloudflare Tunnel →
   FastAPI siempre cifrado. Del backend a los proveedores de IA, solo
   HTTPS: la configuración rechaza una URL de proveedor sin `https`
   (salvo loopback, para desarrollo). En dev local, HTTPS con mkcert.

7. **Backups cifrados.** pg_dump de la DB con cifrado AES-256 antes
   de subir a R2.

8. **Audit log.** Cada acceso a memoria del usuario queda registrado
   con timestamp, modo y origen (modelo o tool).

## Datos personales y borrado

El usuario tiene derecho a:

- Exportar toda su memoria y conversaciones: `GET /v1/memory/export`
  (devuelve las 3 capas descifradas con su propio JWT).
- Borrar todo: `POST /v1/memory/wipe` (con `?dry_run=true` previsualiza el
  recount sin borrar; el execute destructivo exige el confirm per-layer).
- Pausar la memoria semántica de forma temporal.

> Los scripts `scripts/export-user-data.sh` y `scripts/reset-memory.sh`
> son **placeholders** (hoy `exit 1` / sin implementar): la superficie real
> es la API de FastAPI de arriba.

Detalle de implementación en
[`docs/product/MEMORY.md`](./docs/product/MEMORY.md).

## Dependencias

- Cualquier dependencia nueva pasa por review humano.
- Auditoría automática de dependencias del backend con `uv run pip-audit`,
  disparada en cada PR/push que toca `apps/backend/**` (CI de backend,
  `.github/workflows/ci.yml`); falla el build ante una CVE/PYSEC conocida.
  El frontend todavía no tiene auditoría de dependencias automatizada.
- Pin de versiones obligatorio para dependencias críticas (auth,
  crypto, ORM).

## Modelo de amenaza (resumen)

<!-- TODO: completar threat model formal cuando armemos el doc -->

Adversarios considerados:

- Atacante externo intentando acceso a memoria de usuarios.
- Insider con acceso a la VPS de producción.
- Cliente comprometido (token robado).
- Credencial de un proveedor de IA filtrada. Es un secreto de entorno
  (principio 2) que no está en el código ni en los logs; si se expone,
  se revoca en el proveedor y se reemplaza sin desplegar código.
  Mientras tanto, el chat pasa al proveedor de respaldo, o responde
  degradado si no hay otro.

Datos en tránsito hacia los proveedores de IA:

- Lo que se manda a inferir y a embeddear (el mensaje, el contexto de
  memoria del turno, el texto que se consolida) viaja por HTTPS, y el
  proveedor lo procesa en claro. El cifrado de la memoria
  ([ADR-007](./docs/architecture/adrs/ADR-007-memory-decay-retention-encryption.md))
  protege la base, no ese procesamiento: ahí el límite es contractual
  (contrato de tratamiento de datos, sin entrenamiento con los datos) y
  de minimización (a cada llamada va solo lo que esa llamada necesita).
- Hasta cerrar las compuertas de
  [ADR-028](./docs/architecture/adrs/ADR-028-inferencia-por-api.md),
  los proveedores reciben solo datos sintéticos.

No considerados (por ahora):

- Análisis side-channel de la inferencia.
- Ataques a la infraestructura interna de los proveedores de IA: quedan
  del lado del proveedor y del contrato con él, no de un control técnico
  de Ynara.
