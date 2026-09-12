# Network Runbook — NODE A ⇄ NODE B

> Phase 0.4. Read this before connecting the two machines.
> **The link does not need internet access.** It only joins the two nodes. Hospital internet can be completely down and NODE A ⇄ NODE B still works.

## Current machines

| Role | Machine | IP | Status |
|---|---|---|---|
| **NODE A** — core | Abhinendra's laptop (`LAPTOP-06ER0HBM`, Windows 11) | _to be recorded_ | Docker present; stack never run |
| **NODE B** — inference | Ashmit's machine (`LAPTOP-5JCGN9SJ`) | _to be recorded_ | **not yet provisioned.** RTX 3050, 4 GB VRAM — verified with `nvidia-smi` |

Record the real IPs here once both are on the same network.

> ⚠️ An earlier version of this table had the two roles **backwards**. NODE A is
> the development laptop. See [ADR 0006](adr/0006-node-roles-corrected.md).
> Ollama being installed on NODE A is incidental and is **not** NODE B
> provisioning — `infra/nodeb/setup-windows.ps1` still has to run on NODE B,
> and its firewall rule must allow TCP 11434 from **NODE A's** IP.

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
