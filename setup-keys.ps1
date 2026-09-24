# setup-keys.ps1 — reads your .env and wires up your keys.
#
# Your keys go from the .env file straight into the tool. They are never printed,
# never sent to the AI, and never written into a chat.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
  Write-Host "No .env file found in this folder." -ForegroundColor Red
  Write-Host "   Copy .env.template to .env, paste your keys into it, and run this again."
  exit 1
}

# Load .env without printing anything.
Get-Content ".env" | ForEach-Object {
  if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') {
    $v = $matches[2].Trim().Trim('"').Trim("'")
    if ($v) { Set-Item -Path ("Env:" + $matches[1]) -Value $v }
  }
}

$found = 0
function Need($name, $label) {
  if (-not (Test-Path ("Env:" + $name)) -or -not (Get-Item ("Env:" + $name)).Value) {
    Write-Host "  [ -- ] $label  ($name) blank, skipping" -ForegroundColor DarkGray
  } else { Write-Host "  [OK] $label" -ForegroundColor Green; $script:found = 1 }
}

Write-Host "Checking your .env..."

Need "PRICELABS_API_KEY" "PriceLabs"
Need "HOSPITABLE_API_KEY" "Hospitable"
Need "TURNO_API_TOKEN" "Turno — the long JWT (starts with eyJ)"
Need "TURNO_PARTNER_ID" "Turno — the partner UUID"
Need "AIRROI_API_KEY" "AirROI (free key)"

if ($found -eq 0) {
  Write-Host ""
  Write-Host "Nothing is filled in yet. Open the .env file, paste your key(s), save, and run this again." -ForegroundColor Red
  Write-Host "(See KEYS.md for where to get each one.)"
  exit 1
}
Write-Host ""


foreach ($s in @("pricelabs","hospitable","turno","airroi","rankbreeze")) {
  if (Test-Path "mcp-servers/$s") { Copy-Item ".env" "mcp-servers/$s/.env" -Force; Write-Host "  [OK] $s configured" -ForegroundColor Green }
}
if ($env:RANKBREEZE_SESSION -and (Test-Path "mcp-servers/rankbreeze")) {
  [IO.File]::WriteAllText("mcp-servers/rankbreeze/session.txt", $env:RANKBREEZE_SESSION)
}
Write-Host ""; Write-Host "Done. Now FULLY QUIT AND REOPEN Claude Code so it picks up your keys."
