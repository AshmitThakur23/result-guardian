import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { RenderResult } from "@testing-library/react";
import { Link, Navigate, RouterProvider, createMemoryRouter } from "react-router-dom";

import { DischargeGatePage } from "../pages/DischargeGate";
import { EncounterDetailPage } from "../pages/EncounterDetail";
import { PatientDetailPage } from "../pages/PatientDetail";
import { PatientSearchPage } from "../pages/PatientSearch";
import { ResultEntryPage } from "../pages/ResultEntry";

/**
 * Mounts the **whole** route table rather than one page, so navigation
 * between the Phase 1.5 screens is exercised for real: a link that points at a
 * route which does not exist is a bug these tests should catch, and mounting a
 * single page in isolation cannot.
 *
 * Kept separate from `render.tsx` so the Phase 1.4 gate tests keep booting the
 * single route they care about.
 */
export function renderApp(initialPath: string): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: 0 },
      mutations: { retry: false },
    },
  });

  const router = createMemoryRouter(
    [
      { path: "/", element: <Navigate to="/patients" replace /> },
      { path: "/patients", element: <PatientSearchPage /> },
      { path: "/patients/:patientId", element: <PatientDetailPage /> },
      { path: "/encounters/:encounterId", element: <EncounterDetailPage /> },
      { path: "/encounters/:encounterId/discharge", element: <DischargeGatePage /> },
      {
        path: "/encounters/:encounterId/orders/:orderId/result",
        element: <ResultEntryPage />,
      },
      {
        path: "*",
        element: (
          <main>
            <h1>Page not found</h1>
            <Link to="/patients">Find a patient</Link>
          </main>
        ),
      },
    ],
    {
      initialEntries: [initialPath],
      future: { v7_relativeSplatPath: true },
    },
  );

  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} future={{ v7_startTransition: true }} />
    </QueryClientProvider>,
  );
}
