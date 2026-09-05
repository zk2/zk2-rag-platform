"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { X } from "lucide-react";
import { useErrorBanner } from "@/lib/error-banner";

export function ErrorBanner() {
  const { message, dismiss } = useErrorBanner();
  const pathname = usePathname();

  // A failure belongs to the page that hit it; leaving it clears it
  useEffect(() => dismiss(), [pathname, dismiss]);

  if (!message) return null;
  return (
    <div className="flex items-start gap-3 border-b border-red-200 bg-red-50 px-6 py-3">
      <p className="flex-1 text-sm text-red-700">{message}</p>
      <button
        onClick={dismiss}
        title="Dismiss"
        className="text-red-400 hover:text-red-700"
      >
        <X className="size-4" />
      </button>
    </div>
  );
}
