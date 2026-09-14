import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { Link, Navigate, RouterProvider, createBrowserRouter } from "react-router-dom";

import { AuthProvider, RequireAuth } from "./auth/AuthProvider";
import { AppShell } from "./components/AppShell";
import { AdminPage } from "./pages/Admin";
import { AuditTrailPage } from "./pages/AuditTrail";
import { CaseDetailPage } from "./pages/CaseDetail";
import { ChangePasswordPage } from "./pages/ChangePassword";
import { DischargeGatePage } from "./pages/DischargeGate";
import { DocumentDetailPage } from "./pages/DocumentDetail";
import { DocumentQueuePage } from "./pages/DocumentQueue";
import { EncounterDetailPage } from "./pages/EncounterDetail";
import { LoginPage } from "./pages/Login";
import { PatientDetailPage } from "./pages/PatientDetail";
import { PatientSearchPage } from "./pages/PatientSearch";
import { ReportsPage } from "./pages/Reports";
import { ResultEntryPage } from "./pages/ResultEntry";
import { WorklistPage } from "./pages/Worklist";
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

/** Wrap a page in the signed-in frame and the role gate. Phase 5.1. */
function Guarded({
  children,
  roles,
}: {
  children: React.ReactNode;
  roles?: string[];
}) {
  return (
    <RequireAuth roles={roles}>
      <AppShell>{children}</AppShell>
    </RequireAuth>
  );
}

const router = createBrowserRouter(
  [
    // Unauthenticated. `/change-password` is inside RequireAuth but outside
    // AppShell: a user who must change their password should not be shown a
    // navigation bar to pages the server will refuse.
    { path: "/login", element: <LoginPage /> },
    {
      path: "/change-password",
      element: (
        <RequireAuth>
          <ChangePasswordPage />
        </RequireAuth>
      ),
    },

    // The worklist is the landing page in Phase 5: a doctor signs in to see
    // what is waiting for them, not to search for a patient.
    { path: "/", element: <Navigate to="/worklist" replace /> },
    {
      path: "/worklist",
      element: (
        <Guarded roles={["doctor", "unit_head", "admin", "auditor"]}>
          <WorklistPage />
        </Guarded>
      ),
    },
    {
      path: "/cases/:caseId",
      element: (
        <Guarded roles={["doctor", "unit_head", "admin", "auditor"]}>
          <CaseDetailPage />
        </Guarded>
      ),
    },

    // Phases 1–3, now behind auth like everything else.
    {
      path: "/patients",
      element: (
        <Guarded>
          <PatientSearchPage />
        </Guarded>
      ),
    },
    {
      path: "/patients/:patientId",
      element: (
        <Guarded>
          <PatientDetailPage />
        </Guarded>
      ),
    },
    {
      path: "/encounters/:encounterId",
      element: (
        <Guarded>
          <EncounterDetailPage />
        </Guarded>
      ),
    },
    {
      path: "/encounters/:encounterId/discharge",
      element: (
        <Guarded roles={["doctor", "unit_head", "admin"]}>
          <DischargeGatePage />
        </Guarded>
      ),
    },
    // Phase 3.7. Scoped under the encounter so the screen can show whose
    // result this is without a second endpoint to fetch an order on its own.
    {
      path: "/encounters/:encounterId/orders/:orderId/result",
      element: (
        <Guarded roles={["doctor", "unit_head", "lab_tech", "admin"]}>
          <ResultEntryPage />
        </Guarded>
      ),
    },

    // Phase 6. An auditor may read the queue and the page images -- a
    // document is part of the record -- but the upload form and the retry
    // button are refused by the server regardless of what this route allows.
    {
      path: "/documents",
      element: (
        <Guarded roles={["doctor", "unit_head", "lab_tech", "admin", "auditor"]}>
          <DocumentQueuePage />
        </Guarded>
      ),
    },
    {
      path: "/documents/:documentId",
      element: (
        <Guarded roles={["doctor", "unit_head", "lab_tech", "admin", "auditor"]}>
          <DocumentDetailPage />
        </Guarded>
      ),
    },

    // Phase 5.4 / 5.5 / 5.6.
    {
      path: "/reports",
      element: (
        <Guarded roles={["unit_head", "admin", "auditor"]}>
          <ReportsPage />
        </Guarded>
      ),
    },
    {
      path: "/audit",
      element: (
        <Guarded roles={["auditor", "admin"]}>
          <AuditTrailPage />
        </Guarded>
      ),
    },
    {
      path: "/admin",
      element: (
        <Guarded roles={["admin"]}>
          <AdminPage />
        </Guarded>
      ),
    },

    { path: "*", element: <NotFound /> },
  ],
  // Opt in now rather than discovering the behaviour change at the v7 upgrade.
  { future: { v7_relativeSplatPath: true } },
);

function NotFound() {
  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      <h1 className="text-xl font-semibold text-ink">Page not found</h1>
      <p className="mt-2 text-sm text-ink-body">
        <Link to="/worklist" className="text-brand-text underline">
          Go to your worklist
        </Link>
      </p>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} future={{ v7_startTransition: true }} />
      </AuthProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
