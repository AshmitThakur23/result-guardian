import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { Link, Navigate, RouterProvider, createBrowserRouter } from "react-router-dom";

import { DischargeGatePage } from "./pages/DischargeGate";
import { EncounterDetailPage } from "./pages/EncounterDetail";
import { PatientDetailPage } from "./pages/PatientDetail";
import { PatientSearchPage } from "./pages/PatientSearch";
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
  [
    // Patient search is the entry point: everything else is reached by first
    // finding a patient, which is how a ward actually works.
    { path: "/", element: <Navigate to="/patients" replace /> },
    { path: "/patients", element: <PatientSearchPage /> },
    { path: "/patients/:patientId", element: <PatientDetailPage /> },
    { path: "/encounters/:encounterId", element: <EncounterDetailPage /> },
    { path: "/encounters/:encounterId/discharge", element: <DischargeGatePage /> },
    { path: "*", element: <NotFound /> },
  ],
  // Opt in now rather than discovering the behaviour change at the v7 upgrade.
  { future: { v7_relativeSplatPath: true } },
);

function NotFound() {
  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      <h1 className="text-xl font-semibold text-slate-900">Page not found</h1>
      <p className="mt-2 text-sm text-slate-600">
        <Link to="/patients" className="text-blue-700 underline">
          Find a patient
        </Link>
      </p>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} future={{ v7_startTransition: true }} />
    </QueryClientProvider>
  </React.StrictMode>,
);
