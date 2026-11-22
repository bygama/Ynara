# ADR-029: La voz hablada se consume por API

## Estado

Propuesto

## Fecha

2026-11-22

## Contexto

Hablarle a Ynara es parte de la experiencia que el producto quiere ofrecer: el
prototipo navegable ([`docs/prototypes/demo/`](../../prototypes/demo/)) ya
muestra la pantalla de voz completa (cinco estados, subtítulos palabra por
palabra, interrupción, manos libres). Pero la demo la resuelve con la **Web
Speech API** del navegador, y ese camino no sirve para el producto:

- **El audio sale hacia el fabricante del navegador.** En Chrome, el
  reconocimiento puede enviar el audio a los servidores de Google, por fuera de
  todo control del backend. Contradice la regla #4a de `AGENTS.md` (los datos
  de usuario solo van a los proveedores de IA aprobados en 4b, y solo para la
  inferencia): el navegador no es un proveedor aprobado ni firmó nada.
- **No garantiza español rioplatense.** Las voces y el reconocimiento
  disponibles dependen del navegador y el sistema operativo de cada persona.
- **No hay infraestructura propia donde correrlo.**
  [ADR-028](./ADR-028-inferencia-por-api.md) eliminó la GPU propia y todo el
  serving local: montar STT/TTS on-prem revertiría esa decisión para un solo
  feature.

Quedaba decidir si la voz entra al producto de la tesis o queda como
demostración. La decisión de producto es que **entra**, con la misma
arquitectura que el resto de la IA.

## Decisión

### D1 — Reconocimiento y síntesis por API

El audio a texto (STT) y el texto a audio (TTS) se consumen por API de
proveedores externos, en **streaming** en ambos sentidos. Igual que en ADR-028:
nada de SDKs de proveedor; cliente HTTP genérico, proveedor definido por
configuración.

### D2 — El audio es efímero

El backend **no guarda audio**: el audio entrante se transcribe y se descarta;
el saliente se sintetiza y se descarta. Lo único que persiste es el **texto**
del turno, por el mismo camino que un mensaje escrito (cifrado, memoria,
consolidación). Sin archivos de audio en disco, en la base ni en logs.

### D3 — El único lugar que habla con proveedores sigue siendo `providers/`

La regla #4b de `AGENTS.md` cubre también el audio: solo
`apps/backend/app/llm/clients/providers/` habla HTTP con proveedores de voz.
Prohibido llamar APIs de voz desde los frontends — el navegador captura el
micrófono y streamea **al backend**, nunca directo a un tercero.

### D4 — Endpoints de audio en el backend

Dos endpoints en streaming (audio → texto y texto → audio), documentados en
`apps/backend/docs/ENDPOINTS.md` cuando se implementen. La pantalla de voz del
producto consume esos endpoints; la Web Speech API no se usa en el producto.

### D5 — Mismas compuertas que ADR-028

El proveedor de voz **todavía no está elegido**. Rigen las compuertas de la
regla #4b: para datos reales, solo proveedores con DPA y cláusulas modelo de la
AAIP firmadas (Ley 25.326) que no entrenen con los datos; hasta cerrar esa
elección, pruebas y evaluación solo con datos sintéticos. Puede ser el mismo
proveedor del LLM si cubre audio, o uno dedicado — se decide junto con la
elección de proveedor de ADR-028. Mientras tanto, la voz de la demo sigue
siendo una demostración y se presenta como tal.

## Consecuencias positivas

- La voz entra a la tesis con calidad real de STT/TTS en español, sin montar ni
  operar serving de audio propio.
- Una sola arquitectura de IA externa (ADR-028 + este ADR): mismas compuertas,
  misma configuración por entorno, mismo lugar único (`providers/`).
- La promesa de privacidad queda decible: el audio no se guarda y no pasa por
  el fabricante del navegador.

## Consecuencias negativas

- La voz queda bloqueada por la elección de proveedor (y su DPA): no se puede
  implementar end-to-end antes de esa decisión.
- Latencia de red en el loop hablar→escuchar; exige streaming bien hecho en los
  dos sentidos para que la conversación se sienta viva.
- Costo por minuto de audio que se suma al costo por token del LLM.
- Dependencia de un tercero también para la voz; si el proveedor de voz se cae,
  el producto degrada a solo texto.

## Alternativas descartadas

- **Web Speech API en el producto.** El audio viaja al fabricante del navegador
  (incompatible con la regla #4a) y las voces en español no están garantizadas.
  Queda solo en la demo, presentada como demostración.
- **STT/TTS en servidor propio.** Exigiría recuperar la GPU y la operación que
  ADR-028 acaba de eliminar (pesos, serving, una máquina prendida y expuesta),
  para un solo feature.
- **Dejar la voz fuera de la tesis.** Era la opción barata, pero la voz es
  parte central de la experiencia que la demo propone; se decidió cumplirla,
  no retirarla.
