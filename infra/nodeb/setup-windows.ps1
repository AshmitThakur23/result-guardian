<#
.SYNOPSIS
    Provision NODE B (inference server) on Windows.

.DESCRIPTION
    The build plan assumes NODE B is Ubuntu with systemd. The current NODE B is
    a Windows 11 laptop with an RTX 3050, so this is the Windows equivalent of
    infra/nodeb/setup.sh. Both ship; the env vars are identical.

    NODE B holds no patient data, writes nothing but model weights, and can be
    wiped and rebuilt in minutes. See docs/adr/0002-two-node-split.md.

.NOTES
    Firewall rule needs an elevated shell. Everything else does not.
#>

[CmdletBinding()]
param(
    [string]$Model       = "qwen3:4b",
    # 4 GB VRAM on this box, so 4b. docs/adr/0005-node-roles-and-model.md
    [string]$ModelsPath  = "D:\ollama-models",
    # NODE A's LAN IP. The firewall rule allows 11434 from this address only.
    [string]$NodeAIp     = "",
    [switch]$SkipFirewall
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn2($m)  { Write-Host "    !!  $m"   -ForegroundColor Yellow }

# ── 1. Ollama present? Do not install what is already there. ──────────
Write-Step "Checking for Ollama"
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $candidate) { $ollama = $candidate } else {
        Write-Warn2 "Ollama not found. Install from https://ollama.com/download, then re-run."
        exit 1
    }
} else { $ollama = $ollama.Source }
Write-Ok "Ollama at $ollama"
& $ollama --version

# ── 2. Environment ────────────────────────────────────────────────────
# OLLAMA_KEEP_ALIVE=-1 pins the model in VRAM. Without it, the first request
# after ~5 minutes idle stalls 20+ seconds while the model reloads -- which
# would look like NODE B being down and trip the 30s timeout on NODE A.
Write-Step "Setting user environment variables"
$envVars = @{
    "OLLAMA_HOST"              = "0.0.0.0:11434"   # bind to the LAN, not just localhost
    "OLLAMA_KEEP_ALIVE"        = "-1"
    "OLLAMA_NUM_PARALLEL"      = "2"
    "OLLAMA_MAX_LOADED_MODELS" = "1"
    "OLLAMA_MODELS"            = $ModelsPath       # C: is nearly full; keep weights on D:
}
foreach ($k in $envVars.Keys) {
    [Environment]::SetEnvironmentVariable($k, $envVars[$k], "User")
    Set-Item -Path "Env:$k" -Value $envVars[$k]
    Write-Ok "$k = $($envVars[$k])"
}

if (-not (Test-Path $ModelsPath)) {
    New-Item -ItemType Directory -Path $ModelsPath -Force | Out-Null
    Write-Ok "Created $ModelsPath"
}

# ── 3. Restart Ollama so it picks up the new environment ──────────────
Write-Step "Restarting Ollama"
Get-Process ollama, "ollama app" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
Start-Sleep -Seconds 4
Write-Ok "Ollama serving on 0.0.0.0:11434"

# ── 4. Pull the model NOW, not on first request ───────────────────────
# Phase 0.3: "Model pulled and cached at provisioning time, never at first
# request." A doctor clicking Explain must not wait for a 2 GB download.
Write-Step "Pulling $Model (cached at provisioning, never at first request)"
& $ollama pull $Model
Write-Ok "$Model cached"

# ── 5. Firewall: 11434 from NODE A only, not 0.0.0.0/0 ────────────────
if (-not $SkipFirewall) {
    Write-Step "Firewall rule for TCP 11434"
    $isAdmin = ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    if (-not $isAdmin) {
        Write-Warn2 "Not elevated. Run this in an Administrator shell:"
        $scope = if ($NodeAIp) { $NodeAIp } else { "<NODE_A_IP>" }
        Write-Host "    netsh advfirewall firewall add rule name=`"Result Guardian NODE B`" ``"
        Write-Host "        dir=in action=allow protocol=TCP localport=11434 remoteip=$scope"
    } elseif (-not $NodeAIp) {
        Write-Warn2 "-NodeAIp not given. Skipping: a rule open to 0.0.0.0/0 is refused by design (Phase 10.1)."
    } else {
        netsh advfirewall firewall delete rule name="Result Guardian NODE B" 2>$null | Out-Null
        netsh advfirewall firewall add rule name="Result Guardian NODE B" `
            dir=in action=allow protocol=TCP localport=11434 remoteip=$NodeAIp | Out-Null
        Write-Ok "TCP 11434 allowed from $NodeAIp only"
    }
}

# ── 6. Verify ─────────────────────────────────────────────────────────
Write-Step "Verifying"
try {
    $tags = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 10
    Write-Ok "/api/tags reachable. Models: $($tags.models.name -join ', ')"
} catch {
    Write-Warn2 "/api/tags did not respond: $_"
    exit 1
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
       Select-Object -First 1 -ExpandProperty IPAddress)

Write-Host "`n=== NODE B READY ===" -ForegroundColor Green
Write-Host "  This machine's LAN IP : $ip"
Write-Host "  Set on NODE A         : RG_LLM_BASE_URL=http://${ip}:11434"
Write-Host "  Verify from NODE A    : curl http://${ip}:11434/api/tags"
Write-Host "`n  Note: 'localhost' and 'host.docker.internal' inside NODE A's API"
Write-Host "  container do NOT reach this machine. Use the literal IP above."
