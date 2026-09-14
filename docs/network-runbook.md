# Network Runbook — NODE A ⇄ NODE B

> Phase 0.4. Read this before connecting the two machines.
> **The link does not need internet access.** It only joins the two nodes. Hospital internet can be completely down and NODE A ⇄ NODE B still works.

## Current machines

| Role | Machine | IP | Status |
|---|---|---|---|
| **NODE A** — core | Abhinendra's laptop (`LAPTOP-06ER0HBM`, Windows 11) | **172.25.52.148** /20, gw `172.25.48.1`, SSID `Studentwifi_5G` — measured 2026-09-14 | ✅ Full stack runs; `/api/health` → 200, `db: ok`, worker heartbeat 1s, migrations at `0013` |
| **NODE B** — inference | Ashmit's machine (`LAPTOP-5JCGN9SJ`) | **172.25.54.48** /20, gw `172.25.48.1`, SSID `Studentwifi_5G` — measured 2026-09-14 (was `192.168.0.168` on a home network) | ✅ **provisioned 2026-09-14.** RTX 3050, 4 GB VRAM. Ollama `v0.15.2` serving `qwen3:4b` + `mistral:7b`. ⚠️ **firewall rule still outstanding** |

> ### ✅ Both nodes are on the same subnet right now
>
> `172.25.48.0/20` covers `172.25.48.0`–`172.25.63.255`, so NODE A `.52.148` and NODE B `.54.48`
> share a subnet and a gateway. **Addressing is not the blocker** — the only open question is
> whether this network permits peer traffic.

> ### 🔴 Re-read NODE B's IP after every network change
>
> It is DHCP. Get it with the adapter filter, **not** the first address:
>
> ```powershell
> Get-NetIPConfiguration | Where-Object { $_.NetAdapter.Status -eq 'Up' -and $_.IPv4DefaultGateway } |
>   Select-Object InterfaceAlias, @{n='IP';e={$_.IPv4Address.IPAddress}}
> ```
>
> **Why not simply the first address:** on 2026-09-14 a *disconnected* Ethernet adapter still
> held a stale static `172.18.30.66`, and the naive pick returned it. It even answered
> `/api/tags` — because a request from NODE B to its own address never leaves the machine.
> NODE A could not have reached it. `setup-windows.ps1` was fixed to filter on adapter status.

> ### ✅ `Studentwifi_5G` permits peer traffic — measured 2026-09-14, no hotspot needed
>
> Despite campus wifi being listed as "not usable" below, **this one is not isolating clients.**
> From NODE B, TCP to NODE A succeeded on both ports the stack listens on:
>
> ```
> Test-NetConnection 172.25.52.148 -Port 80    -> TcpTestSucceeded : True
> Test-NetConnection 172.25.52.148 -Port 8000  -> TcpTestSucceeded : True
> ```
>
> A phone hotspot is therefore **not required** for the cross-node checks or for Phase 8.

> ### 🔴 Do NOT diagnose this with `ping` — it gives a false negative
>
> `ping 172.25.52.148` from NODE B got **no reply**, while TCP to the same host on the same
> network succeeded. **Windows blocks inbound ICMP by default**, so a silent ping says nothing
> about whether the network works — and the fallback chain's "test with ping" advice will send
> you to a hotspot you do not need.
>
> **Windows-to-Windows, test the port you actually care about:**
>
> ```powershell
> Test-NetConnection <OTHER_NODE_IP> -Port 11434     # from NODE A, to NODE B
> ```
>
> `ping` remains a reasonable first check only where the far side is Linux, or where ICMP is
> known to be permitted.

