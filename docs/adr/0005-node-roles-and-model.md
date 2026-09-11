# ADR 0005 — Node role assignment and model choice for the current hardware

**Status:** Accepted · **Date:** 2026-09-11 · **Phase:** 0.3 / 0.4

## Context

[ADR 0002](0002-two-node-split.md) fixes the *roles*. This ADR records which physical machine takes which role, and the consequences of the hardware actually available.

## Decision

| Role | Machine | Detail |
|---|---|---|
| **NODE B — inference** | This laptop | NVIDIA RTX 3050 Laptop, **4 GB VRAM**, Windows 11. Ollama `v0.15.2` already installed. |
| **NODE A — core** | The other laptop | Not yet provisioned. Pulls this repo. |

**Model: `qwen3:4b` (q4), not `qwen3:8b`.**

## Rationale

**The 8B does not fit.** Qwen3-8B at q4 needs roughly 5–6 GB of VRAM before context. The RTX 3050 Laptop has 4 GB. Ollama would partially offload to CPU, and generation would very likely exceed the 30 s timeout in `LLM_TIMEOUT_S` — making NODE B look *permanently degraded* during development and teaching us nothing about the connected path.

`qwen3:4b` at q4 is roughly 2.5 GB, leaving headroom for context. It stays resident, so `OLLAMA_KEEP_ALIVE=-1` behaves as designed instead of thrashing.

**This costs nothing structurally.** `LLM_MODEL` is an environment variable. Nothing depends on the LLM until Phase 8, which is ~15 weeks out. The build plan already says "start 8B q4, benchmark 14B/32B" — benchmarking on the hospital's actual GPU box is the step that settles it. A 4 GB laptop GPU was never going to be the production answer.

## Consequences and deviations from the build plan

1. **NODE B is Windows, not Ubuntu.** The plan's Phase 0.3 assumes `setup.sh` plus a systemd override. The repo therefore carries **both** `infra/nodeb/setup.sh` (Ubuntu — stays valid for the real hospital box) and `infra/nodeb/setup-windows.ps1` (`setx` + `netsh advfirewall`). The environment variables are identical either way.
2. **Ollama models move to `D:`.** `C:` is ~93% full (17 GB free); `D:` has 178 GB. `OLLAMA_MODELS=D:\ollama-models`. Docker's disk image moves to `D:` for the same reason.
3. **Development happens on NODE B.** NODE A code is authored on this laptop and pushed; the other machine pulls and runs it. This is backwards from the deployment topology and is a temporary development arrangement, not an architectural statement.
4. **Exit Gate 0 cannot fully close yet.** Its "fresh machine" clause and the NODE-A-to-NODE-B reachability check both require the second machine. The *degraded* path — `llm.reachable: false`, `status: ok`, no error — is testable today and is the half that actually proves RULE 2.

## Pre-install scan (2026-09-11)

Per the user's standing rule, nothing was installed without first checking both drives:

| Tool | Found | Action |
|---|---|---|
| Ollama | ✅ `v0.15.2`, `%LOCALAPPDATA%\Programs\Ollama` | configure only |
| Docker + Compose | ✅ `29.7.2` / `v5.4.0` | start daemon |
| Git / gh | ✅ `2.49.0` / `2.78.0` | switch account to `AshmitThakur23` |
| Python / Node | ✅ `3.13.7` / `v22.18.0` | API runs 3.11 in container |
| PostgreSQL | ⚠️ native install present | dev compose maps `5433:5432` to avoid the port clash |
| `make` | ❌ absent | `tasks.ps1` provided alongside the `Makefile` |
