/**
 * Phase 6 screens — the review queue, the upload form and the fallback view.
 *
 * The assertions that matter here are not "does it render". They are:
 *
 * * a document that failed shows **the reason, in words a clerk can act on**;
 * * the fallback banner offers **a route back to manual entry**, because
 *   6.5's guarantee is that the workflow never stalls;
 * * the page image is fetched **with the Authorization header**, since a
 *   plain `<img src>` would 401 and leave the clerk with nothing to read;
 * * the upload posts multipart **without a JSON Content-Type**, because
 *   setting one by hand omits the boundary and the server cannot parse it.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setTokens } from "../api/client";
import type { DocumentDetail, DocumentRow } from "../api/types6";
import { DocumentDetailPage } from "./DocumentDetail";
import { DocumentQueuePage } from "./DocumentQueue";

const DOC_ID = "06aa70c2-7f55-7e8b-8000-4b3560e31f50";

type Call = { method: string; path: string; authorization: string | null;
              contentType: string | null; isFormData: boolean };

let calls: Call[] = [];

function failedDoc(overrides: Partial<DocumentRow> = {}): DocumentRow {
  return {
    id: DOC_ID,
    sha256: "a".repeat(64),
    original_filename: "haemogram-2026-09-14.pdf",
    mime_type: "application/pdf",
    size_bytes: 482_000,
    page_count: 2,
    source_channel: "watched_folder",
    status: "needs_review",
    error_text:
      "This PDF is password-protected. Ask the lab to send an unlocked copy, " +
      "or enter the result manually.",
    received_at: new Date(Date.now() - 3_600_000).toISOString(),
    attempts: 1,
    order_id: null,
    case_id: null,
    ...overrides,
  };
}

function detail(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    ...failedDoc(),
    pages: [
      {
        page_no: 1,
        width_pt: 595,
        height_pt: 842,
        is_scanned: true,
        text_layer: "Haemoglobin 9.2 g/dL",
        // Below 0.70 — the clerk must be told to check every value.
        ocr_confidence: 0.42,
        image_url: `/api/documents/${DOC_ID}/pages/1/image`,
      },
    ],
    ...overrides,
  };
}

function install(handlers: Record<string, unknown> = {}) {
  calls = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const headers = new Headers(init?.headers as HeadersInit | undefined);
    const path = url.replace(/^\/api/, "").split("?")[0];
    calls.push({
      method,
      path,
      authorization: headers.get("Authorization"),
      contentType: headers.get("Content-Type"),
      isFormData: init?.body instanceof FormData,
    });

    const json = (value: unknown, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: { "Content-Type": "application/json" },
      });

    if (path === "/auth/me") {
      return json({
        id: "u1", employee_code: "LAB-1", full_name: "Lab Tech",
        role: "lab_tech", department_id: null, must_change_password: false,
        break_glass: false,
      });
    }
    if (path === "/documents/review-queue") {
      return json(handlers.queue ?? { documents: [failedDoc()], count: 1 });
    }
    if (/\/pages\/\d+\/image$/.test(path)) {
      // A one-pixel PNG is enough: the assertion is that the request carried
      // a bearer token, not what the image looks like.
      return new Response(new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])]), {
        status: 200,
        headers: { "Content-Type": "image/png" },
      });
    }
    if (/\/spans$/.test(path)) return json({ spans: [] });
    if (/\/retry$/.test(path)) {
      return json({ document_id: DOC_ID, status: "received" });
    }
    if (path === "/reports/upload") {
      return json(
        handlers.upload ?? {
          document_id: DOC_ID, sha256: "b".repeat(64), duplicate: false,
          size_bytes: 1200, mime_type: "application/pdf",
          virus_scan: "skipped",
          message: "Report received. Extraction has been queued.",
        },
        202,
      );
    }
    if (/^\/documents\/[^/]+$/.test(path)) {
      return json(handlers.detail ?? detail());
    }
    return json({ title: `unhandled ${method} ${path}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function mount(
  element: React.ReactElement,
  { route = "/", entry = "/" }: { route?: string; entry?: string } = {},
) {
  setTokens("access-token-1", "refresh-token-1");
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, refetchInterval: false },
      mutations: { retry: false },
    },
  });
  const router = createMemoryRouter(
    [
      { path: route, element },
      { path: "/patients", element: <p>Patients</p> },
      { path: "/documents", element: <p>Documents list</p> },
    ],
    { initialEntries: [entry], future: { v7_relativeSplatPath: true } },
  );
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} future={{ v7_startTransition: true }} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  install();
  // jsdom has neither, and PageImage uses both.
  if (!URL.createObjectURL) {
    Object.defineProperty(URL, "createObjectURL", {
      writable: true, value: vi.fn(() => "blob:mock"),
    });
  }
  if (!URL.revokeObjectURL) {
    Object.defineProperty(URL, "revokeObjectURL", {
      writable: true, value: vi.fn(),
    });
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
  setTokens(null, null);
});

describe("the review queue", () => {
  it("shows a failed document with the reason in plain words", async () => {
    mount(<DocumentQueuePage />);

    const row = await screen.findByRole("row", {
      name: /haemogram-2026-09-14\.pdf/,
    });

    expect(
      within(row).getByText(/password-protected/i),
    ).toBeInTheDocument();
    // Not the raw status string: a clerk should not have to know what
    // `needs_review` means.
    expect(within(row).getByText("Needs review")).toBeInTheDocument();
  });

  it("says plainly when nothing is waiting", async () => {
    install({ queue: { documents: [], count: 0 } });
    mount(<DocumentQueuePage />);

    expect(await screen.findByText(/Nothing waiting/i)).toBeInTheDocument();
  });

  it("uploads as multipart and lets the browser set the boundary", async () => {
    const user = userEvent.setup();
    mount(<DocumentQueuePage />);

    const file = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], "r.pdf", {
      type: "application/pdf",
    });
    await user.upload(screen.getByLabelText(/Report file/i), file);
    await user.click(screen.getByRole("button", { name: /^Upload$/ }));

    // Specific, not /Report received/: the banner title and its body both
    // say that, and an ambiguous matcher fails on the duplicate rather than
    // on anything real.
    await screen.findByText("Report received. Extraction has been queued.");

    const upload = calls.find((c) => c.path === "/reports/upload");
    expect(upload).toBeDefined();
    expect(upload!.isFormData).toBe(true);
    // ★ If this header is set by hand it carries no multipart boundary and
    // the server cannot parse the body — a 422 that looks like a validation
    // bug.
    expect(upload!.contentType).toBeNull();
    expect(upload!.authorization).toBe("Bearer access-token-1");
  });

  it("admits when no virus scanner is configured", async () => {
    const user = userEvent.setup();
    mount(<DocumentQueuePage />);

    const file = new File([new Uint8Array([0x25])], "r.pdf", {
      type: "application/pdf",
    });
    await user.upload(screen.getByLabelText(/Report file/i), file);
    await user.click(screen.getByRole("button", { name: /^Upload$/ }));

    // `skipped` is not `clean`, and the screen must not imply otherwise.
    expect(
      await screen.findByText(/No virus scanner is configured/i),
    ).toBeInTheDocument();
  });
});

describe("the fallback view", () => {
  it("★ offers a route back to manual entry when extraction failed", async () => {
    mount(<DocumentDetailPage />, {
      route: "/documents/:documentId",
      entry: `/documents/${DOC_ID}`,
    });

    // 6.5: "The workflow never stalls because parsing failed." The screen has
    // to say so, and give the clerk somewhere to go.
    expect(
      await screen.findByText(/needs to be checked by a person/i),
    ).toBeInTheDocument();
    const link = screen.getByRole("link", {
      name: /Find the patient and enter the result/i,
    });
    expect(link).toHaveAttribute("href", "/patients");
  });

  it("★ fetches the page image with a bearer token", async () => {
    mount(<DocumentDetailPage />, {
      route: "/documents/:documentId",
      entry: `/documents/${DOC_ID}`,
    });

    await waitFor(() => {
      const image = calls.find((c) => /\/pages\/1\/image$/.test(c.path));
      expect(image).toBeDefined();
      // A plain <img src="/api/…"> would send no Authorization header and
      // 401 on every page, leaving the clerk nothing to read from.
      expect(image!.authorization).toBe("Bearer access-token-1");
    });
  });

  it("warns that low-confidence text must be checked against the image", async () => {
    mount(<DocumentDetailPage />, {
      route: "/documents/:documentId",
      entry: `/documents/${DOC_ID}`,
    });

    // Shown and doubted, never hidden and never trusted: hiding it would make
    // the clerk retype a page that is mostly right.
    expect(
      await screen.findByText(/below the 70% threshold/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Haemoglobin 9.2 g\/dL/)).toBeInTheDocument();
  });

  it("re-queues the document when retry is clicked", async () => {
    const user = userEvent.setup();
    mount(<DocumentDetailPage />, {
      route: "/documents/:documentId",
      entry: `/documents/${DOC_ID}`,
    });

    await user.click(
      await screen.findByRole("button", { name: /Try reading it again/i }),
    );

    await waitFor(() => {
      expect(calls.some((c) => c.method === "POST" && /\/retry$/.test(c.path)))
        .toBe(true);
    });
  });

  it("does not offer retry while the document is still being read", async () => {
    install({ detail: detail({ status: "extracting", error_text: null }) });
    mount(<DocumentDetailPage />, {
      route: "/documents/:documentId",
      entry: `/documents/${DOC_ID}`,
    });

    expect(await screen.findByText(/Still being read/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Try reading it again/i }),
    ).toBeDisabled();
  });
});