> ### ✅ RESOLVED 2026-09-14 18:33 — NODE B's firewall no longer blocks ollama.exe
>
> Both auto-created Block rules were removed and the Phase 10.1 allow rule is in place,
> scoped to NODE A's address only:
>
> ```
> ollama inbound Block rules : 0
> Result Guardian NODE B     : Enabled=True Action=Allow Direction=Inbound Profile=Any
>                              TCP 11434, RemoteAddress 172.25.52.148   <- NODE A only
> netstat                    : TCP 0.0.0.0:11434 LISTENING  (+ [::]:11434)
> /api/tags on 172.25.54.48  : qwen3:4b, mistral:7b
> ```
>
> `OLLAMA_HOST` needed no change — Ollama was already bound to `0.0.0.0`, not localhost.
>
> **Still to do, from NODE A:** `Test-NetConnection 172.25.54.48 -Port 11434` must return
> `True`. Only that proves the path end to end; NODE B cannot test its own inbound rule,
> because a request from NODE B to its own address never crosses the firewall.
>
> <details><summary>The original diagnosis, kept for the record</summary>
>
> ### 🔴 THE ACTUAL BLOCKER: NODE B's firewall is set to BLOCK ollama.exe
>
> Two rules on NODE B, created by Windows when Ollama first tried to listen and the prompt was
> dismissed, **explicitly block it inbound on the Public profile**:
>
> ```
> DisplayName : ollama.exe   Direction: Inbound   Action: Block   Enabled: True   Profile: Public
>   TCP Query User{0C9F6AB2-...}C:\users\asus\appdata\local\programs\ollama\ollama.exe
>   UDP Query User{62B02A10-...}C:\users\asus\appdata\local\programs\ollama\ollama.exe
> ```
>
> **In Windows Firewall a Block rule beats an Allow rule**, so adding the Phase 10.1 allow rule
> on its own changes nothing. The blocks must be removed first. NODE B's wifi profile is also
> `Public`, where the default inbound action is to deny.
>
> **Fix — run on NODE B in an elevated PowerShell:**
>
> ```powershell
> # 1. remove the two auto-created Block rules
> Get-NetFirewallApplicationFilter |
>   Where-Object { $_.Program -like "*ollama*" } |
>   Get-NetFirewallRule |
>   Where-Object { $_.Direction -eq "Inbound" -and $_.Action -eq "Block" } |
>   Remove-NetFirewallRule
>
> # 2. allow 11434 from NODE A's address ONLY (never 0.0.0.0/0 - Phase 10.1)
> New-NetFirewallRule -DisplayName "Result Guardian NODE B" `
>     -Direction Inbound -Protocol TCP -LocalPort 11434 `
>     -RemoteAddress 172.25.52.148 -Action Allow -Profile Any
> ```
>
> Then, **from NODE A**: `Test-NetConnection 172.25.54.48 -Port 11434` → must be `True`.
>
> </details>

## ~~🔴 The two machines are on DIFFERENT NETWORKS~~ — RESOLVED 2026-09-14

> ⛔ **Superseded. Do not act on this section — it is kept only so the reasoning
> trail survives.** It was measured when NODE B was on a home router
> (`192.168.0.168`) and NODE A on campus wifi. **NODE B has since joined
> `Studentwifi_5G` and both nodes are now on `172.25.48.0/20`** — see the
> "same subnet" note above, which is the current truth.
>
> Two conclusions it drew are now known to be **wrong**, and each would cost an
> hour if followed:
>
> 1. *"`Studentwifi_5G` almost always runs client isolation."* It does **not** —
>    TCP peer traffic was measured working on ports 80 and 8000. A phone hotspot
>    is not needed.
> 2. *"Test with `ping` before trusting it."* `ping` is a **false negative**
>    between two Windows machines, which block inbound ICMP by default. Use
>    `Test-NetConnection -Port`.
>
> What it got right, and what still stands: **a DHCP address on a network you do
> not control is not a stable basis for a firewall rule.** Both addresses must be
> re-read after every network change.

**Confirmed from NODE A, 2026-09-14, both nodes on `Studentwifi_5G`:**

```
Get-NetIPConfiguration | ? { $_.NetAdapter.Status -eq 'Up' -and $_.IPv4DefaultGateway }
  Wi-Fi   172.25.52.148   gateway 172.25.48.1        <- NODE A, unchanged

Test-NetConnection 172.25.54.48 -Port 11434          <- NODE A -> NODE B
  PingSucceeded    = False     (expected: Windows blocks inbound ICMP)
  TcpTestSucceeded = False     <- THE BLOCKER: ollama.exe Block rules on NODE B
```

