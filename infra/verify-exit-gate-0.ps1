<#
.SYNOPSIS
    Verify Exit Gate 0 from NODE A, by hand. Run it yourself; trust nothing.

.DESCRIPTION
    Every claim this project makes about the two-node link is re-measured here,
    live, and printed as PASS or FAIL. Nothing is read from a doc, and nothing
    is taken from a previous run -- per CLAUDE.md, "a config value is not
    verified until something has READ it."

    The important checks are 4 and 5. They run from INSIDE the api container,
    because that is the only path that matters: from inside a container,
    `localhost`, `127.0.0.1` and `host.docker.internal` all resolve to the
    wrong machine, so a host-level success proves nothing about the app.

.PARAMETER NodeBIp
    NODE B's LAN IP. Defaults to the address recorded in the runbook. If NODE B
    has moved (it is DHCP), pass the new one.

.PARAMETER SkipTests
    Skip the full backend suite (check 8). It takes a few minutes.

.EXAMPLE
    .\infra\verify-exit-gate-0.ps1
    .\infra\verify-exit-gate-0.ps1 -NodeBIp 172.25.54.48 -SkipTests
#>
param(
    [string] $NodeBIp = "172.25.54.48",
    [switch] $SkipTests
)

$ErrorActionPreference = "Continue"
$script:Pass = 0
$script:Fail = 0

