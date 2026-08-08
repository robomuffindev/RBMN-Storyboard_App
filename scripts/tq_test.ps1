# Three-quarter wording experiment: render all 4 wordings, score each on head yaw.
#
#   powershell -ExecutionPolicy Bypass -File scripts\tq_test.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\tq_test.ps1 -Wordings degrees,halfway
#   powershell -ExecutionPolicy Bypass -File scripts\tq_test.ps1 -ScoreOnly
#
# Renders the 16 three-quarter rows once per wording and measures every result
# with head pose. About 64 renders across both Klein workers. Long; unattended.
# Needs backend v1.235 or newer.
#
# Nothing else in the dataset is touched: front, profile and back rows measure
# correct today and are not re-rendered.

param(
    [string]$Id = "",
    [string[]]$Wordings = @("degrees", "frame", "halfway", "tworef"),
    [switch]$ScoreOnly,
    [switch]$KeepImages,
    [switch]$ListDatasets,
    [string]$BaseUrl = "http://127.0.0.1:8899/api/lora"
)

$ErrorActionPreference = "Stop"
$outDir = Join-Path $PSScriptRoot "_diag\tq"

function PostRaw($path, $bodyObj, $method) {
    if (-not $method) { $method = "Post" }
    $json = $bodyObj | ConvertTo-Json -Depth 6
    try {
        return Invoke-RestMethod -Method $method "$BaseUrl$path" -Body $json -ContentType "application/json" -TimeoutSec 1800
    } catch {
        Write-Host ("  REQUEST FAILED: " + $method + " " + $path) -ForegroundColor Red
        Write-Host ("  " + $_.Exception.Message) -ForegroundColor Red
        $r = $_.Exception.Response
        if ($r) { try { $sr = New-Object System.IO.StreamReader($r.GetResponseStream()); Write-Host ("  server said: " + $sr.ReadToEnd()) -ForegroundColor Red } catch { } }
        return $null
    }
}

function WaitRun($dsId, $what) {
    $t = 0; $lastDetail = ""
    while ($true) {
        Start-Sleep -Seconds 5; $t += 5
        try { $s = Invoke-RestMethod "$BaseUrl/datasets/$dsId" -TimeoutSec 30 } catch { continue }
        if (-not $s.run -or $s.run.status -ne "running") {
            if ($s.run) { Write-Host ("    " + $what + " ended: " + $s.run.status + " " + $s.run.detail) }
            if ($s.run.error) { Write-Host ("    error: " + $s.run.error) -ForegroundColor Red }
            return $s
        }
        if ($s.run.detail -ne $lastDetail) {
            Write-Host ("    " + $what + ": " + $s.run.detail + "   (" + $t + "s)")
            $lastDetail = $s.run.detail
        } elseif ($t % 60 -eq 0) {
            Write-Host ("    " + $what + ": still " + $s.run.detail + "   (" + $t + "s, no change)") -ForegroundColor DarkGray
        }
    }
}