NODE A's address has **not** moved, so the allow rule above — scoped to
`172.25.52.148` — is correctly targeted. **The remaining work is entirely on
NODE B**: remove the two auto-created `ollama.exe` inbound Block rules, then add
the allow rule. Both commands are in the blocker section above.

**Set on NODE A** (already in `.env`):

```bash
RG_LLM_BASE_URL=http://172.25.54.48:11434
```

> 🔴 **NODE B's firewall rule has NOT been applied.** Port 11434 is governed only
> by the Windows network profile — acceptable on a trusted network, **not** for
> the hospital deployment.
>
> ⚠️ **The `netsh` one-liner this runbook used to give here was not enough**, and
> is deliberately removed: in Windows Firewall a **Block rule beats an Allow
> rule**, so adding an allow while the two `ollama.exe` blocks exist changes
> nothing and looks like a network fault. Use the two-step fix in the blocker
> section above — remove the blocks *first*.

> ⚠️ An earlier version of this table had the two roles **backwards**, and
> [ADR 0006](adr/0006-node-roles-corrected.md) — which fixed that — then attached the
> GPU to the wrong machine. Verified on 2026-09-14: **the RTX 3050 is NODE B's**, so
> Ollama living on NODE B is correct, and `qwen3:4b` was derived from the right hardware.

---

## Fallback chain — in order

| # | Option | Notes |
|---|---|---|
| 1 | **Managed switch / router** | Best. Own DHCP. Does **not** need internet. |
| 2 | Dedicated travel router | Same, portable. |
| 3 | Phone hotspot | Works. **IPs may shift on reconnect** — re-check after every reconnect. |
| 4 | Direct Ethernet | No router. Static `10.0.0.1` (NODE A) / `10.0.0.2` (NODE B). |
| 5 | Cloud API | **Last resort. Breaks the on-prem claim. Requires written approval.** |

### ⚠️ Usually not usable: campus, hotel, or guest wifi — **but measure, don't assume**

Client isolation blocks machine-to-machine traffic **even when both machines show "connected"**. This is the single most common wasted hour.

**The second most common wasted hour is assuming isolation that isn't there.**
`Studentwifi_5G` — a campus network — was measured on 2026-09-14 and **permits
peer traffic**. Had we trusted the rule of thumb, we would have chased a hotspot
instead of finding the actual blocker, which was a firewall rule on NODE B.

**Test with the port, never with `ping`:**

```powershell
Test-NetConnection <OTHER_NODE_IP> -Port 11434     # Windows to Windows
```

```bash
nc -vz <OTHER_NODE_IP> 11434                        # if the far side is Linux
```

⛔ **A silent `ping` proves nothing between two Windows machines** — inbound ICMP
is blocked by default, so it fails on a perfectly good network. Only trust `ping`
where the far side is Linux, or ICMP is known to be permitted.

`TcpTestSucceeded : False` → check the **far side's firewall first** (blocked
program rules beat allow rules), and only then suspect the network. Do not debug
the application.

---

## Addressing

- Use **static IPs or DHCP reservations**.
- **Never rely on hostnames resolving.** mDNS/NetBIOS name resolution between a Linux container and a Windows host fails in ways that look like application bugs.

## Finding each machine's IP

**Both machines are currently Windows 11**, so this is the command on each:

```powershell
# NODE A (LAPTOP-06ER0HBM) and NODE B (LAPTOP-5JCGN9SJ)
Get-NetIPConfiguration |
  Where-Object { $_.NetAdapter.Status -eq 'Up' -and $_.IPv4DefaultGateway } |
  Select-Object InterfaceAlias,
                @{n='IP';e={$_.IPv4Address.IPAddress}},
                @{n='GW';e={$_.IPv4DefaultGateway.NextHop}}
```

