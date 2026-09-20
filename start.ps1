#Requires -Version 5.0
<#
.SYNOPSIS
    启动本项目（由 wanggang-run-oss 生成）。实际逻辑按 PowerShell 7 执行。
.DESCRIPTION
    主路径：无参运行后交互选择要启动的服务（可单开或同开多个）。
    单服务项目会跳过菜单直接启动。
.PARAMETER Mode
    direct = 本机直接运行；docker = docker compose。
.PARAMETER Port
    仅当最终只启动一个需端口的服务时，作为该服务的端口搜索起点。
.PARAMETER Service
    内部/自动化用：跳过菜单，直接启动指定 Id。日常请无参走交互。
#>
[CmdletBinding()]
param(
    [ValidateSet('direct', 'docker')]
    [string]$Mode = 'direct',

    [int]$Port = 0,

    [string]$Service = ''
)

$ErrorActionPreference = 'Stop'

if ($PSVersionTable.PSVersion.Major -lt 7) {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if (-not $pwsh) {
        throw '未找到 PowerShell 7 (pwsh)。请先全局安装：winget install --id Microsoft.PowerShell -e'
    }
    $argList = @('-NoProfile', '-File', $PSCommandPath)
    foreach ($key in $PSBoundParameters.Keys) {
        $argList += "-$key"
        $val = $PSBoundParameters[$key]
        if ($val -isnot [System.Management.Automation.SwitchParameter]) {
            $argList += [string]$val
        }
    }
    & $pwsh.Source @argList
    exit $LASTEXITCODE
}

Set-Location $PSScriptRoot

$FfmpegBin = 'E:\Programs\ffmpeg-master-latest-win64-gpl\bin'
if (Test-Path $FfmpegBin) {
    $env:Path = "$FfmpegBin;$env:Path"
} else {
    Write-Warning "未找到本机 ffmpeg：$FfmpegBin"
}

function Show-GpuStatus {
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi) {
        Write-Warning '未检测到 nvidia-smi，跳过 GPU 检查。'
        return
    }
    Write-Host '=== GPU 状态 ===' -ForegroundColor Cyan
    & nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu --format=csv
}

function Test-PortBusy {
    param([int]$TargetPort)
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener ([System.Net.IPAddress]::Loopback, $TargetPort)
        $listener.Start()
        return $false
    } catch {
        return $true
    } finally {
        if ($null -ne $listener) { $listener.Stop() }
    }
}

function Get-FreePort {
    param(
        [int]$StartPort,
        [int]$MaxTries = 50
    )
    if ($StartPort -lt 1) { $StartPort = 1024 }
    $end = $StartPort + $MaxTries - 1
    if ($end -gt 65535) { $end = 65535 }
    $p = $StartPort
    while ($p -le $end) {
        if (-not (Test-PortBusy -TargetPort $p)) {
            if ($p -ne $StartPort) {
                Write-Host "端口 $StartPort 已占用，顺延到 $p" -ForegroundColor Yellow
            }
            return $p
        }
        $p++
    }
    throw "从 $StartPort 起连续探测均被占用，放弃。"
}

