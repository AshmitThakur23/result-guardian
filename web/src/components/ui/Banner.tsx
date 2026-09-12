import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

type Tone = "danger" | "warning" | "info" | "success";

const TONES: Record<Tone, string> = {
  danger: "border-red-300 bg-red-50 text-red-900",
  warning: "border-amber-300 bg-amber-50 text-amber-900",
  info: "border-slate-300 bg-white text-slate-800",
  success: "border-green-300 bg-green-50 text-green-900",
};

export function Banner({
  tone,
  title,
  children,
  className,
}: {
  tone: Tone;
  title: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      // `alert` on the blocking and error banners: a doctor using a screen
      // reader must be told the discharge is blocked without having to go
      // looking for the reason.
      role={tone === "danger" || tone === "warning" ? "alert" : "status"}
      className={cn("rounded-md border px-4 py-3", TONES[tone], className)}
    >
      <p className="font-semibold">{title}</p>
      {children ? <div className="mt-1 text-sm">{children}</div> : null}
    </div>
  );
}
