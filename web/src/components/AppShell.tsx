/**
 * The frame every signed-in screen sits inside. Phase 5.2.
 *
 * Carries the two things that must be visible from anywhere: who you are
 * signed in as, and whether the system is healthy.
 *
 * The header is sticky because the status pill is one of those two things. A
 * clerk forty rows down a worklist should not have to scroll up to find out
 * that the worker has stopped — that is the single failure this product exists
 * to prevent, and it must stay on screen.
 */

import { Link, NavLink, useNavigate } from "react-router-dom";
import type { ReactNode } from "react";

import { SystemStatusPill } from "./SystemStatusPill";
import { ThemeToggle } from "./ThemeToggle";
import { Button } from "./ui/Button";
import { IfRole, useAuth } from "../auth/AuthProvider";
import { cn } from "../lib/cn";

const NAV = [
  { to: "/worklist", label: "Worklist", roles: ["doctor", "unit_head", "admin", "auditor"] },
  { to: "/patients", label: "Patients", roles: ["doctor", "unit_head", "lab_tech", "admin"] },
  // Phase 6. "Documents" rather than "Reports": the Reports tab below is the
  // Phase 5.6 metrics pack, and two tabs called Reports meaning different
  // things is how a clerk ends up on the wrong screen in a hurry.
  {
    to: "/documents",
    label: "Documents",
    roles: ["doctor", "unit_head", "lab_tech", "admin", "auditor"],
  },
  { to: "/reports", label: "Reports", roles: ["unit_head", "admin", "auditor"] },
  { to: "/audit", label: "Audit trail", roles: ["auditor", "admin"] },
  { to: "/admin", label: "Admin", roles: ["admin"] },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-canvas">
      {/* Keyboard users should not have to tab through six nav items to reach
          the thing they came for. */}
      <a href="#main" className="rg-skip-link">
        Skip to content
      </a>

      {/* ⚠️ Opaque, not translucent. A `bg-surface/85 backdrop-blur` header
          looked good on a wide screen and was a genuine legibility bug at
          ~950px: the header wraps to two rows there, and the page heading
          scrolled underneath it showed through as a ghost. Frosted glass is a
          decoration; reading the page is the job. */}
      <header className="sticky top-0 z-40 border-b border-line bg-surface shadow-sm">
        <div className="mx-auto flex max-w-7xl items-center gap-x-3 px-4 py-2.5">
          <Link
            to="/worklist"
            className="flex shrink-0 items-center gap-2 text-sm font-semibold text-ink"
          >
            <ShieldMark />
            <span className="hidden sm:inline">Result Guardian</span>
          </Link>

          {/* Scrolls rather than wraps. A header that grows a second row on a
              narrow window pushes the page content down unpredictably and,
              being sticky, eats a third of a laptop screen. */}
          <nav
            aria-label="Main"
            className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto
                       [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {NAV.map((item) => (
              <IfRole key={item.to} roles={item.roles}>
                <NavLink
                  to={item.to}
                  className={({ isActive }) =>
                    cn(
                      "shrink-0 whitespace-nowrap rounded px-2.5 py-1.5 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-brand-subtle text-brand-text"
                        : "text-ink-body hover:bg-surface-hover hover:text-ink",
                    )
                  }
                >
                  {item.label}
                </NavLink>
              </IfRole>
            ))}
          </nav>

          <div className="flex shrink-0 items-center gap-2">
            <SystemStatusPill />
            {user ? (
              <span className="hidden text-sm text-ink-body sm:inline">
                {user.full_name}{" "}
                <span className="text-ink-muted">
                  · {user.role.replace("_", " ")}
                </span>
              </span>
            ) : null}
            <ThemeToggle />
            <Button
              variant="ghost"
              size="sm"
              onClick={async () => {
                await logout();
                navigate("/login", { replace: true });
              }}
            >
              Sign out
            </Button>
          </div>
        </div>

        {/* Break-glass is loud in the audit log; it should be loud on screen
            too, so nobody forgets they are outside their own department. */}
        {user?.break_glass ? (
          <div
            role="alert"
            className="border-t border-followup-line bg-followup-subtle px-4 py-2
                       text-center text-sm font-medium text-followup-text"
          >
            Break-glass access is active. Everything you open is being recorded
            against the reason you gave.
          </div>
        ) : null}
      </header>

      <main id="main" className="mx-auto max-w-7xl px-4 py-6">
        {children}
      </main>
    </div>
  );
}

/** A quiet mark. Not a logo — this product has no room for decoration. */
function ShieldMark() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-[18px] text-brand"
    >
      <path d="M12 3 5 6v6c0 4.2 2.9 7.9 7 9 4.1-1.1 7-4.8 7-9V6l-7-3Z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}
