import type { ReactNode } from "react";
import { Card } from "@/components/ui/Card";
import { EmptyStateCard } from "@/components/ui/EmptyStateCard";
import { cn } from "@/lib/cn";
import type {
  AiBackendT,
  AiStatusT,
  SystemAiT,
  SystemEmbeddingsT,
  SystemLlmProviderT,
} from "../schemas";

type Props = {
  /** Bloque `ai` de `GET /v1/admin/system`: el LLM (modo + proveedores) y los embeddings. */
  ai: SystemAiT;
  className?: string;
};

/**
 * Label y color de cada estado. Decisión de marca, igual que `StatusCard`: Operativo =
 * azul plano (sin verde), caído = `--color-error`, no en uso = `ink-soft`.
 */
const STATUS_META: Record<AiStatusT, { label: string; colorVar: string }> = {
  ok: { label: "Operativo", colorVar: "--color-blue-flat" },
  down: { label: "Caído", colorVar: "--color-error" },
  unconfigured: { label: "No en uso", colorVar: "--color-ink-soft" },
};

/**
 * Proveedores de IA del runtime (ADR-028): contra qué proveedores corre Ynara y si
 * responden.
 *
 * Una tarjeta con dos grupos. El LLM lleva un bloque por proveedor de
 * `LLM_PROVIDERS`, en su orden (el primero es el principal y el resto, el respaldo);
 * los embeddings, uno solo. Cada bloque muestra nombre, estado, host y modo; el del
 * LLM suma sus alias con el id de modelo de ese proveedor y el de embeddings, el
 * modelo y la dimensión del vector. Del backend llega solo el host de cada URL base,
 * nunca la URL entera ni la credencial.
 *
 * Con el backend `fake` el panel no sondea a nadie: el estado es "No en uso",
 * no Operativo. Los valores técnicos van con `tabular-nums`.
 */
export function AiProviders({ ai, className }: Props) {
  const { llm, embeddings } = ai;

  return (
    <Card className={cn("flex flex-col gap-6", className)}>
      <header className="flex flex-col gap-1">
        <p className="text-caption text-[var(--color-ink-soft)]">Inferencia por API</p>
        <h3 className="text-subtitle text-[var(--color-ink-deep)]">Proveedores de IA</h3>
      </header>

      <Group title="LLM">
        {llm.providers.length === 0 ? (
          <EmptyStateCard
            title="Sin proveedores de LLM configurados."
            hint={EMPTY_LLM_HINT[llm.backend]}
          />
        ) : (
          <ul className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {llm.providers.map((provider, index) => (
              <li key={provider.name}>
                <ProviderTile
                  provider={provider}
                  role={roleLabel(index, llm.providers.length)}
                  backend={llm.backend}
                />
              </li>
            ))}
          </ul>
        )}
      </Group>

      <Group title="Embeddings">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <EmbeddingsTile embeddings={embeddings} />
        </div>
      </Group>
    </Card>
  );
}

/** Próximo paso cuando `LLM_PROVIDERS` está vacío, según el modo del LLM. */
const EMPTY_LLM_HINT: Record<AiBackendT, string> = {
  fake: "Modo fake: el chat corre con el cliente determinista, sin red. Los proveedores se cargan en LLM_PROVIDERS.",
  api: "Modo api: cargá los proveedores en LLM_PROVIDERS.",
};

/** El primero de `LLM_PROVIDERS` es el principal; el resto, el respaldo en orden. */
function roleLabel(index: number, total: number): string {
  if (index === 0) return "Principal";
  return total > 2 ? `Respaldo ${index}` : "Respaldo";
}

/** Grupo de la tarjeta (LLM / embeddings): caption + su contenido. */
function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-3">
      <h4 className="text-caption text-[var(--color-ink-soft)]">{title}</h4>
      {children}
    </div>
  );
}

/** Un proveedor de LLM: rol + nombre + estado, host, modo y sus alias → id de modelo. */
function ProviderTile({
  provider,
  role,
  backend,
}: {
  provider: SystemLlmProviderT;
  role: string;
  backend: AiBackendT;
}) {
  return (
    <Tile eyebrow={role} title={provider.name} status={provider.status}>
      <dl className="flex flex-col">
        <Row label="Host" value={provider.host} />
        <Row label="Modo" value={backend} last />
      </dl>

      <div className="flex flex-col gap-1">
        <p className="text-caption text-[var(--color-ink-soft)]">Modelos por alias</p>
        <dl className="flex flex-col">
          {provider.models.map((route, index) => (
            <Row
              key={route.alias}
              label={route.alias}
              value={route.model_id}
              last={index === provider.models.length - 1}
            />
          ))}
        </dl>
      </div>
    </Tile>
  );
}

/** El cliente de embeddings: modelo + estado, host, modo y dimensión del vector. */
function EmbeddingsTile({ embeddings }: { embeddings: SystemEmbeddingsT }) {
  return (
    <Tile eyebrow="Modelo de embeddings" title={embeddings.model} status={embeddings.status}>
      <dl className="flex flex-col">
        <Row label="Host" value={embeddings.host ?? "Sin URL configurada"} />
        <Row label="Modo" value={embeddings.backend} />
        <Row label="Dimensión" value={String(embeddings.dim)} last />
      </dl>
    </Tile>
  );
}

/** Bloque con borde hairline: eyebrow + título a la izquierda, pill de estado a la derecha. */
function Tile({
  eyebrow,
  title,
  status,
  children,
}: {
  eyebrow: string;
  title: string;
  status: AiStatusT;
  children: ReactNode;
}) {
  return (
    <div className="flex h-full flex-col gap-4 rounded-[var(--radius-md)] border border-[var(--color-border)] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <p className="text-caption text-[var(--color-ink-soft)]">{eyebrow}</p>
          <p className="wrap-break-word text-body text-[var(--color-ink-deep)]">{title}</p>
        </div>
        <StatusPill status={status} />
      </div>
      {children}
    </div>
  );
}

/** Pill de estado: dot + label en el color del estado (el dot late solo si está OK). */
function StatusPill({ status }: { status: AiStatusT }) {
  const { label, colorVar } = STATUS_META[status];
  return (
    <span className="inline-flex shrink-0 items-center gap-2 rounded-[var(--radius-pill)] border border-[var(--color-border)] px-3 py-1">
      <span
        aria-hidden
        className={cn("size-2 rounded-[var(--radius-pill)]", status === "ok" && "anim-pulse-soft")}
        style={{ backgroundColor: `var(${colorVar})` }}
      />
      <span className="text-caption" style={{ color: `var(${colorVar})` }}>
        {label}
      </span>
    </span>
  );
}

/**
 * Fila de definición: término a la izquierda (`ink-soft`), valor técnico a la
 * derecha con `tabular-nums`. Hairline inferior salvo la última (`last`), mismo
 * idioma que el `RuntimeInventory`.
 */
function Row({ label, value, last = false }: { label: string; value: string; last?: boolean }) {
  return (
    <div
      className={cn(
        "flex items-baseline justify-between gap-4 py-2",
        !last && "border-b border-[var(--color-border)]",
      )}
    >
      <dt className="shrink-0 text-body-sm text-[var(--color-ink-soft)]">{label}</dt>
      <dd className="min-w-0 wrap-break-word text-right text-body-sm tabular-nums text-[var(--color-ink)]">
        {value}
      </dd>
    </div>
  );
}
