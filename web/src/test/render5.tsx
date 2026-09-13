/**
 * Mount a Phase 5 screen inside the real auth provider and router.
 *
 * Not a stubbed auth context: the provider does the silent-refresh dance on
 * mount, sets the Authorization header and handles a lost session, and a test
 * that replaces it would prove none of that works.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { RenderResult } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";

import { setTokens } from "../api/client";
import { AuthProvider, RequireAuth } from "../auth/AuthProvider";
import { AppShell } from "../components/AppShell";
import { AdminPage } from "../pages/Admin";
import { AuditTrailPage } from "../pages/AuditTrail";
import { CaseDetailPage } from "../pages/CaseDetail";
import { LoginPage } from "../pages/Login";
import { WorklistPage } from "../pages/Worklist";

function client(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 0,
        gcTime: 0,
        // The real screens poll every 30s. Left on, the fake timers in these
        // tests would fire it and make assertions race.
        refetchInterval: false,
      },
      mutations: { retry: false },
    },
  });
}

const ROUTES = [
  { path: "/login", element: <LoginPage /> },
  {
    path: "/worklist",
    element: (
      <RequireAuth>
        <AppShell>
          <WorklistPage />
        </AppShell>
      </RequireAuth>
    ),
  },
  {
    path: "/cases/:caseId",
    element: (
      <RequireAuth>
        <AppShell>
          <CaseDetailPage />
        </AppShell>
      </RequireAuth>
    ),
  },
  {
    path: "/audit",
    element: (
      <RequireAuth roles={["auditor", "admin"]}>
        <AppShell>
          <AuditTrailPage />
        </AppShell>
      </RequireAuth>
    ),
  },
  {
    path: "/admin",
    element: (
      <RequireAuth roles={["admin"]}>
        <AppShell>
          <AdminPage />
        </AppShell>
      </RequireAuth>
    ),
  },
  { path: "/patients", element: <p>Patients</p> },
  { path: "/change-password", element: <p>Change your password</p> },
  { path: "*", element: <p>Not found</p> },
];

/**
 * `signedIn` seeds both tokens, so the provider's restore step trades the
 * refresh token for a user exactly as it does after a page reload. That is
 * the path worth exercising: it is the one that runs on every F5.
 */
export function renderApp5(
  path: string,
  { signedIn = true }: { signedIn?: boolean } = {},
): RenderResult {
  setTokens(
    signedIn ? "access-token-1" : null,
    signedIn ? "refresh-token-1" : null,
  );

  const router = createMemoryRouter(ROUTES, {
    initialEntries: [path],
    future: { v7_relativeSplatPath: true },
  });

  return render(
    <QueryClientProvider client={client()}>
      <AuthProvider>
        <RouterProvider router={router} future={{ v7_startTransition: true }} />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

/** Mount one element with the providers but no route table. */
export function renderWithProviders(element: ReactElement): RenderResult {
  const router = createMemoryRouter([{ path: "/", element }], {
    initialEntries: ["/"],
    future: { v7_relativeSplatPath: true },
  });
  return render(
    <QueryClientProvider client={client()}>
      <AuthProvider>
        <RouterProvider router={router} future={{ v7_startTransition: true }} />
      </AuthProvider>
    </QueryClientProvider>,
  );
}
