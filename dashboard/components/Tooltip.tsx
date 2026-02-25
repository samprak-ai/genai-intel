/**
 * Tooltip — lightweight hover tooltip using pure CSS/Tailwind.
 * Works across all browsers including Safari (which ignores `title` on non-inputs).
 *
 * Usage:
 *   <Tooltip text="Explanation here">
 *     <span>Hover me</span>
 *   </Tooltip>
 *
 *   <Tooltip text="Explanation here" position="below">
 *     <span>Hover me</span>
 *   </Tooltip>
 */

import { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface Props {
  text: string;
  children: ReactNode;
  className?: string;
  position?: "above" | "below";
}

export function Tooltip({ text, children, className, position = "above" }: Props) {
  const isBelow = position === "below";
  return (
    <span className={cn("relative group inline-flex cursor-help", className)}>
      {children}
      <span
        role="tooltip"
        className={cn(
          // Positioning
          "absolute left-1/2 -translate-x-1/2 z-50",
          isBelow ? "top-full mt-1.5" : "bottom-full mb-1.5",
          // Appearance
          "w-max max-w-[240px] rounded-md bg-gray-900 px-2.5 py-1.5",
          "text-xs text-white leading-snug text-center whitespace-normal",
          "shadow-lg pointer-events-none",
          // Show only on group hover
          "opacity-0 group-hover:opacity-100 transition-opacity duration-150",
        )}
      >
        {text}
        {/* Arrow */}
        {isBelow
          ? <span className="absolute bottom-full left-1/2 -translate-x-1/2 border-4 border-transparent border-b-gray-900" />
          : <span className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-gray-900" />
        }
      </span>
    </span>
  );
}
