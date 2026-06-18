# Launch the CRYPTO playground (PAPER, 24/7) — a research toy with NO demonstrated edge.
# Sets the crypto profile, then hands off to the standard supervised loop (bot + watchdog,
# auto-restart). Keys + Discord webhook come from your existing .env; only the crypto-specific
# settings are overridden here, so your stock .env is left untouched.
#
#   powershell -ExecutionPolicy Bypass -File scripts\run_crypto.ps1
#   (override pairs:  $env:BOT_SYMBOLS='["BTC/USD","ETH/USD"]'  before running)
#
# Stop with Ctrl+C. Expected behavior: flat-to-negative; the point is to WATCH it trade.

param(
    [int]$Interval = 1800   # daily breakout: the signal only changes at the daily close, so a
                            # modest poll is plenty (the 60s safety poll still guards the kill switch)
)

$env:BOT_MARKET = "crypto"
$env:BOT_STRATEGY = "breakout"
$env:BOT_TIMEFRAME = "1Day"
if (-not $env:BOT_SYMBOLS) {
    # Liquid pairs with multi-year Alpaca history; 7 names x 10% cap fits the 60% exposure gate.
    $env:BOT_SYMBOLS = '["BTC/USD","ETH/USD","SOL/USD","LTC/USD","LINK/USD","AVAX/USD","DOGE/USD"]'
}
# Isolated ledger/heartbeat so the crypto playground never mixes with the (retired) stock bot.
# The watchdog (a child of run_windows.ps1) inherits these env vars + market=crypto, so it
# reads the same heartbeat and stays armed 24/7.
$env:BOT_LEDGER_PATH = "data/crypto_ledger.sqlite"
$env:BOT_HEARTBEAT_PATH = "data/crypto_heartbeat.json"

Write-Host "CRYPTO PLAYGROUND (paper) — breakout, 1Day, interval ${Interval}s. NO demonstrated edge; do not fund."
& (Join-Path $PSScriptRoot "run_windows.ps1") -Interval $Interval