function Check {
    param([string] $Name, [scriptblock] $Test, [string] $Expect)
    Write-Host ""
    Write-Host "-> $Name" -ForegroundColor Cyan
    if ($Expect) { Write-Host "   expect: $Expect" -ForegroundColor DarkGray }
    try {
        $result = & $Test
        if ($result.Ok) {
            $script:Pass++
            Write-Host "   PASS   $($result.Detail)" -ForegroundColor Green
        } else {
            $script:Fail++
            Write-Host "   FAIL   $($result.Detail)" -ForegroundColor Red
        }
    } catch {
        $script:Fail++
        Write-Host "   FAIL   threw: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== Exit Gate 0 -- live verification from NODE A ===" -ForegroundColor White
Write-Host "    $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')   NODE B target: $NodeBIp"

# 1 ---------------------------------------------------------------------
Check "NODE A's own address (adapter filter, NOT ipconfig)" {
    $cfg = Get-NetIPConfiguration |
           Where-Object { $_.NetAdapter.Status -eq 'Up' -and $_.IPv4DefaultGateway } |
           Select-Object -First 1
    if (-not $cfg) { return @{ Ok = $false; Detail = "no connected adapter with a default route" } }
    $ip = $cfg.IPv4Address.IPAddress
    @{ Ok = $true; Detail = "$ip via $($cfg.InterfaceAlias), gw $($cfg.IPv4DefaultGateway.NextHop)" }
} "one address, on an adapter that is Up and has a gateway"

# 2 ---------------------------------------------------------------------
Check "Both nodes on the same /20 subnet" {
    $cfg = Get-NetIPConfiguration |
           Where-Object { $_.NetAdapter.Status -eq 'Up' -and $_.IPv4DefaultGateway } |
           Select-Object -First 1
    $a = $cfg.IPv4Address.IPAddress
    # /20 -> compare the first two octets and the third masked to /20
    $oa = $a.Split('.'); $ob = $NodeBIp.Split('.')
    $same = ($oa[0] -eq $ob[0]) -and ($oa[1] -eq $ob[1]) -and
            ([int]$oa[2] -band 240) -eq ([int]$ob[2] -band 240)
    @{ Ok = $same; Detail = "NODE A $a  vs  NODE B $NodeBIp" }
} "same subnet -- if this fails, nothing below can work"

# 3 ---------------------------------------------------------------------
Check "TCP to NODE B:11434 from the NODE A host" {
    $t = Test-NetConnection $NodeBIp -Port 11434 -WarningAction SilentlyContinue
    @{ Ok = $t.TcpTestSucceeded
       Detail = "TcpTestSucceeded=$($t.TcpTestSucceeded)  PingSucceeded=$($t.PingSucceeded) (ping False is EXPECTED - Windows blocks inbound ICMP)" }
} "TcpTestSucceeded True. Ignore ping entirely."

# 4 ---------------------------------------------------------------------
Check "*** Ollama reachable FROM INSIDE the api container ***" {
    $py = "import httpx;r=httpx.get('http://${NodeBIp}:11434/api/tags',timeout=10);" +
          "print(r.status_code, ','.join(m['name'] for m in r.json().get('models',[])))"
    $out = docker compose exec -T api python -c $py 2>&1 | Out-String
    $out = $out.Trim()
    @{ Ok = $out.StartsWith("200"); Detail = $out }
} "200 and the model list. THIS is the path that actually matters."

# 5 ---------------------------------------------------------------------
Check "The container's LLM address matches what you think it is" {
    $envUrl = (docker compose exec -T api printenv RG_LLM_BASE_URL 2>&1 | Out-String).Trim()
    $ok = $envUrl -eq "http://${NodeBIp}:11434"
    @{ Ok = $ok; Detail = "container has '$envUrl' (expected 'http://${NodeBIp}:11434')" }
} "ask the PROCESS, not the .env file -- editing .env changes nothing until the container is recreated"

# 6 ---------------------------------------------------------------------
Check "/api/health through Caddy" {
    $raw = (Invoke-WebRequest -Uri "http://localhost/api/health" -UseBasicParsing -TimeoutSec 20)
    $j = $raw.Content | ConvertFrom-Json
    $ok = ($raw.StatusCode -eq 200) -and ($j.status -eq "ok") -and ($j.db -eq "ok")
    @{ Ok = $ok
       Detail = "HTTP $($raw.StatusCode)  status=$($j.status)  db=$($j.db)  worker_heartbeat_age_s=$($j.worker_heartbeat_age_s)  llm.reachable=$($j.llm.reachable) $(if($j.llm.latency_ms){"($($j.llm.latency_ms) ms)"})  degraded=[$($j.degraded_features -join ',')]" }
} "HTTP 200, status ok, db ok. 200 even when NODE B is DOWN -- that is RULE 2."

# 7 ---------------------------------------------------------------------
Check "Worker is alive and draining queues" {
    $j = (Invoke-WebRequest -Uri "http://localhost/api/health" -UseBasicParsing -TimeoutSec 20).Content | ConvertFrom-Json
    $age = [int]$j.worker_heartbeat_age_s
    $logs = docker compose logs --tail=40 worker 2>&1 | Out-String
    $handled = ([regex]::Matches($logs, '"event": "message_handled"')).Count
    @{ Ok = ($age -lt 60)
       Detail = "heartbeat ${age}s old; $handled 'message_handled' events in the last 40 log lines" }
} "heartbeat under 60s. This keeps ticking with NODE B dead."

# 8 ---------------------------------------------------------------------
if ($SkipTests) {
    Write-Host ""
    Write-Host "-> Full backend test suite" -ForegroundColor Cyan
    Write-Host "   SKIPPED (-SkipTests)" -ForegroundColor Yellow
} else {
    Check "Full backend test suite" {
        Write-Host "   running... this takes a few minutes" -ForegroundColor DarkGray
        docker compose exec -T api python -m pytest tests/ -q -p no:cacheprovider -p no:warnings --tb=line 2>&1 | Out-Null
        $code = $LASTEXITCODE
        @{ Ok = ($code -eq 0); Detail = "pytest exit code $code (0 = every test passed)" }
    } "exit code 0 -- and it must STILL be 0 with NODE B switched off"
}

# ------------------------------------------------------------------------
Write-Host ""
Write-Host ("=" * 64)
if ($script:Fail -eq 0) {
    Write-Host "  ALL $($script:Pass) CHECKS PASSED" -ForegroundColor Green
    Write-Host "  Exit Gate 0 holds, measured live -- not read from a doc." -ForegroundColor Green
} else {
    Write-Host "  $($script:Pass) passed, $($script:Fail) FAILED" -ForegroundColor Red
    Write-Host "  Do not tick anything until these are green." -ForegroundColor Red
}
Write-Host ("=" * 64)
Write-Host ""
Write-Host "To prove RULE 2 yourself: have NODE B stop Ollama (tray app FIRST," -ForegroundColor DarkGray
Write-Host "or it relaunches), then re-run. Checks 3-5 should FAIL and checks" -ForegroundColor DarkGray
Write-Host "6-8 must still PASS, with degraded=[llm_generation]. That is the" -ForegroundColor DarkGray
Write-Host "whole safety claim of this project, in one command." -ForegroundColor DarkGray
Write-Host ""
