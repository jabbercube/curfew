import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// shadcn's standard `cn` helper: clsx for conditional classnames, tailwind-merge
// to dedupe conflicting Tailwind utilities (e.g. `px-2` vs `px-4`).
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
