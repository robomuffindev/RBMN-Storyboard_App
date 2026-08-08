# Recover dorian-v1 after the re-plan that lost its preset.
#
#   powershell -ExecutionPolicy Bypass -File scripts\repair_dataset.ps1
#       -> shows BOTH options, changes nothing
#
#   powershell -ExecutionPolicy Bypass -File scripts\repair_dataset.ps1 -Apply face_heavy
#   powershell -ExecutionPolicy Bypass -File scripts\repair_dataset.ps1 -Apply balanced
#       -> sets the preset, turns tq_base on, renders everything missing, runs QC

param(
    [string]$Id = "",
    [ValidateSet("", "face_heavy", "balanced")][string]$Apply = "",
    [string]$TqBase = "front",
    [switch]$ListDatasets,
    [string]$BaseUrl = "http://127.0.0.1:8899/api/lora"
)

$ErrorActionPreference = "Stop"

function Wait-Run($dsId, $what) {
    $t = 0
    while ($true) {
        Start-Sleep -Seconds 5; $t += 5
        $s = Invoke-RestMethod "$BaseUrl/datasets/$dsId"
        if (-not $s.run -or $s.run.status -ne "running") { break }
        Write-Host ("  " + $what + ": " + $s.run.detail + "   (" + $t + "s)")
    }
    return Invoke-RestMethod "$BaseUrl/datasets/$dsId"
}

try {
    $ver = (Invoke-RestMethod "http://127.0.0.1:8899/api/health" -TimeoutSec 10).version
    Write-Host ("running version: " + $ver) -ForegroundColor Green
    if ($ver -lt "1.223.0") { Write-Host "Restart run.bat first - this needs v1.222." -ForegroundColor Red; exit 1 }
} catch {
    Write-Host "Cannot reach the backend. Start run.bat first." -ForegroundColor Red; exit 1
}

# v1.245: pick the NEWEST dataset, not the one with the most images. Sorting by
# "rendered" silently kept measuring the 40-image dorian set while a new
# character was the thing being tested, and the only sign was an id in one line
# of output. The API already returns newest-first.
$allDs = @((Invoke-RestMethod "$BaseUrl/datasets").datasets)
if ($ListDatasets) {
    Write-Host "datasets, newest first:" -ForegroundColor Cyan
    $allDs | Select-Object id, name, char_slug, total, rendered, flagged, created_at | Format-Table -AutoSize
    exit 0
}
if (-not $Id) { $Id = $allDs[0].id }
$dsInfo = $allDs | Where-Object { $_.id -eq $Id }
if (-not $dsInfo) { Write-Host ("No dataset with id '" + $Id + "'. Use -ListDatasets to see them.") -ForegroundColor Red; exit 1 }
Write-Host ("DATASET: " + $dsInfo.name + "   character: " + $dsInfo.char_slug) -ForegroundColor Cyan
Write-Host ("  id " + $dsInfo.id + " - " + $dsInfo.rendered + " of " + $dsInfo.total + " rendered, created " + $dsInfo.created_at)
if ($allDs.Count -gt 1 -and -not $PSBoundParameters.ContainsKey("Id")) {
    Write-Host ("  (newest of " + $allDs.Count + " datasets - pass -Id to pick another, -ListDatasets to see them all)") -ForegroundColor DarkGray
}
$d = Invoke-RestMethod "$BaseUrl/datasets/$Id"
$rendered = @($d.items | Where-Object { $_.status -eq "done" }).Count
Write-Host ("dataset: " + $Id)
Write-Host ("current preset: " + $d.preset + "    rendered: " + $rendered + " of " + $d.items.Count)

# ---------------------------------------------------------------- resync
# v1.223: rows whose status was eaten by the old write race are on disk but
# recorded as unrendered. Fix the bookkeeping BEFORE anything reads it.
try {
    $rs = Invoke-RestMethod -Method Post "$BaseUrl/datasets/$Id/resync" -TimeoutSec 120
    if ($rs.marked_rendered -gt 0) {
        Write-Host ("resync: " + $rs.marked_rendered + " image(s) were on disk but recorded as unrendered - fixed") -ForegroundColor Yellow
    }
    if ($rs.cleared -gt 0) { Write-Host ("resync: " + $rs.cleared + " marked done but missing - cleared") }
    Write-Host ("rendered after resync: " + $rs.rendered + " of " + $rs.total)
} catch {
    Write-Host "resync unavailable - restart run.bat to pick up v1.223" -ForegroundColor Yellow
}

