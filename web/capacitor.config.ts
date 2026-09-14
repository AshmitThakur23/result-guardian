import type { CapacitorConfig } from "@capacitor/cli";

/**
 * Result Guardian as a standalone Android app.
 *
 * ## The one decision in this file
 *
 * `server.url` makes the WebView load the dashboard **from NODE A** rather than
 * from files bundled inside the APK. That is not a shortcut — it is what keeps
 * the phone honest:
 *
 * * every `fetch("/api/...")` in the app is **relative**, so loading from NODE A
 *   makes them same-origin. **No CORS change, no build-time base URL** — exactly
 *   what `vite.config.ts` already says the design depends on.
 * * the APK is built **once**. A UI change is `npm run build` on NODE A and the
 *   phone picks it up on next launch. Bundling would mean a new APK, installed
 *   on every phone, for every fix.
 * * **nothing clinical lives on the phone.** No bundled dashboard, no cached
 *   patient data, no offline store. Lose the handset and you have lost a
 *   browser, not a record.
 *
 * ## What this app must never do
 *
 * **Talk to NODE B.** The phone reaches NODE A and nothing else; NODE A alone
 * decides whether to consult the inference node. Verified in the codebase: the
 * only NODE B address in `web/src` is a test mock, and the admin screen renders
 * NODE B's URL as *text* rather than calling it.
 *
 *     Phone ──HTTP──► NODE A ──► NODE B   (explanation only)
 *        ✗ never phone ──► NODE B
 *
 * ## ⚠️ Two things that will bite, and are not bugs
 *
 * 1. **`cleartext: true` is a real exemption.** Patient data crosses the Wi-Fi
 *    unencrypted. Acceptable on an isolated ward VLAN; **not acceptable on
 *    shared or campus Wi-Fi**. Phase 10.1 replaces `:80` with a hospital
 *    hostname so Caddy provisions TLS — at which point `url` becomes `https://`
 *    and this exemption is deleted, not merely narrowed.
 *
 * 2. **The address below is DHCP.** NODE A is `172.25.52.148` today because
 *    that is what the campus router leased it. A reconnect can change it and
 *    the app stops working until this file and the APK are rebuilt. Before any
 *    real deployment this must be a DHCP reservation or a hostname.
 */
const config: CapacitorConfig = {
  appId: "in.resultguardian.app",
  appName: "Result Guardian",
  // Required by the CLI even though nothing is bundled: `server.url` wins at
  // runtime, and `dist` is what `npx cap sync` copies for the fallback page.
  webDir: "dist",
  android: {
    // The APK is a debug build for the hackathon. Kept explicit so nobody
    // mistakes it for something that has been signed for distribution.
    allowMixedContent: true,
  },
  server: {
    // NODE A, over the private LAN. Nothing else is reachable from the phone.
    url: "http://172.25.52.148",
    cleartext: true,
  },
};

export default config;
