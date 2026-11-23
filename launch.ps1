# 启动脚本：自动寻找可用的 Python，然后打开量化分析工作台
# 用法：
#   powershell -ExecutionPolicy Bypass -File launch.ps1
#   powershell -ExecutionPolicy Bypass -File launch.ps1 -Port 8899 -NoBrowser
param(
    [switch]$NoBrowser,
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$bundled = Join-Path $env:USERPROFILE ".dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$py = $null
$pyArgs = @()

if (Test-Path $bundled) {
    & $bundled -c "import pandas, numpy" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $py = $bundled
    }
}

if (-not $py) {
    foreach ($name in @("py", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($cmd.Source -like "*WindowsApps*") { continue }
        $testArgs = @()
        if ($name -eq "py") { $testArgs = @("-3") }
        & $cmd.Source @testArgs -c "import pandas, numpy" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $py = $cmd.Source
            $pyArgs = $testArgs
            break
        }
    }
}

if (-not $py) {
    Write-Host ""
    Write-Host "未找到可用的 Python 3 环境（需要 pandas、numpy）。" -ForegroundColor Yellow
    Write-Host "请安装 Python 3.10+ 后执行： pip install pandas numpy" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "按回车键退出"
    exit 1
}

Write-Host ""
Write-Host "Python: $py" -ForegroundColor DarkGray
Write-Host "正在启动 量析 StockQuant ..." -ForegroundColor Cyan
Write-Host ""

$serverArgs = @("server.py", "--port", "$Port")
if (-not $NoBrowser) { $serverArgs += "--open" }

& $py @pyArgs @serverArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "服务异常退出（退出码 $LASTEXITCODE）。" -ForegroundColor Red
    Read-Host "按回车键关闭"
}
