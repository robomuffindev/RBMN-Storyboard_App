# A/B test: do three-quarter shots get their angle right if they start from the
# FRONT base instead of the side profile base?
#
#   powershell -ExecutionPolicy Bypass -File scripts\ab_tq_base.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\ab_tq_base.ps1 -Revert
#
# Costs 14 renders. Nothing else in the dataset is touched.

param(
    [string]$Id = "",
    [switch]$Revert,
    [string]$BaseUrl = "http://127.0.0.1:8899/api/lora"
)

$ErrorActionPreference = "Stop"
$mode = if ($Revert) { "side" } else { "front" }

function Wait-Run($dsId, $what) {
    $spin = 0
    while ($true) {
        Start-Sleep -Seconds 4
        $s = Invoke-RestMethod "$BaseUrl/datasets/$dsId"
        if (-not $s.run) { break }
        if ($s.run.status -ne "running") { break }
        $spin++
        Write-Host ("  " + $what + ": " + $s.run.detail + "   (" + ($spin * 4) + "s)")
    }
    $s = Invoke-RestMethod "$BaseUrl/datasets/$dsId"
    if ($s.run -and $s.run.error) { Write-Host ("  error: " + $s.run.error) -ForegroundColor Red }
    return $s
}

try {
    $ver = (Invoke-RestMethod "http://127.0.0.1:8899/api/health" -TimeoutSec 10).version
    Write-Host ("running version: " + $ver) -ForegroundColor Green
} catch {
    Write-Host "Cannot reach the backend. Start run.bat first." -ForegroundColor Red
    exit 1
}

if (-not $Id) {
    $all = (Invoke-RestMethod "$BaseUrl/datasets").datasets
    # v1.245: NEWEST, not most-rendered. Sorting by "rendered" kept measuring the
    # 40-image dorian set while a new character was the thing being tested.
    $Id = $all[0].id
}
$d = Invoke-RestMethod "$BaseUrl/datasets/$Id"
Write-Host ("dataset: " + $Id)

$tq = @($d.items | Where-Object { $_.angle -eq "three_quarter_left" -or $_.angle -eq "three_quarter_right" })
if ($tq.Count -eq 0) { Write-Host "No three-quarter rows in this dataset." -ForegroundColor Yellow; exit 0 }
$ids = @($tq | ForEach-Object { $_.id })

$beforeMiss = @($tq | Where-Object { $_.qc -and $_.qc.angle_ok -eq $false }).Count
$beforeScores = @($tq | Where-Object { $_.qc -and $_.qc.identity_score -ne $null } | ForEach-Object { $_.qc.identity_score } | Sort-Object)
$beforeMedian = if ($beforeScores.Count) { [math]::Round($beforeScores[[int][math]::Floor($beforeScores.Count / 2)], 3) } else { "n/a" }

Write-Host ""
Write-Host "BEFORE" -ForegroundColor Cyan
Write-Host ("  three-quarter rows : " + $tq.Count)
Write-Host ("  angle misses       : " + $beforeMiss + " of " + $tq.Count)
Write-Host ("  identity median    : " + $beforeMedian)
Write-Host ("  base they used     : " + (($tq | ForEach-Object { $_.identity } | Sort-Object -Unique) -join ", "))

# ---------------------------------------------------------------- set tq_base
# options is REPLACED wholesale by the plan route, so merge rather than send one key.
$opts = @{}
if ($d.options) { $d.options.PSObject.Properties | ForEach-Object { $opts[$_.Name] = $_.Value } }
$opts["tq_base"] = $mode
$body = @{ count = $d.items.Count; options = $opts } | ConvertTo-Json -Depth 6
Write-Host ""
Write-Host ("setting tq_base = " + $mode) -ForegroundColor Yellow
$null = Invoke-RestMethod -Method Post "$BaseUrl/datasets/$Id/plan" -Body $body -ContentType "application/json"

# re-planning keeps rendered images whose slot is unchanged; only the base choice moved
$chk = Invoke-RestMethod "$BaseUrl/datasets/$Id"
$stillRendered = @($chk.items | Where-Object { $_.status -eq "done" }).Count
Write-Host ("  images still rendered after re-plan: " + $stillRendered + " of " + $chk.items.Count)

# ---------------------------------------------------------------- re-render
Write-Host ""
Write-Host ("re-rendering " + $ids.Count + " three-quarter images...") -ForegroundColor Yellow
$gen = @{ item_ids = $ids; overwrite = $true } | ConvertTo-Json -Depth 4
$null = Invoke-RestMethod -Method Post "$BaseUrl/datasets/$Id/generate" -Body $gen -ContentType "application/json"
$null = Wait-Run $Id "render"

# ---------------------------------------------------------------- re-check
Write-Host ""
Write-Host "re-running QC on those images..." -ForegroundColor Yellow
$qc = @{ item_ids = $ids; overwrite = $true } | ConvertTo-Json -Depth 4
$null = Invoke-RestMethod -Method Post "$BaseUrl/datasets/$Id/qc" -Body $qc -ContentType "application/json"
$after = Wait-Run $Id "qc"

# ---------------------------------------------------------------- verdict
$tq2 = @($after.items | Where-Object { $ids -contains $_.id })
$afterMiss = @($tq2 | Where-Object { $_.qc -and $_.qc.angle_ok -eq $false }).Count
$afterScores = @($tq2 | Where-Object { $_.qc -and $_.qc.identity_score -ne $null } | ForEach-Object { $_.qc.identity_score } | Sort-Object)
$afterMedian = if ($afterScores.Count) { [math]::Round($afterScores[[int][math]::Floor($afterScores.Count / 2)], 3) } else { "n/a" }

Write-Host ""
Write-Host "=== RESULT ===" -ForegroundColor Cyan
Write-Host ("  tq_base            : side  ->  " + $mode)
Write-Host ("  angle misses       : " + $beforeMiss + "  ->  " + $afterMiss + "   (of " + $tq.Count + ")")
Write-Host ("  identity median    : " + $beforeMedian + "  ->  " + $afterMedian)
Write-Host ("  base now used      : " + (($tq2 | ForEach-Object { $_.identity } | Sort-Object -Unique) -join ", "))
Write-Host ""
$tq2 | Select-Object id, angle, @{n = "angle_ok"; e = { $_.qc.angle_ok } }, @{n = "score"; e = { if ($_.qc.identity_score -ne $null) { [math]::Round($_.qc.identity_score, 3) } else { "no face" } } }, @{n = "base"; e = { $_.identity } } | Format-Table -AutoSize

Write-Host ""
if ($afterMiss -lt $beforeMiss) {
    Write-Host ("BETTER by " + ($beforeMiss - $afterMiss) + " images. Keep it.") -ForegroundColor Green
} elseif ($afterMiss -eq $beforeMiss) {
    Write-Host "NO CHANGE. The base was not the cause - tell Claude." -ForegroundColor Yellow
} else {
    Write-Host "WORSE. Revert with -Revert and tell Claude." -ForegroundColor Red
}
Write-Host "To undo: powershell -ExecutionPolicy Bypass -File scripts\ab_tq_base.ps1 -Revert"
