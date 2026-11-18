# VISION.md — Visión de Ynara

<!-- TODO: refinar con el equipo en sesión de visión -->

## A 1 año (MVP → product-market fit)

Ynara como herramienta personal real: 500-1000 usuarios activos en
LATAM (arranque Argentina) que dependen de Ynara para productividad
+ memoria personal. Métricas eje:

- Retención semana 4 > 30%.
- Mensajes promedio por usuario activo / semana.
- % de acciones cerradas por tools sobre acciones intentadas.
- NPS interno > 40.

## A 3 años (escala)

Ynara como plataforma de asistencia personal en español. 50K-200K
usuarios activos. Tres planos:

1. **Producto**: app multi-modo con memoria propia, sin quedar atada a
   un proveedor de IA: los modelos se cambian por configuración
   ([ADR-028](../architecture/adrs/ADR-028-inferencia-por-api.md)).
2. **Plataforma**: tools de tercero pueden conectarse vía API
   controlada (calendar de terceros, etc.).
3. **Infra**: backend y datos en la infraestructura de Ynara (la base,
   self-hosted desde V2 según ADR-005) y la IA por API, con proveedores
   intercambiables; capacidad de offering enterprise bajo demanda (B2B,
   instituciones educativas).

## Ventaja defensiva

- **Modelos intercambiables**: la IA va por API detrás de dos alias, con
  una lista de proveedores y respaldo. Si un proveedor cambia precios,
  términos o modelos, se reemplaza por configuración, no por código
  ([ADR-028](../architecture/adrs/ADR-028-inferencia-por-api.md)).
- **Memoria propia**: el dato es del usuario y la memoria vive en la
  infraestructura de Ynara, con un engine in-house (ADR-010). Los
  gigantes tienen incentivos opuestos.
- **Tono y lengua**: rioplatense específico, no traducido. Difícil de
  copiar a escala global.
- **Producto, no modelo**: lo difícil de copiar es la memoria de 3
  capas con los modos y la voz encima; el modelo de lenguaje es
  intercambiable.

## Lo que **no** somos

- No somos un chat genérico con otra cara: usamos modelos de lenguaje
  de proveedores externos, pero el producto es la memoria, los modos y
  la voz.
- No somos un terapeuta. No queremos serlo.
- No somos un agente generalista que hace todo. Somos un asistente
  **personal**, con foco.

## Equipo

3 personas (@MateoGs013, @bygama, @querques20). No hay hardware de
inferencia: la IA se consume por API
([ADR-028](../architecture/adrs/ADR-028-inferencia-por-api.md)). Stack
TypeScript + Python.

## Riesgos identificados

- Dependencia de proveedores externos (disponibilidad, cuotas, precios,
  términos y retiro de modelos) → mitigación: lista de proveedores con
  respaldo y alias de modelo (ADR-028).
- Costo por token, que crece con el uso y se multiplica con el agente y
  la consolidación → mitigación: medirlo por tipo de llamada, con topes
  por usuario y alertas, antes de mandar datos reales (compuertas de
  ADR-028).
- Datos del usuario procesados por terceros (transferencia
  internacional, datos sensibles en Bienestar) → mitigación: contrato de
  tratamiento de datos y cláusulas modelo de la AAIP con cada proveedor,
  minimización y solo datos sintéticos hasta cerrar las compuertas de
  ADR-028.
- Costo de DevOps en V2 cuando se migra a self-hosted → mitigación:
  ADR-005 separa fases.
- Burnout del equipo chico → mitigación: scope acotado por modo,
  features se cierran completas antes de la siguiente.

## Open questions

<!-- TODO: respuestas con el equipo -->
- Modelo de negocio: freemium + paid tier vs solo paid.
- Aplicación enterprise (B2B educativo) — ¿desde cuándo?
- Open source de parte del stack — ¿qué partes y cuándo?