⛔ **Do not use `ipconfig | Select-String IPv4`.** It lists every address on the
machine, including stale static ones on **disconnected** adapters, with nothing
to tell you which is live. On 2026-09-14 that is exactly what happened on NODE B:
a dead Ethernet adapter held `172.18.30.66`, the setup script reported it as the
LAN IP, and it even answered `/api/tags` — because a request from a machine to
its own address never leaves the box. The check looked green while NODE A could
never have reached it. **An address only counts if its adapter is `Up` and it has
a default gateway.**

If NODE A is later moved to the hospital's Linux box, as the build plan assumes:

```bash
hostname -I | awk '{print $1}'
```

---

## ⚠️ The Docker gotcha

Inside NODE A's **API container**, these do **NOT** reach NODE B:

- `localhost` → the container itself
- `127.0.0.1` → the container itself
- `host.docker.internal` → NODE A's host, not NODE B

**Always use the literal LAN IP of NODE B.**

```bash
RG_LLM_BASE_URL=http://192.168.1.50:11434
```

Verify from **inside** the container, which is the only test that proves the real path:

```bash
docker compose exec api python -c \
  "import httpx; print(httpx.get('http://192.168.1.50:11434/api/tags', timeout=5).status_code)"
```

---

## Switching targets — one line

`.env` on NODE A:

```bash
RG_LLM_BASE_URL=http://192.168.1.50:11434    # router
RG_LLM_BASE_URL=http://172.20.10.3:11434     # phone hotspot
RG_LLM_BASE_URL=http://10.0.0.2:11434        # direct ethernet
RG_LLM_ENABLED=false                         # forced degrade / kill switch
```

Then `docker compose up -d api worker`. No rebuild needed.

---

## NODE B required settings

```bash
OLLAMA_HOST=0.0.0.0:11434        # bind to the LAN, not just localhost
OLLAMA_KEEP_ALIVE=-1             # pin in VRAM; without this the first request
                                 # after ~5 min idle stalls 20+ seconds
OLLAMA_NUM_PARALLEL=2
OLLAMA_MAX_LOADED_MODELS=1
```

Firewall: allow inbound **TCP 11434 from NODE A's IP only** — never `0.0.0.0/0`.

Applied by `infra/nodeb/setup-windows.ps1` or `infra/nodeb/setup.sh`.

---

## Diagnosing, in order

| # | Check | Command | If it fails |
|---|---|---|---|
| 1 | Same subnet? | `Get-NetIPConfiguration \| ? { $_.NetAdapter.Status -eq 'Up' -and $_.IPv4DefaultGateway }` on both — **not** `ipconfig`, which lists stale addresses on disconnected adapters | Fix addressing before anything else |
| 2 | Reachable? | `Test-NetConnection <NODE_B_IP> -Port 11434` — **never `ping`**, it false-negatives on Windows | Far side's firewall first (a Block rule beats an Allow rule), then client isolation → change network |
| 3 | Port open? | `curl http://<NODE_B_IP>:11434/api/tags` from NODE A host | Firewall rule, or `OLLAMA_HOST` still bound to localhost |
| 4 | Reachable **from the container**? | `docker compose exec api python -c "import httpx; print(httpx.get('http://<IP>:11434/api/tags', timeout=5).status_code)"` | Wrong IP in `.env` — check for `localhost` |
| 5 | Model loaded? | `ollama ps` on NODE B | `ollama pull <model>`; check `OLLAMA_KEEP_ALIVE=-1` |
| 6 | App sees it? | `curl http://localhost/api/health` on NODE A | Check `RG_LLM_ENABLED`; probe caches for 30s so wait before re-testing |

---

## What "broken" actually means here

**It does not mean the system is down.** If NODE B is unreachable:

- ✅ Tracking, flags, timers, escalation, SMS, dashboard and retrieval all keep working
- ❌ Only generated prose is lost; retrieved guideline chunks are still shown
- `/api/health` returns **200** with `"llm": {"reachable": false}` and `"degraded_features": ["llm_generation"]`
- The dashboard header reads *"AI: offline — core tracking unaffected"*

Phase 10.4 classifies **NODE B unreachable as a low-priority alert — it is not an outage.**

If NODE B being down ever produces a non-200 from `/api/health`, or stops a timer firing, **that is a RULE 2 violation and a release blocker.**
