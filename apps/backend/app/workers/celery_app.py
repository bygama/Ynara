"""Instancia de Celery + autodiscovery de tasks + beat schedule.

POLITICA DE FIABILIDAD (explicita, no defaults implicitos): la cola corre
tasks que escriben en tablas SAGRADAS (``consolidate_turn`` aplica ops via
``apply_ops``; ``decay_procedural`` hace UPDATE/DELETE sobre
``procedural_memory``). El default global es **AT-MOST-ONCE**: con
``task_acks_late=False`` el broker ackea ANTES de ejecutar, asi un crash del
worker NO reencola y NO reejecuta. Una task que no es idempotente no puede
correr dos veces sin duplicar lo que escribe.

Las dos tasks de consolidacion (``consolidate_turn`` y ``consolidate_session``)
son la excepcion: optan por **AT-LEAST-ONCE** (``acks_late=True`` por task) y
reintentan las fallas transitorias del proveedor con backoff y jitter. Pueden
porque re-ejecutarlas no duplica: cada intento es todo-o-nada, el turno queda
marcado como consolidado en la misma transaccion que escribe la memoria y el
episodio de una sesion es unico. Opciones y razones en
``_CONSOLIDATION_TASK_OPTIONS`` (``app/workflows/consolidation.py``).
"""

from __future__ import annotations

from datetime import timedelta

from celery import Celery

from app.core.config import get_settings
from app.memory.config import MemoryConfigError, load_decay_config, load_retention_config

# Cadencia del decay procedural (M8 Ola 3, ADR-007 D1). Se lee del loader de
# ``ynara.config.json[memory]`` (#211) en vez de importarlo de
# ``app.workflows.decay`` para evitar el ciclo de import (ese modulo importa
# ``celery_app`` de aqui). ``load_decay_config()`` es default-safe cuando el
# bloque ``[memory]`` no trae el threshold (cae al default de ADR-007 D1), pero
# AUN puede levantar ``MemoryConfigError`` si ``ynara.config.json`` falta, no es
# JSON valido o trae un valor invalido. Como esto corre en IMPORT-TIME, una
# excepcion aca tumbaria el worker ANTES de registrar las tasks. Por eso se
# envuelve en try/except con fallback al default literal (14 dias): el job nunca
# tumba el worker por un config ausente/invalido (la red final del task Celery
# sigue siendo su propio try/except). El override del operador via config se
# pierde si el config es invalido, pero el beat arranca igual.
_DECAY_INTERVAL_DAYS_FALLBACK = 14
try:
    _DECAY_INTERVAL_DAYS = load_decay_config().decay_interval_days
except MemoryConfigError:
    _DECAY_INTERVAL_DAYS = _DECAY_INTERVAL_DAYS_FALLBACK

# Cadencia (en dias) del worker de retention episodica. Mismo patron default-safe
# que el decay: se lee de ``ynara.config.json[memory].episodic_retention_interval_days``
# (default 1 = diario, configurable) y, si el config rompe en import-time
# (MemoryConfigError), cae al literal de fallback para NO tumbar el worker antes de
# registrar las tasks (la red final sigue siendo el try/except del propio task).
_EPISODIC_RETENTION_INTERVAL_DAYS_FALLBACK = 1
try:
    _EPISODIC_RETENTION_INTERVAL_DAYS = load_retention_config().episodic_retention_interval_days
except MemoryConfigError:
    _EPISODIC_RETENTION_INTERVAL_DAYS = _EPISODIC_RETENTION_INTERVAL_DAYS_FALLBACK

