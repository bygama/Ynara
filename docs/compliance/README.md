# docs/compliance/

Cumplimiento legal de Ynara: privacidad y protección de datos.

## Documentos

- [`compliance.pdf`](./compliance.pdf) — informe de compliance para
  LATAM (Argentina, México, Colombia y Chile) con el borrador de la
  política de privacidad y de los términos (mayo de 2026). Es un
  borrador técnico-legal para revisión de un abogado: no reemplaza
  asesoría legal.

## Leelo con ADR-028 al lado

El informe es anterior a
[ADR-028](../architecture/adrs/ADR-028-inferencia-por-api.md) y da
por hecho que la IA corre en infraestructura propia. Con ADR-028 el
chat, el agente, la consolidación y los embeddings llaman a
proveedores de IA por API, así que varias partes del PDF ya no
describen el sistema. Hasta que se rehaga, valen estas correcciones:

- **Con quién compartimos tus datos** (punto 5 del borrador de
  política). Dice que las conversaciones se procesan en
  infraestructura propia y que no van a servicios externos de IA. Ya
  no es así: el texto que se manda a inferir y a embeddear lo procesa
  el proveedor, y la política tiene que nombrar a cada uno.
- **Transferencias internacionales** (punto 1.9). Además del hosting
  de la base, mandarle datos a un proveedor del exterior es una
  transferencia internacional (art. 12 de la Ley 25.326). Se cubre con
  DPA y cláusulas modelo de la AAIP firmadas con cada proveedor, el de
  embeddings incluido. El argumento de la "infra propia" ya no aplica.
- **Entrenamiento y fine-tuning** (punto 4 del borrador, bases legales
  del punto 1.7 y orden de implementación). Hablan de entrenar modelos
  propios con datos de usuarios. Sin pesos propios no hay qué tunear;
  lo que hace falta es que ningún proveedor entrene con los datos, y
  eso va firmado en cada DPA.
- **Bienestar** (punto 3 del borrador). Sigue siendo el modo de mayor
  riesgo porque trata datos sensibles. Si el visto bueno legal objeta
  mandárselos a un proveedor, la salida que prevé ADR-028 es no
  guardar memoria de Bienestar.

Hasta cerrar las cinco compuertas de ADR-028 (legal, crisis, costo,
respaldo y latencia), los proveedores reciben solo datos sintéticos.
