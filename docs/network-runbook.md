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

## 🔴 The two machines are on DIFFERENT NETWORKS — measured 2026-09-14

**This, not the firewall rule, is the current blocker.**

| | NODE A | NODE B |
|---|---|---|
| Address | `172.25.52.148` | `192.168.0.168` |
| Mask | /20 → `172.25.48.0`–`172.25.63.255` | /24 → `192.168.0.0`–`192.168.0.255` |
| Gateway | `172.25.48.1` | `192.168.0.1` |

`192.168.0.168` is **not** in NODE A's subnet, and the gateways differ — these
are two separate routers. Measured from NODE A:

```
Test-NetConnection 192.168.0.168 -Port 11434
  PingSucceeded    = False
  TcpTestSucceeded = False
```

**Adding a firewall rule now would achieve nothing**, and the address it was
scoped to would be stale. NODE A was on `192.168.0.156` earlier the same day —
the same subnet as NODE B — and has since moved to the campus Wi-Fi. **A DHCP
address on a network you do not control is not a stable basis for a firewall
rule.**

⚠️ **`Studentwifi_5G` is a further problem even if both machines join it.**
Campus and guest networks almost always run **client isolation**, which blocks
machine-to-machine traffic while both devices show "connected" — see the
warning below, which calls this *"the single most common wasted hour"*. Test
with `ping` before trusting it.

**What actually unblocks this**, in order of preference:

1. **Both machines back on the `192.168.0.x` router.** NODE B is already there,
   NODE A was there this morning, and NODE B's address is already recorded. Then
   NODE A's IP will be `192.168.0.x` — re-read it and scope the rule to that.
2. **Phone hotspot.** Works, and is immune to campus client isolation. IPs shift
   on every reconnect, so re-read both and update `RG_LLM_BASE_URL`.
3. **Ethernet cable between the two laptops**, static addressing. Most reliable,
   least convenient.

**Set on NODE A once both machines are on the same network** (already set in
`.env`, and correct for option 1):

```bash
RG_LLM_BASE_URL=http://172.25.54.48:11434
```

> 🔴 **NODE B's firewall rule has NOT been applied**, and should not be applied
> until the two machines are on one network and NODE A's address on *that*
> network is known. It needs NODE A's IP, and the script refuses `0.0.0.0/0` by
> design (Phase 10.1). Until it is added, port 11434 is governed only by the
> Windows network profile — acceptable on a trusted home network, **not** for
> the hospital deployment. Run on **NODE B**, elevated:
>
> ```powershell
> netsh advfirewall firewall add rule name="Result Guardian NODE B" `
>     dir=in action=allow protocol=TCP localport=11434 remoteip=<NODE_A_IP>
> ```

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

### ⛔ Not usable: campus, hotel, or guest wifi

Client isolation blocks machine-to-machine traffic **even when both machines show "connected"**. This is the single most common wasted hour. Always test with `ping` before trusting a network:

```bash
ping 192.168.1.50        # from NODE A, to NODE B
```

No reply → switch to a hotspot or a cable. Do not debug the application.

---

## Addressing

- Use **static IPs or DHCP reservations**.
- **Never rely on hostnames resolving.** mDNS/NetBIOS name resolution between a Linux container and a Windows host fails in ways that look like application bugs.

## Finding each machine's IP

**Both machines are currently Windows 11**, so this is the command on each:

```powershell
# NODE A (LAPTOP-06ER0HBM) and NODE B (LAPTOP-5JCGN9SJ)
ipconfig | Select-String IPv4
```

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
| 1 | Same subnet? | `ipconfig` / `hostname -I` on both | Fix addressing before anything else |
| 2 | Reachable? | `ping <NODE_B_IP>` | Client isolation → change network |
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