# El singleton de Celery se construye al importar (lo necesitan worker y beat).
# get_settings() (lru_cache) se llama inline en los args en vez de capturar un
# `settings` a nivel de módulo (convención del repo: no instanciar Settings global);
# las dos llamadas devuelven el mismo objeto cacheado.
celery_app = Celery(
    "ynara",
    broker=get_settings().celery_broker_url,
    backend=get_settings().celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Argentina/Buenos_Aires",
    enable_utc=True,
    task_track_started=True,
    # --- Politica de fiabilidad EXPLICITA (ver docstring del modulo) ---
    # AT-MOST-ONCE por default: ackear ANTES de ejecutar. Un crash NO reencola, asi
    # una task que no es idempotente no corre dos veces. Las de consolidacion lo
    # pisan con acks_late=True por task (idempotentes, ver el docstring).
    task_acks_late=False,
    # Solo surte efecto en las tasks con acks_late=True (hoy, las de consolidacion):
    # si el proceso hijo muere con el mensaje AUN NO ackeado (OOM, un kill), lo
    # rechaza y lo reencola en vez de perderlo. El corte por hard time limit no
    # cuenta: se ackea (acks_on_failure_or_timeout) y no vuelve a la cola. Un mensaje
    # que matara al proceso cada vez se reencolaria en loop; el payload de la
    # consolidacion es texto acotado, asi que ese caso no se espera. En las tasks con
    # early-ack el mensaje ya se ackeo y no hay nada que reencolar.
    task_reject_on_worker_lost=True,
    # Sin pipelining: un mensaje por worker a la vez (tasks pesadas, no rafagas).
    worker_prefetch_multiplier=1,
    # Limites de tiempo: soft (90s) lanza SoftTimeLimitExceeded; hard (120s) mata.
    # Las de consolidacion tienen los suyos (mas largos que el presupuesto del LLM).
    task_soft_time_limit=90,
    task_time_limit=120,
    # Redis visibility_timeout: cuanto puede quedar un mensaje entregado sin ack antes
    # de que el broker lo re-entregue a otro worker. Tiene que superar con margen al
    # countdown mas largo de un reintento (retry_backoff_max, 600s) mas el hard limit
    # de la task (210s): un reintento con ETA queda sin ack en el worker hasta que
    # corre y, con un techo menor, se re-entregaria y ejecutaria de nuevo en loop. Con
    # acks_late el mensaje tambien queda sin ack mientras la task corre. 3600s es el
    # default de Celery para Redis; el costo es que, si un worker entero cae, sus
    # mensajes sin ack tardan hasta una hora en volver a la cola.
    broker_transport_options={"visibility_timeout": 3600},
)

# Beat schedule. El decay procedural corre cada DECAY_INTERVAL_DAYS dias, NO
# diario: la regla es "confidence *= 0.9 por intervalo" y sin una columna
# last_decayed_at (que seria migracion sagrada) la cadencia por-intervalo es la
# unica forma de evitar el compounding dia a dia. Cada corrida decae las
# entradas no reforzadas en el ultimo intervalo (ADR-007 D1, decision M8 Ola 3).
celery_app.conf.beat_schedule = {
    "decay-procedural-every-interval": {
        "task": "workflows.decay_procedural",
        "schedule": timedelta(days=_DECAY_INTERVAL_DAYS),
    },
    # Retention del audit_log: borra entradas > 24 meses (AUDIT_RETENTION_DAYS).
    # Cadencia mensual: para una retention de 24 meses no hace falta correr a
    # diario; mensual mantiene la tabla acotada sin scheduling innecesario.
    "purge-audit-log-monthly": {
        "task": "workflows.purge_audit_log",
        "schedule": timedelta(days=30),
    },
    # Retention episodica (ADR-007 D2 / roadmap §5.3): borra episodios cuya ventana
    # (created_at + retention_days) ya vencio. Cadencia config-driven (default 1 =
    # diario); a diferencia del audit (mensual, 24m de retention) los episodios
    # sensibles vencen a 180d, asi que conviene una cadencia mas fina y configurable.
    "purge-episodic-memory-every-interval": {
        "task": "workflows.purge_episodic_memory",
        "schedule": timedelta(days=_EPISODIC_RETENTION_INTERVAL_DAYS),
    },
    # Despacho de recordatorios vencidos (PR-C): cada minuto busca los ``pending`` cuya
    # hora llegó (``remind_at <= now``) y los despacha (noop por ahora), marcándolos
    # ``sent``. Cadencia fina (1 min) porque un recordatorio debe avisar cerca de su hora,
    # a diferencia de la retention (diaria/mensual).
    "dispatch-due-reminders-every-minute": {
        "task": "workflows.dispatch_due_reminders",
        "schedule": timedelta(minutes=1),
    },
    # Cierre de sesiones de chat inactivas: cada 5 minutos cierra las que llevan
    # SESSION_IDLE_CLOSE_MINUTES (+ un margen) sin turnos y encola su consolidacion
    # episodica. Sin este cierre la memoria episodica no se generaba nunca.
    "close-idle-sessions-every-5-minutes": {
        "task": "workflows.close_idle_sessions",
        "schedule": timedelta(minutes=5),
    },
    # Retención de turnos crudos: una vez por día borra los turnos de las sesiones
    # cerradas con ended_at (su última actividad) de hace más de 8 días, que ya no se
    # van a consolidar (las consolidadas los purgan solas). Sin este job quedaban para
    # siempre.
    "purge-stale-turns-daily": {
        "task": "workflows.purge_stale_turns",
        "schedule": timedelta(days=1),
    },
}

# Autodiscovery de tasks en app.workflows.*. Hoy registra consolidation.py
# (consolidate_turn) y decay.py (decay_procedural); ambos ya existen. Los
# nuevos workflows que se agreguen al paquete se descubren solos.
celery_app.autodiscover_tasks(packages=["app.workflows"])
