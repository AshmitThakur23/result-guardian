#!/usr/bin/env bash
# Provision NODE B (inference server) on Ubuntu.
#
# This is the path for the real hospital GPU box. The current development
# NODE B is Windows -- see setup-windows.ps1. The environment variables are
# identical either way.
#
#   sudo ./setup.sh --node-a-ip 192.168.1.10 --model qwen3:4b
#
# NODE B holds no patient data and writes nothing but model weights. It can be
# wiped and rebuilt in under 15 minutes; that is why it needs no backup.
# See docs/adr/0002-two-node-split.md.

set -euo pipefail

MODEL="${MODEL:-qwen3:4b}"
NODE_A_IP="${NODE_A_IP:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)     MODEL="$2"; shift 2 ;;
    --node-a-ip) NODE_A_IP="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

step() { printf '\n==> %s\n' "$1"; }
ok()   { printf '    OK  %s\n' "$1"; }
warn() { printf '    !!  %s\n' "$1"; }

# ── 1. Ollama: check before installing ────────────────────────────────
step "Checking for Ollama"
if command -v ollama >/dev/null 2>&1; then
  ok "already installed: $(ollama --version 2>&1 | head -1)"
else
  warn "not found, installing"
  curl -fsSL https://ollama.com/install.sh | sh
fi

# ── 2. systemd override ───────────────────────────────────────────────
# OLLAMA_KEEP_ALIVE=-1 pins the model in VRAM. Without it the first request
# after ~5 min idle stalls 20+ seconds reloading, which trips NODE A's 30s
# timeout and looks like an outage.
step "Writing systemd override"
install -d /etc/systemd/system/ollama.service.d
cat >/etc/systemd/system/ollama.service.d/override.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
EOF
systemctl daemon-reload
systemctl enable --now ollama
systemctl restart ollama
sleep 3
ok "ollama.service active"

# ── 3. Pull at provisioning, never at first request ───────────────────
step "Pulling ${MODEL}"
ollama pull "${MODEL}"
ok "${MODEL} cached"

# ── 4. Firewall: NODE A only, never 0.0.0.0/0 ─────────────────────────
step "Firewall"
if [[ -z "${NODE_A_IP}" ]]; then
  warn "--node-a-ip not given; refusing to open 11434 to the world (Phase 10.1)"
elif command -v ufw >/dev/null 2>&1; then
  ufw allow from "${NODE_A_IP}" to any port 11434 proto tcp
  ok "TCP 11434 allowed from ${NODE_A_IP} only"
else
  warn "ufw not present; add an equivalent rule for TCP 11434 from ${NODE_A_IP}"
fi

# ── 5. Verify ─────────────────────────────────────────────────────────
step "Verifying"
curl -fsS http://localhost:11434/api/tags >/dev/null && ok "/api/tags reachable"

IP="$(hostname -I | awk '{print $1}')"
cat <<EOF

=== NODE B READY ===
  This machine's LAN IP : ${IP}
  Set on NODE A         : RG_LLM_BASE_URL=http://${IP}:11434
  Verify from NODE A    : curl http://${IP}:11434/api/tags

  Note: 'localhost' and 'host.docker.internal' inside NODE A's API container
  do NOT reach this machine. Use the literal IP above.
EOF
