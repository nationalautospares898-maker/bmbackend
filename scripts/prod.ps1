# Run a command with the PRODUCTION environment loaded from `.env.production`.
#
# WHY THIS EXISTS
# ---------------
# A bare `uv run alembic upgrade head` in this repository migrates the **local** database,
# and does so silently. `migrations/env.py::_load_dotenv()` reads `.env`, which on a
# development machine necessarily points `BM_MIGRATION_DATABASE_URL` at the database the
# developer is working against. There is no prompt and no confirmation: the command looks
# identical whether it is about to touch Neon or the shop's own working copy.
#
# That is not hypothetical in this project. `bt.ps1` exists for the same class of mistake
# on the test side, and its header records what happened: a bare `uv run pytest` truncated
# the demo database on 2026-10-31 because only one of the two variables had been
# overridden.
#
# So the production path is a different command, not the same command with a different
# file lying around. This script:
#
#   1. loads `.env.production` into the process environment — where it **wins**, because
#      `_load_dotenv()` uses `os.environ.setdefault` and pydantic-settings ranks real
#      environment variables above `env_file`;
#   2. prints the resolved target with the password masked, before anything runs;
#   3. refuses to run `pytest`, which truncates every business table between tests and
#      must never be pointed at Neon;
#   4. runs from the repository root, so `alembic.ini` resolves.
#
# It does **not** decide whether the target is safe. `scripts/production_db.py` does that,
# per command, against the actual server it connects to.
#
# USAGE
#   ./scripts/prod.ps1 python scripts/production_db.py inspect
#   ./scripts/prod.ps1 python scripts/production_db.py migrate --yes
#   ./scripts/prod.ps1 python scripts/production_db.py verify
#   ./scripts/prod.ps1 alembic current
#
# Everything after the script name is passed to `uv run` unchanged.

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Command
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot '.env.production'

if (-not (Test-Path -LiteralPath $envFile)) {
    Write-Error "No .env.production at $envFile. Copy .env.production.example and fill it in."
}

if (-not $Command -or $Command.Count -eq 0) {
    Write-Error "Nothing to run. Example: ./scripts/prod.ps1 python scripts/production_db.py inspect"
}

# The test suite TRUNCATES every business table between tests. `tests/conftest.py` has its
# own allow-list guard keyed on the database name, and `neondb` is not on it — so this is
# belt and braces rather than the only defence. It is here because the failure it prevents
# is unrecoverable and the check costs nothing.
foreach ($token in $Command) {
    if ($token -match '(^|[\\/])pytest(\.exe)?$' -or $token -eq 'pytest') {
        Write-Error "Refusing to run pytest with the production environment loaded. Use bt.ps1."
    }
}

# --- load ---------------------------------------------------------------------
#
# Deliberately minimal parsing, matching `migrations/env.py::_load_dotenv()` so the two
# cannot disagree about what a line in this file means: `KEY=VALUE`, `#` comments and
# blank lines, with one layer of surrounding quotes stripped. No interpolation, no
# `export` prefix, no multi-line values.
$loaded = New-Object System.Collections.Generic.List[string]

foreach ($raw in Get-Content -LiteralPath $envFile -Encoding UTF8) {
    $line = $raw.Trim()
    if ($line.Length -eq 0) { continue }
    if ($line.StartsWith('#')) { continue }
    $split = $line.IndexOf('=')
    if ($split -lt 1) { continue }

    $key = $line.Substring(0, $split).Trim()
    $value = $line.Substring($split + 1).Trim().Trim('"').Trim("'")

    Set-Item -Path "Env:$key" -Value $value
    $loaded.Add($key)
}

if ($loaded.Count -eq 0) {
    Write-Error "$envFile contained no KEY=VALUE lines."
}

# --- report, with the password AND the endpoint id masked ---------------------
function Format-Target([string] $url) {
    if ([string]::IsNullOrWhiteSpace($url)) { return '(not set)' }

    # Two separate redactions, because two different things in this string are
    # sensitive for two different reasons.
    #
    # 1. The password, obviously.
    # 2. The Neon **endpoint id** (`ep-cold-pond-123456`). It is not a credential on its
    #    own, but it is the routable address of the shop's production database, and this
    #    banner is printed on every single production command — so it ends up in terminal
    #    scrollback, in CI logs, in pasted output and in reports. Combined with a leaked
    #    password it is everything an attacker needs, and there is no operational reason
    #    to see it: what the operator needs to confirm is the ROLE, the DATABASE, the
    #    REGION and whether the endpoint is pooled, all of which survive below.
    #
    # Applied to the string rather than through a URI parser, because a placeholder value
    # is not a valid URI and this banner has to stay readable in exactly that case.
    $masked = [regex]::Replace($url, '://([^:/@]+):[^@]*@', '://$1:***@')
    return [regex]::Replace($masked, 'ep-[a-z0-9]+-[a-z0-9]+-?[a-z0-9]*', 'ep-<redacted>')
}

Write-Host ''
Write-Host "env file   $envFile"
Write-Host "variables  $($loaded.Count) loaded: $($loaded -join ', ')"
Write-Host ''
Write-Host "BM_ENV                     $env:BM_ENV"
Write-Host "BM_MIGRATION_DATABASE_URL  $(Format-Target $env:BM_MIGRATION_DATABASE_URL)"
Write-Host "BM_DATABASE_URL            $(Format-Target $env:BM_DATABASE_URL)"
Write-Host ''

# --- run ----------------------------------------------------------------------
Push-Location -LiteralPath $repoRoot
try {
    & uv run @Command
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $code
