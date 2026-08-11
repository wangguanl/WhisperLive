# ============================================================================
# WhisperLive service stop script (engine + adapter gateway)
# Stops the background processes started by start_services.ps1.
#   engine  WS 47831 / REST 47832
#   gateway WS 47833 / HTTP 47834
# Usage: powershell -ExecutionPolicy Bypass -File stop_services.ps1
# ============================================================================
$ErrorActionPreference = 'Stop'

# Ports (match E:\Pro2\.env and start_services.ps1)
$EnginePort = 47831   # WHISPERLIVE_WS_PORT
$GatewayPort = 47834  # WHISPERLIVE_GW_HTTP_PORT

# Stop process(es) currently listening on a port, return whether anything died
function Stop-PortOwner([int]$Port) {
    $conns = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($conns) {
        $conns | ForEach-Object {
            Write-Host "  stopping pid $($_.OwningProcess) on port $Port ..." -ForegroundColor Yellow
            Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
        }
        return $true
    }
    return $false
}

Write-Host "==== Stopping WhisperLive services ====" -ForegroundColor Cyan

$engineAlive = Stop-PortOwner $EnginePort
$gatewayAlive = Stop-PortOwner $GatewayPort

# brief wait for ports to free up
Start-Sleep -Milliseconds 500

Write-Host ""
Write-Host "==== Result ====" -ForegroundColor Cyan
if (-not (Get-NetTCPConnection -State Listen -LocalPort $EnginePort -ErrorAction SilentlyContinue)) {
    Write-Host "  Engine (47831)  : stopped [OK]" -ForegroundColor Green
} else {
    Write-Host "  Engine (47831)  : still listening [FAIL]" -ForegroundColor Red
}
if (-not (Get-NetTCPConnection -State Listen -LocalPort $GatewayPort -ErrorAction SilentlyContinue)) {
    Write-Host "  Gateway (47834) : stopped [OK]" -ForegroundColor Green
} else {
    Write-Host "  Gateway (47834) : still listening [FAIL]" -ForegroundColor Red
}

if (-not $engineAlive -and -not $gatewayAlive) {
    Write-Host ""
    Write-Host "No WhisperLive service was running." -ForegroundColor Blue
}