"use client";

import * as React from "react";
import { HelpCircle } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * The "?" next to a setting, explaining what it does in plain words.
 *
 * A visible affordance rather than a `title` attribute: a native tooltip is
 * invisible until you happen to hover the right pixels, waits a second, and
 * never appears at all on a touch screen. This one is focusable and opens on
 * keyboard focus too, so the explanation is reachable without a mouse.
 *
 * Written for settings whose name only makes sense to whoever implemented
 * them - which, in a RAG platform, is most of them.
 */
export function HelpTip({
  children,
  className,
  side = "right",
}: {
  children: React.ReactNode;
  className?: string;
  side?: "right" | "left";
}) {
  return (
    <span className={cn("group relative inline-flex align-middle", className)}>
      <button
        type="button"
        aria-label="What is this?"
        className="text-slate-400 transition-colors hover:text-slate-700 focus-visible:text-slate-700 focus-visible:outline-none"
        onClick={(e) => e.preventDefault()}
      >
        <HelpCircle className="size-3.5" />
      </button>
      <span
        role="tooltip"
        className={cn(
          "pointer-events-none absolute bottom-full z-20 mb-1.5 hidden w-64 rounded-md",
          "border border-slate-200 bg-white p-2 text-xs font-normal leading-relaxed",
          "text-slate-600 shadow-lg group-hover:block group-focus-within:block",
          side === "right" ? "left-0" : "right-0",
        )}
      >
        {children}
      </span>
    </span>
  );
}

/** A label with its explanation attached, for the common case. */
export function LabelWithHelp({
  htmlFor,
  label,
  help,
  className,
}: {
  htmlFor?: string;
  label: string;
  help: React.ReactNode;
  className?: string;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className={cn("mb-1 flex items-center gap-1.5 text-sm font-medium text-slate-700", className)}
    >
      {label}
      <HelpTip>{help}</HelpTip>
    </label>
  );
}
