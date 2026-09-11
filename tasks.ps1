<#
.SYNOPSIS
    Windows equivalent of the Makefile. `make` is not installed on the dev box.

.EXAMPLE
    ./tasks.ps1 up
    ./tasks.ps1 test
    ./tasks.ps1 revision -Message "add patients"
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("help","up","down","logs","ps","health","dx","psql","migrate",
                 "revision","test","lint","fmt","typecheck","check","nodeb-up","clean")]
    [string]$Task = "help",

    [string]$Message = ""
)

$ErrorActionPreference = "Stop"
$compose = @("docker","compose")
$dbUser  = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "rg_app" }
$dbName  = if ($env:POSTGRES_DB)   { $env:POSTGRES_DB }   else { "result_guardian" }

function Invoke-Compose { & $compose[0] $compose[1] @args }

switch ($Task) {
    "help" {
        Write-Host "`nResult Guardian tasks`n" -ForegroundColor Cyan
        @(
            @("up",        "Build and start the NODE A stack"),
            @("down",      "Stop the stack (data kept)"),
            @("logs",      "Tail api + worker logs"),
            @("ps",        "Show service status"),
            @("health",    "Hit /api/health"),
            @("dx",        "List installed Postgres extensions"),
            @("psql",      "Open a psql shell"),
            @("migrate",   "Apply migrations"),
            @("revision",  "New migration: ./tasks.ps1 revision -Message '...'"),
            @("test",      "Run tests (must pass with NODE B unreachable)"),
            @("lint",      "ruff + black --check"),
            @("fmt",       "Format in place"),
            @("typecheck", "mypy"),
            @("check",     "Everything CI runs"),
            @("nodeb-up",  "Start Ollama (run on NODE B, not NODE A)"),
            @("clean",     "Stop and DELETE all data")
        ) | ForEach-Object { "  {0,-12} {1}" -f $_[0], $_[1] }
        Write-Host ""
    }
    "up"        { Invoke-Compose up -d --build }
    "down"      { Invoke-Compose down }
    "logs"      { Invoke-Compose logs -f api worker }
    "ps"        { Invoke-Compose ps }
    "health"    { (Invoke-WebRequest -Uri "http://localhost/api/health" -UseBasicParsing).Content }
    "dx"        { Invoke-Compose exec postgres psql -U $dbUser -d $dbName -c '\dx' }
    "psql"      { Invoke-Compose exec postgres psql -U $dbUser -d $dbName }
    "migrate"   { Invoke-Compose exec api alembic upgrade head }
    "revision"  {
        if (-not $Message) { Write-Error "-Message is required"; exit 1 }
        Invoke-Compose exec api alembic revision --autogenerate -m $Message
    }
    "test"      { Invoke-Compose run --rm api pytest }
    "lint"      { Invoke-Compose run --rm api sh -c "ruff check . && black --check ." }
    "fmt"       { Invoke-Compose run --rm api sh -c "ruff check --fix . && black ." }
    "typecheck" { Invoke-Compose run --rm api mypy app worker }
    "check"     {
        Invoke-Compose run --rm api sh -c "ruff check . && black --check ."
        Invoke-Compose run --rm api mypy app worker
        Invoke-Compose run --rm api pytest
    }
    "nodeb-up"  { Invoke-Compose -f docker-compose.nodeb.yml up -d }
    "clean"     {
        Write-Host "This DELETES all database data." -ForegroundColor Yellow
        Invoke-Compose down -v
    }
}
