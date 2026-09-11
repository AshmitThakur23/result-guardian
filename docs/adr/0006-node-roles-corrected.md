# ADR 0006 — Node roles, corrected: the development machine is NODE A

**Status:** Accepted · **Date:** 2026-09-11 · **Phase:** 0.3 / 0.4
**Supersedes:** [ADR 0005](0005-node-roles-and-model.md)

## Context

[ADR 0002](0002-two-node-split.md) fixes the *roles*: NODE A holds all state and all safety logic, NODE B is a stateless GPU accelerator. [ADR 0005](0005-node-roles-and-model.md) then assigned those roles to physical machines — **and got them backwards.**

It recorded the development laptop (the one carrying the RTX 3050) as NODE B, reasoning from the presence of the GPU, and described the arrangement as "development happens on NODE B… backwards from the deployment topology". The user corrected this on 2026-09-11: **the development machine is NODE A.**

The error had spread to seven tracked files before it was caught, which is exactly the failure the tracking protocol exists to prevent. It is recorded here rather than edited away.

## Decision

| Role | Machine | Runs |
|---|---|---|
| **NODE A — core** | **Abhinendra's laptop** — the machine the work is done on | Postgres, FastAPI, worker, Caddy, React dashboard. **All patient data. All safety logic.** |
| **NODE B — inference** | **Ashmit's machine** — the repo owner's | **Ollama only.** GPU work. Stateless, no database, no patient data at any moment. |

The repo remains `AshmitThakur23/result-guardian`, and commits are authored by `AshmitThakur23 <ashmitthakur615@gmail.com>` from either machine.

## Rationale

This is not a change of design — [ADR 0002](0002-two-node-split.md) is untouched. It is a correction of which physical box holds which role, which the user is the authority on.

It also makes the arrangement *less* strange than ADR 0005 described. Under the corrected assignment, development happens on NODE A, the node that actually runs the stack — so the developer's machine and the deployment target are the same role, and ADR 0005's "this is backwards from the deployment topology" caveat simply dissolves. The earlier assignment was the awkward one.

## Consequences

**1. The model choice is now unfounded and must be re-derived.**
ADR 0005 chose `qwen3:4b` over `qwen3:8b` because "the RTX 3050 Laptop has 4 GB" of VRAM. That GPU is in **NODE A**, where inference never runs. **NODE B's GPU and VRAM are unknown** and cannot be inspected from NODE A.

This costs nothing structurally and nothing urgently: `RG_LLM_MODEL` is an environment variable, and **nothing touches NODE B until Phase 8** — roughly 15 weeks out. But `qwen3:4b` must be treated as a placeholder, not a decision, until it is re-derived from Ashmit's actual hardware. The build plan's own instruction stands: benchmark before promising anything.

**2. Ollama is installed on the wrong node.**
Ollama `v0.15.2` is present on NODE A, where it is unnecessary. Harmless, but it is **not** NODE B provisioning. `infra/nodeb/setup-windows.ps1` / `setup.sh` still has to run on **Ashmit's** machine, and the firewall rule there must allow TCP 11434 from **NODE A's** IP.

**3. The "wait for NODE A" blocker needs re-reading.**
`PROGRESS.md` recorded an agreement to "stand up NODE A on the other machine first" and run nothing further until then. Under the corrected roles, **NODE A is the machine already in hand**, with Docker present. The critical path is therefore not what it appeared to be — see the RESUME HERE block in [`../../PROGRESS.md`](../../PROGRESS.md).

**4. Exit Gate 0's cross-node clause still cannot close.**
It needs both machines on one LAN and a `curl http://<NODE_B_IP>:11434/api/tags` from NODE A. Open decision #5 (real IPs, network method) remains undecided, and NODE B is not provisioned.

## Reversal condition

None. This records which machine belongs to whom, which is a fact, not a trade-off.
