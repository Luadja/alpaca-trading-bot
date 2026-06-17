# Keep the trading bot + watchdog alive on Windows, restarting the bot if it crashes.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1
# Stop with Ctrl+C. For survive-logout/reboot, register this with Task Scheduler (see README).

param(
    [int]$Interval = 300,        # bot trade-cycle seconds
    [int]$RestartDelaySec = 10   # wait before restarting after a crash
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }   # fall back to PATH python

Write-Host "Project: $root"
$wd = $null
$bot = $null
try {
    while ($true) {
        # Supervise BOTH processes every tick. Running the bot in the FOREGROUND would block
        # this loop for hours/days (1Day strategy), so a watchdog that died mid-session would
        # never be relaunched and the dead-man's switch would silently disappear. Run both in
        # the background and relaunch whichever has exited.
        if ($null -eq $wd -or $wd.HasExited) {
            Write-Host "$(Get-Date -Format o)  (re)starting watchdog..."
            $wd = Start-Process -FilePath $py -ArgumentList "-m", "scripts.watchdog" -WorkingDirectory $root -PassThru
        }
        if ($null -eq $bot -or $bot.HasExited) {
            if ($null -ne $bot) {
                Write-Host "$(Get-Date -Format o)  bot exited (code $($bot.ExitCode)) - restarting in ${RestartDelaySec}s."
                Start-Sleep -Seconds $RestartDelaySec
            }
            Write-Host "$(Get-Date -Format o)  starting bot (interval ${Interval}s)..."
            $bot = Start-Process -FilePath $py -ArgumentList "-m", "bot.run", "--interval", $Interval -WorkingDirectory $root -PassThru
        }
        Start-Sleep -Seconds 15   # supervision poll
    }
}
finally {
    # Don't leave either process orphaned when the launcher stops.
    foreach ($p in @($bot, $wd)) {
        if ($p -and -not $p.HasExited) {
            Write-Host "Stopping pid $($p.Id)..."
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
