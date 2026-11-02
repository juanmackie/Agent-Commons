import { cn } from "@/lib/utils";
import * as React from "react";

const Badge = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "inline-flex items-center rounded-full border border-[var(--line)] px-2.5 py-0.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--focus)] focus:ring-offset-2 bg-[var(--surface)] text-[var(--ink-secondary)]",
        className
      )}
      {...props}
    />
  )
);
Badge.displayName = "Badge";

export { Badge };
