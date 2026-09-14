/**
 * The frame every signed-in screen sits inside. Phase 5.2.
 *
 * Carries the two things that must be visible from anywhere: who you are
 * signed in as, and whether the system is healthy.
 */

import { Link, NavLink, useNavigate } from "react-router-dom";
import type { ReactNode } from "react";

import { SystemStatusPill } from "./SystemStatusPill";
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
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-4 px-4 py-3">
          <Link to="/worklist" className="text-sm font-semibold text-slate-900">
            Result Guardian
          </Link>

          <nav className="flex flex-wrap items-center gap-1">
            {NAV.map((item) => (
              <IfRole key={item.to} roles={item.roles}>
                <NavLink
                  to={item.to}
                  className={({ isActive }) =>
                    cn(
                      "rounded-md px-3 py-1.5 text-sm font-medium",
                      isActive
                        ? "bg-blue-50 text-blue-800"
                        : "text-slate-600 hover:bg-slate-100",
                    )
                  }
                >
                  {item.label}
                </NavLink>
              </IfRole>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <SystemStatusPill />
            {user ? (
              <span className="text-sm text-slate-600">
                {user.full_name}{" "}
                <span className="text-slate-400">· {user.role.replace("_", " ")}</span>
              </span>
            ) : null}
            <Button
              variant="ghost"
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
          <div className="bg-amber-100 px-4 py-2 text-center text-sm font-medium text-amber-900">
            Break-glass access is active. Everything you open is being recorded
            against the reason you gave.
          </div>
        ) : null}
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
    </div>
  );
}
