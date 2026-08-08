# Measure the ANGLE of every rendered image, from head pose. No GPU, no Ollama.
#
#   powershell -ExecutionPolicy Bypass -File scripts\angles.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\angles.ps1 -Id dorian-v1-b1966f
#
# Seconds, not minutes. Safe to run while anything else is running - it only
# reads the images and writes the angle fields. Needs backend v1.234 or newer.

param(
    [string]$Id = "",
    [switch]$ListDatasets,
    [string]$BaseUrl = "http://127.0.0.1:8899/api/lora"
)

$ErrorActionPreference = "Stop"

$ver = (Invoke-RestMethod "http://127.0.0.1:8899/api/health" -TimeoutSec 10).version
Write-Host ("backend " + $ver) -ForegroundColor Green
if (([version]$ver) -lt ([version]"1.243.0")) {
    Write-Host "  This needs v1.243 or newer. Restart run.bat and try again." -ForegroundColor Yellow
    exit 1
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
Write-Host ("dataset " + $Id)

try {
    $r = Invoke-RestMethod -Method Post "$BaseUrl/datasets/$Id/angles" -Body "{}" -ContentType "application/json" -TimeoutSec 900
} catch {
    Write-Host ("FAILED: " + $_.Exception.Message) -ForegroundColor Red
    $resp = $_.Exception.Response
    if ($resp) { try { $sr = New-Object System.IO.StreamReader($resp.GetResponseStream()); Write-Host ("server said: " + $sr.ReadToEnd()) -ForegroundColor Red } catch { } }
    exit 1
}

Write-Host ""
Write-Host ("measured " + $r.measured + " image(s)") -ForegroundColor Cyan
Write-Host ("sign: " + $r.sign) -ForegroundColor DarkGray
if ($r.framing_cal) {
    Write-Host ""
    Write-Host "=== shot type, calibrated from THIS dataset ===" -ForegroundColor Cyan
    Write-Host ("  method: " + $r.framing_method) -ForegroundColor DarkGray
    foreach ($p in $r.framing_cal.medians.PSObject.Properties) {
        $n = $r.framing_cal.n.($p.Name)
        Write-Host ("  {0,-10} median face height {1,6:N1}%   from {2} image(s)" -f $p.Name, ($p.Value * 100), $n)
    }
    if ($r.framing_cal.separation) {
        Write-Host ("  spacing: " + (($r.framing_cal.separation.PSObject.Properties | ForEach-Object { $_.Name + " " + $_.Value + "x" }) -join "   "))
    }
    if ($r.framing_cal.order_ok -eq $false) { Write-Host "  ORDER IS WRONG - the shot types are not coming out as different shots" -ForegroundColor Red }
    foreach ($w in @($r.framing_cal.warnings)) { Write-Host ("  WARNING: " + $w) -ForegroundColor Yellow }
}

if ($r.by_framing) {
    Write-Host ""
    Write-Host "=== by planned shot type ===" -ForegroundColor Cyan
    foreach ($p in $r.by_framing.PSObject.Properties) {
        $b = $p.Value
        $med = if ($null -eq $b.face_h_median) { "  -  " } else { "{0,5:N1}%" -f ($b.face_h_median * 100) }
        Write-Host ("  {0,-10} n={1,-3} ok {2,-3} miss {3,-3} unmeasured {4,-3}  median face {5}" -f $p.Name, $b.n, $b.ok, $b.miss, $b.unmeasured, $med)
    }
    $badF = @($r.rows | Where-Object { $_.framing_ok -eq $false })
    if ($badF.Count) {
        Write-Host ("  " + $badF.Count + " wrong shot type:") -ForegroundColor Yellow
        foreach ($m in $badF) { Write-Host ("    " + $m.id + "  " + $m.framing + "  " + $m.framing_note) }
    }
}

Write-Host ""
Write-Host "=== by planned angle ===" -ForegroundColor Cyan
foreach ($p in $r.by_angle.PSObject.Properties) {
    $b = $p.Value
    $med = if ($null -eq $b.yaw_median) { "  -  " } else { "{0,6:N1}" -f $b.yaw_median }
    $rng = if ($null -eq $b.yaw_min) { "" } else { ("{0,6:N1} .. {1,6:N1}" -f $b.yaw_min, $b.yaw_max) }
    Write-Host ("  {0,-20} n={1,-3} ok {2,-3} miss {3,-3} unmeasured {4,-3}  median yaw {5}   range {6}" -f $p.Name, $b.n, $b.ok, $b.miss, $b.unmeasured, $med, $rng)
}

Write-Host ""
Write-Host "=== every image ===" -ForegroundColor Cyan
$r.rows | Sort-Object angle, id | Select-Object id, framing, angle, yaw, @{n="ok";e={$_.ok}}, det_score, kps_yaw | Format-Table -AutoSize

$bad = @($r.rows | Where-Object { $_.ok -eq $false })
if ($bad.Count) {
    Write-Host ("=== " + $bad.Count + " measured miss(es) ===") -ForegroundColor Yellow
    $bad | Sort-Object angle, id | ForEach-Object { Write-Host ("  " + $_.id + "  " + $_.angle + "  " + $_.note) }
}

$r | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $PSScriptRoot "_diag\angles.json") -Encoding UTF8
Write-Host ""
Write-Host ("wrote " + (Join-Path $PSScriptRoot "_diag\angles.json")) -ForegroundColor Green
Write-Host "Tell Claude 'angles are done'." -ForegroundColor Green
