param([switch]$Media)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
function Invoke-Checked { param([string]$Exe, [string[]]$Arguments) & $Exe @Arguments; if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE" } }
$backend = Join-Path $projectRoot 'backend'
$envPath = Join-Path $backend '.env'
if (!(Test-Path -LiteralPath $envPath)) {
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    $secret = [Convert]::ToBase64String($bytes)
    $template = [IO.File]::ReadAllText((Join-Path $backend '.env.example'))
    [IO.File]::WriteAllText($envPath, $template.Replace('replace-with-a-random-secret', $secret), [Text.UTF8Encoding]::new($false))
}
$python = Join-Path $backend '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { Invoke-Checked 'python' @('-m', 'venv', (Join-Path $backend '.venv')) }
Invoke-Checked $python @('-m', 'pip', 'install', '-r', (Join-Path $backend 'requirements.txt'), '-r', (Join-Path $backend 'requirements-dev.txt'))
if ($Media) { Invoke-Checked $python @('-m', 'pip', 'install', '-r', (Join-Path $backend 'requirements-media.txt')) }
foreach ($frontend in @('frontend-user', 'frontend-admin')) {
    Push-Location (Join-Path $projectRoot $frontend)
    try { Invoke-Checked 'npm.cmd' @('ci') } finally { Pop-Location }
}
Write-Host 'Dependencies installed. Configure backend/.env, then start PostgreSQL and Redis using docker compose up -d postgres redis.'
Write-Host 'Initialize tables: .\scripts\dev.ps1 migrate. Start API: .\scripts\dev.ps1 backend.'
