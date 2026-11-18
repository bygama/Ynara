import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { getResponse } from "msw";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { AuditFilters } from "@/features/audit/components/AuditFilters";
import { AuditRow } from "@/features/audit/components/AuditRow";
import { buildAuditQuery } from "@/features/audit/hooks/useAudit";
import {
  AdminAuditPage,
  type AdminAuditPageT,
  type AdminAuditRowT,
  type AuditFilterState,
  EMPTY_AUDIT_FILTERS,
  ORIGIN_MODEL_ID_MAX_LENGTH,
} from "@/features/audit/schemas";
import { auditPage, FIXTURE_MODEL_IDS } from "@/fixtures/audit";
import { adminHandlers } from "@/fixtures/handlers";
import { env } from "@/lib/env";

/**
 * Filtro y columna de modelo de origen del audit (ADR-028): el panel filtra por
 * el id exacto que informó el proveedor (`origin_model_id`) y pinta ese id, o el
 * enum de antes en las filas viejas. Cubre la query que arma el hook, el mock que
 * la contesta, el campo del filtro y la celda de la fila.
 */

const [MODEL_ID] = FIXTURE_MODEL_IDS;

function queryParams(filters: AuditFilterState): URLSearchParams {
  return new URLSearchParams(buildAuditQuery("7d", filters, 0));
}

/** Pide la página al handler MSW con la misma query que arma `useAudit`. */
async function fetchMockAudit(filters: AuditFilterState): Promise<AdminAuditPageT> {
  const query = buildAuditQuery("7d", filters, 0, 1000);
  const request = new Request(`${env.NEXT_PUBLIC_API_URL}/v1/admin/audit?${query}`);
  const response = await getResponse(adminHandlers, request);
  if (!response) throw new Error("ningún handler respondió /v1/admin/audit");
  return AdminAuditPage.parse(await response.json());
}

/** Dueño del estado de los filtros, como `AuditScreen`. */
function FiltersHarness({
  initial = EMPTY_AUDIT_FILTERS,
  onChange,
}: {
  initial?: AuditFilterState;
  onChange: (filters: AuditFilterState) => void;
}) {
  const [filters, setFilters] = useState(initial);
  return (
    <AuditFilters
      value={filters}
      onChange={(next) => {
        onChange(next);
        setFilters(next);
      }}
    />
  );
}

const ROW: AdminAuditRowT = {
  id: "3f2b8c1e-9a4d-4e7b-8c2a-1d5e6f7a8b9c",
  created_at: "2026-11-17T12:00:00Z",
  operation: "write",
  target_layer: "semantic",
  origin_mode: null,
  origin_model: null,
  origin_model_id: "Org/Agent-2026-06-01",
  origin_tool: "memory.consolidate",
  sensitive: false,
};

describe("buildAuditQuery: modelo de origen", () => {
  it("manda el id recortado como origin_model_id y nunca origin_model", () => {
    const sp = queryParams({ ...EMPTY_AUDIT_FILTERS, originModelId: `  ${MODEL_ID} ` });
    expect(sp.get("origin_model_id")).toBe(MODEL_ID);
    expect(sp.has("origin_model")).toBe(false);
  });

  it("omite el filtro si es null o queda vacío (el backend responde 422 a un id vacío)", () => {
    for (const originModelId of [null, "", "   "]) {
      const sp = queryParams({ ...EMPTY_AUDIT_FILTERS, originModelId });
      expect(sp.has("origin_model_id")).toBe(false);
      expect(sp.has("origin_model")).toBe(false);
    }
  });
});

describe("mock de /v1/admin/audit: modelo de origen", () => {
  it("contesta el filtro con el mismo nombre de param que manda el hook", async () => {
    const page = await fetchMockAudit({ ...EMPTY_AUDIT_FILTERS, originModelId: ` ${MODEL_ID} ` });
    expect(page.total).toBeGreaterThan(0);
    expect(page.items.every((r) => r.origin_model_id === MODEL_ID)).toBe(true);
    expect(page.total).toBe(
      auditPage({ ...EMPTY_AUDIT_FILTERS, originModelId: MODEL_ID }, 1000, 0).total,
    );
  });

  it("sin filtro de modelo devuelve todo el dataset", async () => {
    const page = await fetchMockAudit(EMPTY_AUDIT_FILTERS);
    expect(page.total).toBe(auditPage(EMPTY_AUDIT_FILTERS, 1000, 0).total);
  });
});

describe("AuditFilters: modelo de origen", () => {
  it("aplica el id recortado con Enter y no pide nada mientras se escribe", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<FiltersHarness onChange={onChange} />);

    await user.type(screen.getByLabelText("Modelo"), `  ${MODEL_ID} `);
    expect(onChange).not.toHaveBeenCalled();

    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith({ ...EMPTY_AUDIT_FILTERS, originModelId: MODEL_ID });
  });

  it("aplica al salir del campo y no repite el pedido si el id no cambió", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<FiltersHarness onChange={onChange} />);
    const input = screen.getByLabelText("Modelo");

    await user.type(input, MODEL_ID);
    await user.tab();
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith({ ...EMPTY_AUDIT_FILTERS, originModelId: MODEL_ID });

    await user.click(input);
    await user.keyboard("{Enter}");
    await user.tab();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("vaciar el campo vuelve a todos los modelos", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <FiltersHarness
        initial={{ ...EMPTY_AUDIT_FILTERS, originModelId: MODEL_ID }}
        onChange={onChange}
      />,
    );
    const input = screen.getByLabelText("Modelo");
    expect(input).toHaveValue(MODEL_ID);

    await user.clear(input);
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith(EMPTY_AUDIT_FILTERS);
  });

  it("Limpiar filtros vacía también el campo del modelo", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <FiltersHarness
        initial={{ ...EMPTY_AUDIT_FILTERS, originModelId: MODEL_ID }}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    expect(onChange).toHaveBeenLastCalledWith(EMPTY_AUDIT_FILTERS);
    expect(screen.getByLabelText("Modelo")).toHaveValue("");
  });

  it("corta el id al largo de la columna", () => {
    render(<AuditFilters value={EMPTY_AUDIT_FILTERS} onChange={vi.fn()} />);
    expect(screen.getByLabelText("Modelo")).toHaveAttribute(
      "maxlength",
      String(ORIGIN_MODEL_ID_MAX_LENGTH),
    );
  });
});

describe("AuditRow: modelo de origen", () => {
  it("muestra el id tal como lo informó el proveedor, sin pasarlo a mayúsculas", () => {
    render(<AuditRow row={ROW} />);
    const model = screen.getByText("Org/Agent-2026-06-01");
    expect(model).toHaveAttribute("title", "Org/Agent-2026-06-01");
    expect(model).not.toHaveClass("uppercase");
  });

  it("en una fila vieja muestra el enum de antes", () => {
    render(<AuditRow row={{ ...ROW, origin_model: "qwen", origin_model_id: null }} />);
    expect(screen.getByText("qwen")).toBeInTheDocument();
  });

  it("si la fila trae los dos, muestra el id", () => {
    render(<AuditRow row={{ ...ROW, origin_model: "qwen" }} />);
    expect(screen.getByText("Org/Agent-2026-06-01")).toBeInTheDocument();
    expect(screen.queryByText("qwen")).not.toBeInTheDocument();
  });

  it("sin modelo lo dice", () => {
    render(<AuditRow row={{ ...ROW, origin_model: null, origin_model_id: null }} />);
    expect(screen.getByText("sin modelo")).toBeInTheDocument();
  });
});
