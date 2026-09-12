/** Join class names, dropping falsy entries. Small enough not to warrant clsx. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
