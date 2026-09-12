import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";

import { DischargeGatePage } from "./pages/DischargeGate";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Readiness is the gate's source of truth and it moves underneath us --
      // a result can arrive, or a colleague can contract an order, while this
      // screen is open. Never serve it from a stale cache.
      staleTime: 0,
      refetchOnWindowFocus: true,
      retry: 1,
    },
    // A discharge is not idempotent. Retrying a failed POST could open a
    // second set of tracking cases, so mutations never retry themselves.
    mutations: { retry: false },
  },
});

const router = createBrowserRouter(
  [{ path: "/encounters/:encounterId/discharge", element: <DischargeGatePage /> }],
  // Opt in now rather than discovering the behaviour change at the v7 upgrade.
  { future: { v7_relativeSplatPath: true } },
);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} future={{ v7_startTransition: true }} />
    </QueryClientProvider>
  </React.StrictMode>,
);
