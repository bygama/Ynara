import type { Metadata } from "next";
import { notFound } from "next/navigation";

export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function LabLayout({ children }: { children: React.ReactNode }) {
  // Rutas internas de laboratorio visual: no disponibles en producción.
  if (process.env.NODE_ENV === "production") {
    notFound();
  }
  return <>{children}</>;
}