function Select-ServicesInteractive {
    param([object[]]$AllServices)

    if ($AllServices.Count -eq 0) {
        throw '未配置 $Services，请按项目改写模板。'
    }
    if ($AllServices.Count -eq 1) {
        Write-Host "仅一个服务，直接启动：$($AllServices[0].Label)" -ForegroundColor Cyan
        return @($AllServices[0])
    }

    Write-Host ''
    Write-Host '=== 启动哪些服务？===' -ForegroundColor Cyan
    for ($i = 0; $i -lt $AllServices.Count; $i++) {
        $svc = $AllServices[$i]
        $portHint = if ($svc.NeedsPort) { "端口起点 $($svc.PreferredPort)" } else { '无需端口' }
        Write-Host ("  [{0}] {1}  ({2})" -f ($i + 1), $svc.Label, $portHint)
    }
    Write-Host ("  [{0}] 全部开" -f ($AllServices.Count + 1))
    Write-Host '  [0] 取消'
    Write-Host ''

    $defaultChoice = '1'
    $raw = Read-Host "请选择（可多选，逗号分隔，如 1,2；默认 $defaultChoice）"
    if ([string]::IsNullOrWhiteSpace($raw)) { $raw = $defaultChoice }

    if ($raw.Trim() -eq '0') {
        throw '已取消启动。'
    }

    $allIndex = $AllServices.Count + 1
    $parts = $raw.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
    $selected = [System.Collections.Generic.List[object]]::new()

    foreach ($part in $parts) {
        $n = 0
        if (-not [int]::TryParse($part, [ref]$n)) {
            throw "无效选项：$part"
        }
        if ($n -eq $allIndex) {
            return @($AllServices)
        }
        if ($n -lt 1 -or $n -gt $AllServices.Count) {
            throw "选项超出范围：$n"
        }
        $selected.Add($AllServices[$n - 1])
    }

    if ($selected.Count -eq 0) {
        throw '未选择任何服务。'
    }

    $byId = [ordered]@{}
    foreach ($s in $selected) { $byId[$s.Id] = $s }
    return @($byId.Values)
}

function Assert-GpuOkForSelection {
    param([object[]]$Selected)

    $gpuServices = @($Selected | Where-Object { $_.UsesGpu })
    if ($gpuServices.Count -le 1) { return }

    $labels = ($gpuServices | ForEach-Object { $_.Label }) -join ', '
    Write-Host ''
    Write-Host "已选多个占卡服务：$labels" -ForegroundColor Yellow
    Write-Host '16GB 显存下同时加载多个推理服务容易 OOM。请确认剩余显存足够，或改回只开一个。' -ForegroundColor Yellow
    $confirm = Read-Host '仍要继续？[y/N]'
    if ($confirm -notmatch '^[yY]') {
        throw '已取消：多服务占卡未确认。'
    }
}

$Services = @(
    [pscustomobject]@{
        Id            = 'server'
        Label         = 'server（faster_whisper 引擎）'
        PreferredPort = 47831
        NeedsPort     = $true
        UsesGpu       = $true
    }
    [pscustomobject]@{
        Id            = 'gateway'
        Label         = 'gateway（适配网关 WS/HTTP）'
        PreferredPort = 47833
        NeedsPort     = $true
        UsesGpu       = $false
    }
    [pscustomobject]@{
        Id            = 'client'
        Label         = 'client（示例转写客户端）'
        PreferredPort = 0
        NeedsPort     = $false
        UsesGpu       = $false
    }
)

