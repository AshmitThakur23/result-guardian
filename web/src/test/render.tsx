import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { RenderResult } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";

import { DischargeGatePage } from "../pages/DischargeGate";
import { ENCOUNTER_ID } from "./server";

/** Mount the gate at its real route, with retries off so a stubbed failure
 *  surfaces immediately instead of after a backoff. */
export function renderGate(encounterId: string = ENCOUNTER_ID): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: 0 },
      mutations: { retry: false },
    },
  });

  const router = createMemoryRouter(
    [{ path: "/encounters/:encounterId/discharge", element: <DischargeGatePage /> }],
    {
      initialEntries: [`/encounters/${encounterId}/discharge`],
      future: { v7_relativeSplatPath: true },
    },
  );

  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} future={{ v7_startTransition: true }} />
    </QueryClientProvider>,
  );
}
