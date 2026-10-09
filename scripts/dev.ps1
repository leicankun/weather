param([ValidateSet('backend','user','admin','migrate','worker','beat')][string]$Service = 'user')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
$folder = switch ($Service) { 'user' {'frontend-user'} 'admin' {'frontend-admin'} default {'backend'} }
Push-Location (Join-Path $projectRoot $folder)
try {
    switch ($Service) {
        'user' { & npm.cmd run dev }
        'admin' { & npm.cmd run dev }
        'backend' { & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload }
        'migrate' { & $python -m alembic upgrade head }
        'worker' { & $python -m celery -A app.celery_app worker --pool=solo -l info }
        'beat' { & $python -m celery -A app.celery_app beat -l info }
    }
    if ($LASTEXITCODE -ne 0) { throw "Service exited with code $LASTEXITCODE" }
} finally { Pop-Location }
