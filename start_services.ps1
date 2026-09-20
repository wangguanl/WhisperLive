# ============================================================================
# WhisperLive service startup script (engine + adapter gateway)
# Ports follow E:\AI\local-voice\.env:
#   engine  WS 47831 / REST 47832
#   gateway WS 47833 / HTTP 47834
# Usage: powershell -ExecutionPolicy Bypass -File start_services.ps1
# ============================================================================
$ErrorActionPreference = 'Stop'

$Project = 'E:\AI\local-voice\WhisperLive'
$VenvPy  = Join-Path $Project '.venv\Scripts\python.exe'

# Ports (match E:\AI\local-voice\.env)
$EnginePort = 47831   # WHISPERLIVE_WS_PORT
$EngineRest = 47832   # WHISPERLIVE_REST_PORT
$GwWs  = 47833        # WHISPERLIVE_GW_WS_PORT
$GwHttp = 47834       # WHISPERLIVE_GW_HTTP_PORT

# Log directory
$LogDir = Join-Path $Project 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Wait until a port starts listening
function Wait-Port([int]$Port, [int]$Seconds = 60) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# Stop process(es) currently listening on a port (idempotent restart)
function Stop-PortOwner([int]$Port) {
    $conns = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($conns) {
        Write-Host "    stopping existing process on port $Port ..." -ForegroundColor Yellow
        $conns | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
        # wait until the port is freed
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline) {
            if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) {
                return
            }
            Start-Sleep -Milliseconds 300
        }
        Write-Host "    port $Port still in use after stop, proceeding anyway" -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# 1. Start WhisperLive engine (GPU, faster_whisper)
#    If already running, stop it first, then start fresh.
# ---------------------------------------------------------------------------
Stop-PortOwner $EnginePort
Write-Host "[engine] starting WhisperLive engine (WS $EnginePort / REST $EngineRest) ..."
$env:HF_HUB_DISABLE_XET = '1'
$env:HF_HOME = 'E:\huggingface_cache'
$env:PATH = (
    'E:\AI\local-voice\WhisperLive\.venv\Lib\site-packages\nvidia\cublas\bin;' +
    'E:\AI\local-voice\WhisperLive\.venv\Lib\site-packages\nvidia\cuda_nvrtc\bin;' +
    'E:\AI\local-voice\WhisperLive\.venv\Lib\site-packages\nvidia\curand\bin;' + $env:PATH
)
Start-Process -FilePath $VenvPy `
    -ArgumentList @(
        (Join-Path $Project 'run_server.py'),
        '--port',   "$EnginePort",
        '--rest_port', "$EngineRest",
        '--backend', 'faster_whisper'
    ) `
    -WorkingDirectory $Project `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir 'engine.log') `
    -RedirectStandardError  (Join-Path $LogDir 'engine.err.log')

if (-not (Wait-Port $EnginePort 90)) {
    Write-Host "[engine] start timeout, check logs\engine.err.log" -ForegroundColor Red
} else {
    Write-Host "[engine] ready on ws://127.0.0.1:$EnginePort" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 2. Start adapter gateway (default config: upstream=engine, WS/HTTP per .env)
#    If already running, stop it first, then start fresh.
# ---------------------------------------------------------------------------
Stop-PortOwner $GwHttp
Write-Host "[gateway] starting adapter gateway (WS $GwWs / HTTP $GwHttp) ..."
Start-Process -FilePath $VenvPy `
    -ArgumentList @((Join-Path $Project 'run_gateway.py')) `
    -WorkingDirectory $Project `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir 'gateway.log') `
    -RedirectStandardError  (Join-Path $LogDir 'gateway.err.log')

if (-not (Wait-Port $GwHttp 30)) {
    Write-Host "[gateway] start timeout, check logs\gateway.err.log" -ForegroundColor Red
} else {
    Write-Host "[gateway] ready" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 3. Summary + health check
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==== Service status ====" -ForegroundColor Cyan
if (Get-NetTCPConnection -State Listen -LocalPort $EnginePort -ErrorAction SilentlyContinue) {
    Write-Host "  Engine WS   :  ws://127.0.0.1:$EnginePort   [OK]" -ForegroundColor Green
} else {
    Write-Host "  Engine WS   :  ws://127.0.0.1:$EnginePort   [FAIL]" -ForegroundColor Red
}
if (Get-NetTCPConnection -State Listen -LocalPort $GwWs -ErrorAction SilentlyContinue) {
    Write-Host "  Gateway WS  :  ws://127.0.0.1:$GwWs/v1/stream/transcriptions   [OK]" -ForegroundColor Green
} else {
    Write-Host "  Gateway WS  :  ws://127.0.0.1:$GwWs   [FAIL]" -ForegroundColor Red
}
if (Get-NetTCPConnection -State Listen -LocalPort $GwHttp -ErrorAction SilentlyContinue) {
    Write-Host "  Gateway HTTP:  http://127.0.0.1:$GwHttp   [OK]" -ForegroundColor Green
    try {
        $h = (Invoke-WebRequest -Uri "http://127.0.0.1:$GwHttp/health" -UseBasicParsing -TimeoutSec 5).Content
        Write-Host "  Health      :  $h" -ForegroundColor Green
    } catch {
        Write-Host "  Health      :  request failed: $($_.Exception.Message)" -ForegroundColor Red
    }
} else {
    Write-Host "  Gateway HTTP:  http://127.0.0.1:$GwHttp   [FAIL]" -ForegroundColor Red
}
Write-Host ""
Write-Host "Frontend endpoints:" -ForegroundColor Cyan
Write-Host "  WS  : ws://127.0.0.1:$GwWs/v1/stream/transcriptions"
Write-Host "  HTTP: http://127.0.0.1:$GwHttp"
Write-Host "Logs : $LogDir"