# ------------------------------------------------------------------ preview
if (-not $Apply) {
    Write-Host ""
    Write-Host "Nothing has been changed. Here is what each option would cost:" -ForegroundColor Cyan
    foreach ($p in @("face_heavy", "balanced")) {
        $body = @{ options = @{ preset = $p; tq_base = $TqBase } } | ConvertTo-Json -Depth 5
        $pv = Invoke-RestMethod -Method Post "$BaseUrl/datasets/$Id/plan-preview" -Body $body -ContentType "application/json"
        Write-Host ""
        Write-Host ("  -Apply " + $p) -ForegroundColor Yellow
        Write-Host ("     keeps            : " + $pv.impact.kept + " rendered images")
        Write-Host ("     throws away      : " + $pv.impact.discarded)
        Write-Host ("     renders needed   : " + ($pv.count - $pv.impact.kept))
    }
    Write-Host ""
    Write-Host "RECOMMENDED: -Apply balanced" -ForegroundColor Green
    Write-Host "  Your original framing counts were face 8 / headshot 8 / upper 12 / full 12,"
    Write-Host "  which IS balanced. The preset never changed - an earlier claim of mine was wrong."
    Write-Host "  face_heavy would be a composition change you never asked for."
    Write-Host ""
    Write-Host "Run:  powershell -ExecutionPolicy Bypass -File scripts\repair_dataset.ps1 -Apply balanced"
    exit 0
}

# ------------------------------------------------------------------ apply
$body = @{ options = @{ preset = $Apply; tq_base = $TqBase }; force = $true } | ConvertTo-Json -Depth 5
Write-Host ""
Write-Host ("setting preset=" + $Apply + ", tq_base=" + $TqBase) -ForegroundColor Yellow
$r = Invoke-RestMethod -Method Post "$BaseUrl/datasets/$Id/plan" -Body $body -ContentType "application/json"
Write-Host ("  kept " + $r.impact.kept + ", discarded " + $r.impact.discarded)
foreach ($w in $r.warnings) { Write-Host ("  note: " + $w) -ForegroundColor Yellow }

$miss = @($r.items | Where-Object { $_.status -ne "done" }).Count
Write-Host ""
Write-Host ("rendering " + $miss + " missing images...") -ForegroundColor Yellow
$null = Invoke-RestMethod -Method Post "$BaseUrl/datasets/$Id/generate" -Body (@{ overwrite = $false } | ConvertTo-Json) -ContentType "application/json"
$null = Wait-Run $Id "render"

Write-Host ""
Write-Host "running QC on everything..." -ForegroundColor Yellow
$null = Invoke-RestMethod -Method Post "$BaseUrl/datasets/$Id/qc" -Body (@{ overwrite = $true } | ConvertTo-Json) -ContentType "application/json"
$after = Wait-Run $Id "qc"

Write-Host ""
Write-Host "=== RESULT ===" -ForegroundColor Cyan
$tq = @($after.items | Where-Object { $_.angle -like "three_quarter*" })
$miss2 = @($tq | Where-Object { $_.qc -and $_.qc.angle_ok -eq $false }).Count
Write-Host ("  preset             : " + $Apply + "   tq_base: " + $TqBase)
Write-Host ("  rendered           : " + @($after.items | Where-Object { $_.status -eq "done" }).Count + " of " + $after.items.Count)
Write-Host ("  three-quarter rows : " + $tq.Count)
Write-Host ("  their angle misses : " + $miss2 + " of " + $tq.Count + "   (was 14 of 14 before tq_base)")
Write-Host ("  base they now use  : " + (($tq | ForEach-Object { $_.identity } | Sort-Object -Unique) -join ", "))
$after.flags | Format-List
Write-Host ""
Write-Host "Now run:  powershell -ExecutionPolicy Bypass -File scripts\diag_likeness.ps1 -Rescore" -ForegroundColor Green