$ver = (Invoke-RestMethod "http://127.0.0.1:8899/api/health" -TimeoutSec 10).version
Write-Host ("backend " + $ver) -ForegroundColor Green
if (([version]$ver) -lt ([version]"1.235.0")) {
    Write-Host "  This needs v1.235 or newer. Restart run.bat and try again." -ForegroundColor Yellow
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
$d = Invoke-RestMethod "$BaseUrl/datasets/$Id"
$tq = @($d.items | Where-Object { $_.angle -eq "three_quarter_left" -or $_.angle -eq "three_quarter_right" })
$ids = @($tq | ForEach-Object { $_.id })
Write-Host ("dataset " + $Id + "   three-quarter rows: " + $ids.Count)
Write-Host ("original wording: " + $(if ($d.options.tq_wording) { $d.options.tq_wording } else { "degrees (default)" }))
if ($ids.Count -eq 0) { Write-Host "no three-quarter rows to test."; exit 0 }

if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
$all = @{}

foreach ($w in $Wordings) {
    Write-Host ""
    Write-Host ("=== " + $w.ToUpper() + " ===") -ForegroundColor Cyan
    $set = PostRaw "/datasets/$Id/tq-wording" @{ wording = $w } "Put"
    if ($null -eq $set) { Write-Host "  could not set the wording - skipping." -ForegroundColor Yellow; continue }
    Write-Host ("  reads: " + $set.reads) -ForegroundColor DarkGray

    if (-not $ScoreOnly) {
        Write-Host ("  rendering " + $ids.Count + " images...") -ForegroundColor Yellow
        $r = PostRaw "/datasets/$Id/generate" @{ item_ids = $ids; overwrite = $true }
        if ($null -eq $r) { Write-Host "  render request failed - skipping this wording." -ForegroundColor Red; continue }
        if ($r.started -eq $false) { Write-Host "  server had nothing to do." -ForegroundColor Yellow }
        else { $null = WaitRun $Id "render" }
    }

    $a = PostRaw "/datasets/$Id/angles" @{}
    if ($null -eq $a) { Write-Host "  scoring failed - skipping." -ForegroundColor Red; continue }
    $rows = @($a.rows | Where-Object { $ids -contains $_.id })
    $all[$w] = $rows

    $wanted = @($rows | Where-Object { $_.ok -eq $true }).Count
    $meas = @($rows | Where-Object { $null -ne $_.yaw })
    $absList = @($meas | ForEach-Object { [math]::Abs($_.yaw) } | Sort-Object)
    $med = if ($absList.Count) { $absList[[int]($absList.Count / 2)] } else { 0 }
    $wrongWay = 0
    foreach ($x in $meas) {
        $wantNeg = ($x.angle -eq "three_quarter_left")
        if ($null -ne $x.yaw -and [math]::Abs($x.yaw) -gt 3) { if (($x.yaw -lt 0) -ne $wantNeg) { $wrongWay++ } }
    }
    $inT = @($absList | Where-Object { $_ -ge 25 -and $_ -le 45 }).Count
    Write-Host ("  in target 25-45   : " + $inT + " of " + $rows.Count) -ForegroundColor Green
    Write-Host ("  in band 20-55 deg : " + $wanted + " of " + $rows.Count)
    Write-Host ("  median |yaw|      : " + ("{0:N1}" -f $med) + " deg")
    Write-Host ("  turned the wrong way: " + $wrongWay)

    if ($KeepImages) {
        $vd = Join-Path $outDir $w
        if (Test-Path $vd) { Remove-Item $vd -Recurse -Force }
        New-Item -ItemType Directory -Path $vd -Force | Out-Null
        foreach ($x in @($rows | Where-Object { $_.framing -eq "full" })) {
            $it = $d.items | Where-Object { $_.id -eq $x.id }
            $yawTxt = if ($null -eq $x.yaw) { "noface" } else { ("{0:N0}" -f $x.yaw) }
            $n = "{0}_{1}_yaw{2}.png" -f $x.id, $x.angle, $yawTxt
            try { Invoke-WebRequest ("http://127.0.0.1:8899" + $it.url) -OutFile (Join-Path $vd $n) -TimeoutSec 120 } catch { }
        }
    }
}

Write-Host ""
Write-Host "=== RESULT ===" -ForegroundColor Cyan
Write-Host ("  measured 2026-08-05: halfway 13/16 in target, frame 9, tworef 4, degrees 3") -ForegroundColor DarkGray
Write-Host ""
$summary = @()
foreach ($w in $all.Keys) {
    $rows = $all[$w]
    $meas = @($rows | Where-Object { $null -ne $_.yaw })
    $absList = @($meas | ForEach-Object { [math]::Abs($_.yaw) } | Sort-Object)
    $med = if ($absList.Count) { $absList[[int]($absList.Count / 2)] } else { 0 }
    $wrongWay = 0
    foreach ($x in $meas) {
        $wantNeg = ($x.angle -eq "three_quarter_left")
        if ([math]::Abs($x.yaw) -gt 3) { if (($x.yaw -lt 0) -ne $wantNeg) { $wrongWay++ } }
    }
    $inTarget = @($absList | Where-Object { $_ -ge 25 -and $_ -le 45 }).Count
    $summary += [pscustomobject]@{
        wording = $w
        in_target = $inTarget
        in_band = @($rows | Where-Object { $_.ok -eq $true }).Count
        of = $rows.Count
        median_abs_yaw = [math]::Round($med, 1)
        max_abs_yaw = if ($absList.Count) { [math]::Round($absList[-1], 1) } else { 0 }
        wrong_way = $wrongWay
        unmeasured = @($rows | Where-Object { $null -eq $_.yaw }).Count
    }
}
# v1.236: ranked on the TARGET window (25-45 deg), not on the wide pass/fail
# band. Measured why: "frame" put 15 of 16 inside 20-55 and won the band count,
# but its median was 43.4 and its max 53.9 - near-profile faces on square
# shoulders, and the dataset already has 8 real profiles at 56-82. On the 25-45
# window it is halfway 13, frame 9, tworef 4, degrees 3, which is also what the
# pictures show. Wrong-way turns break the tie before the median does.
$ranked = $summary | Sort-Object -Property @{Expression="in_target";Descending=$true}, @{Expression="wrong_way";Descending=$false}, @{Expression="in_band";Descending=$true}
$ranked | Format-Table -AutoSize

$best = $ranked[0]
Write-Host ("  best: " + $best.wording + "  (" + $best.in_target + " of " + $best.of + " in the 25-45 target, " + $best.in_band + " in the wider band, median |yaw| " + $best.median_abs_yaw + " deg, " + $best.wrong_way + " wrong way)") -ForegroundColor Green
$null = PostRaw "/datasets/$Id/tq-wording" @{ wording = $best.wording } "Put"
Write-Host ("  dataset left set to: " + $best.wording) -ForegroundColor Green

$payload = @{ dataset = $Id; summary = $summary; rows = $all }
$payload | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $outDir "results.json") -Encoding UTF8
Write-Host ""
Write-Host ("wrote " + (Join-Path $outDir "results.json")) -ForegroundColor Green
Write-Host "Tell Claude 'tq test is done'." -ForegroundColor Green
