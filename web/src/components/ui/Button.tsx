/**
 * The one button in the product.
 *
 * Variants are named by **intent**, not by colour, so a screen cannot end up
 * with two differently-coloured primary actions. `danger` is deliberately the
 * only filled red control anywhere: the override path is the one place a
 * clinician can do something irreversible, and it must not look like Save.
 */

import { forwardRef } from "react";
import type { ButtonHTMLAttributes } from "react";

import { cn } from "../../lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-brand text-ink-inverse shadow-sm hover:bg-brand-hover " +
    "disabled:bg-line-strong disabled:text-ink-muted disabled:shadow-none",
  secondary:
    "bg-surface text-ink border border-line shadow-sm hover:bg-surface-hover " +
    "hover:border-line-strong disabled:text-ink-muted disabled:shadow-none",
  ghost: "bg-transparent text-ink-body hover:bg-surface-hover hover:text-ink",
  // Reserved for the override path. Nothing else on this screen is red-filled,
  // so the one destructive action cannot be mistaken for the normal one.
  danger:
    "bg-critical text-ink-inverse shadow-sm hover:brightness-95 " +
    "disabled:bg-critical-line disabled:text-ink-muted disabled:shadow-none",
};

const SIZES: Record<Size, string> = {
  sm: "px-3 py-1.5 text-xs gap-1.5",
  md: "px-4 py-2 text-sm gap-2",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", className, type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        "inline-flex items-center justify-center rounded font-medium",
        // 120ms: perceptible, but gone before it can feel like lag on a
        // clinician clicking through forty cases.
        "transition-[background-color,border-color,box-shadow,filter] duration-120",
        "disabled:cursor-not-allowed active:translate-y-px",
        SIZES[size],
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
});