function Start-ProjectService {
    param(
        [Parameter(Mandatory)][object]$Service,
        [Parameter(Mandatory)][string]$RunMode,
        [int]$ListenPort = 0
    )

    if ($RunMode -eq 'docker') {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw '未找到 docker，无法使用 -Mode docker。'
        }
        docker compose up
        return
    }

    $py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { throw "未找到虚拟环境：$py" }

    if (-not $env:HF_HOME) { $env:HF_HOME = 'E:\huggingface_cache' }
    $env:HF_HUB_DISABLE_XET = '1'
    if (-not $env:HF_ENDPOINT) { $env:HF_ENDPOINT = 'https://hf-mirror.com' }

    $nvBins = @(
        (Join-Path $PSScriptRoot '.venv\Lib\site-packages\nvidia\cublas\bin'),
        (Join-Path $PSScriptRoot '.venv\Lib\site-packages\nvidia\cuda_nvrtc\bin'),
        (Join-Path $PSScriptRoot '.venv\Lib\site-packages\nvidia\curand\bin')
    ) | Where-Object { Test-Path $_ }
    if ($nvBins.Count -gt 0) {
        $env:Path = ($nvBins -join ';') + ';' + $env:Path
    }

    switch ($Service.Id) {
        'server' {
            $restStart = if ($ListenPort -eq 47831) { 47832 } else { $ListenPort + 1 }
            $restPort = Get-FreePort -StartPort $restStart
            Write-Host "WhisperLive engine WS=$ListenPort REST=$restPort" -ForegroundColor Green
            & $py (Join-Path $PSScriptRoot 'run_server.py') --port $ListenPort --rest_port $restPort --backend faster_whisper
            return
        }
        'gateway' {
            $httpStart = if ($ListenPort -eq 47833) { 47834 } else { $ListenPort + 1 }
            $httpPort = Get-FreePort -StartPort $httpStart
            $upstream = if ($env:GW_UPSTREAM_PORT) { $env:GW_UPSTREAM_PORT } else { '47831' }
            $env:GW_WS_PORT = "$ListenPort"
            $env:GW_HTTP_PORT = "$httpPort"
            $env:GW_UPSTREAM_PORT = "$upstream"
            Write-Host "Gateway WS=$ListenPort HTTP=$httpPort upstream=$upstream" -ForegroundColor Green
            Write-Host '提示：gateway 依赖已启动的 server 引擎。' -ForegroundColor Yellow
            & $py (Join-Path $PSScriptRoot 'run_gateway.py')
            return
        }
        'client' {
            $sample = Join-Path $PSScriptRoot 'jfk_resampled.wav'
            $serverPort = if ($env:WHISPERLIVE_WS_PORT) { $env:WHISPERLIVE_WS_PORT } else { '47831' }
            if (Test-Path $sample) {
                Write-Host "client 转写样例：$sample -> server :$serverPort" -ForegroundColor Green
                & $py (Join-Path $PSScriptRoot 'run_client.py') --server localhost --port $serverPort --files $sample --model small --lang en
            } else {
                Write-Host '未找到样例音频，启动麦克风客户端（Ctrl+C 结束）' -ForegroundColor Yellow
                & $py (Join-Path $PSScriptRoot 'run_client.py') --server localhost --port $serverPort --model small --lang en
            }
            return
        }
        default { throw "未知服务：$($Service.Id)" }
    }
}

Show-GpuStatus

if ($Service) {
    $match = @($Services | Where-Object { $_.Id -eq $Service })
    if ($match.Count -eq 0) {
        throw "未知服务 Id：$Service。可选：$($Services.Id -join ', ')"
    }
    $chosen = $match
} else {
    $chosen = Select-ServicesInteractive -AllServices $Services
    Assert-GpuOkForSelection -Selected $chosen
}

$multi = $chosen.Count -gt 1

if ($multi) {
    $started = @()
    foreach ($svc in $chosen) {
        $listen = 0
        if ($svc.NeedsPort) {
            $listen = Get-FreePort -StartPort $svc.PreferredPort
            Write-Host "$($svc.Label) 使用端口 $listen" -ForegroundColor Cyan
        }

        $argList = [System.Collections.Generic.List[string]]::new()
        $argList.AddRange([string[]]@('-NoProfile', '-File', $PSCommandPath, '-Mode', $Mode, '-Service', $svc.Id))
        if ($listen -gt 0) {
            $argList.Add('-Port')
            $argList.Add("$listen")
        }

        $p = Start-Process -FilePath 'pwsh' -ArgumentList $argList -PassThru -WorkingDirectory $PSScriptRoot
        $started += [pscustomobject]@{ Id = $svc.Id; Label = $svc.Label; Port = $listen; Pid = $p.Id }
        Write-Host "已后台启动 $($svc.Label) PID=$($p.Id)" -ForegroundColor Green
    }

    Write-Host ''
    Write-Host '=== 已启动 ===' -ForegroundColor Cyan
    foreach ($s in $started) {
        $portInfo = if ($s.Port -gt 0) { " port=$($s.Port)" } else { '' }
        Write-Host ("- {0}{1} pid={2}" -f $s.Label, $portInfo, $s.Pid)
    }
    Write-Host '各服务在独立进程中运行；结束请自行停对应 PID。' -ForegroundColor Yellow
    return
}

$svc = $chosen[0]
$listen = 0
if ($svc.NeedsPort) {
    $startPort = $svc.PreferredPort
    if ($Port -gt 0) { $startPort = $Port }
    $listen = Get-FreePort -StartPort $startPort
    Write-Host "$($svc.Label) 使用端口 $listen" -ForegroundColor Cyan
}

Start-ProjectService -Service $svc -RunMode $Mode -ListenPort $listen
