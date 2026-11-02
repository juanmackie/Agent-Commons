import { cn } from "@/lib/utils";
import * as React from "react";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "primary" | "secondary" | "outline" | "ghost";
  size?: "default" | "sm" | "lg";
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "default", ...props }, ref) => {
    const variants = {
      default: "bg-[var(--surface)] text-[var(--ink)] border-[var(--line)] hover:bg-[var(--surface-raised)]",
      primary: "bg-[var(--accent)] text-white border-[var(--accent)] hover:brightness-95",
      secondary: "bg-[var(--surface-raised)] text-[var(--ink-secondary)] border-[var(--line)] hover:bg-[var(--line)]",
      outline: "border-[var(--line)] bg-transparent hover:bg-[var(--surface)] text-[var(--ink)]",
      ghost: "bg-transparent hover:bg-[var(--surface)] text-[var(--ink)] border-transparent",
    };
    const sizes = {
      default: "h-12 px-4 text-sm",
      sm: "h-9 px-3 text-xs",
      lg: "h-14 px-6 text-base",
    };
    return (
      <button
        className={cn(
          "inline-flex items-center justify-center rounded-md font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)] focus-visible:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed border",
          variants[variant],
          sizes[size],
          className
        )}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button };
