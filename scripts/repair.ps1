# Repair a dataset against EVERY measurement, not just one.
#
#   powershell -ExecutionPolicy Bypass -File scripts\repair.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\repair.ps1 -Rounds 4
#   powershell -ExecutionPolicy Bypass -File scripts\repair.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File scripts\repair.ps1 -ListDatasets
#
# WHY THIS REPLACES repair_angles.ps1
#   repair_angles re-rendered until the ANGLE was right and checked nothing else.
#   Measured on redv1: of the three rows it repaired, TWO came back scoring 0.19
#   and 0.21 on ArcFace - below the "different person" floor. It fixed the angle
#   and broke the face, and nothing noticed until a separate likeness run.
#
#   This loop re-measures angle, shot type, crop AND identity every round, and
#   repairs anything failing any of them. It also reports rows that OSCILLATE -
#   fixed on one measure, broken on another, round after round - because those
#   are a plan problem and no amount of re-rendering solves them.
#
# Angles and likeness are both CPU-only and take seconds. The renders are the
# only slow part, and only failing rows are re-rendered.

param(
    [string]$Id = "",
    [int]$Rounds = 3,
    [switch]$DryRun,
    [switch]$SkipFinalQc,
    [switch]$ListDatasets,
    [string]$BaseUrl = "http://127.0.0.1:8899/api/lora"
)

$ErrorActionPreference = "Stop"

function PostRaw($path, $bodyObj) {
    $json = $bodyObj | ConvertTo-Json -Depth 6
    try {
        return Invoke-RestMethod -Method Post "$BaseUrl$path" -Body $json -ContentType "application/json" -TimeoutSec 1800
    } catch {
        Write-Host ("  REQUEST FAILED: POST " + $path) -ForegroundColor Red
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
if (([version]$ver) -lt ([version]"1.248.0")) {
    Write-Host "  This needs v1.248 or newer. Restart run.bat and try again." -ForegroundColor Yellow
    exit 1
}

$allDs = @((Invoke-RestMethod "$BaseUrl/datasets").datasets)
if ($ListDatasets) {
    Write-Host "datasets, newest first:" -ForegroundColor Cyan
    $allDs | Select-Object id, name, char_slug, total, rendered, flagged, created_at | Format-Table -AutoSize
    exit 0
}
if (-not $Id) { $Id = $allDs[0].id }
$dsInfo = $allDs | Where-Object { $_.id -eq $Id }
if (-not $dsInfo) { Write-Host ("No dataset with id '" + $Id + "'. Use -ListDatasets.") -ForegroundColor Red; exit 1 }
Write-Host ("DATASET: " + $dsInfo.name + "   character: " + $dsInfo.char_slug) -ForegroundColor Cyan
Write-Host ("  id " + $dsInfo.id + " - " + $dsInfo.rendered + " of " + $dsInfo.total + " rendered")
if ($allDs.Count -gt 1 -and -not $PSBoundParameters.ContainsKey("Id")) {
    Write-Host ("  (newest of " + $allDs.Count + " datasets - pass -Id to pick another)") -ForegroundColor DarkGray
}

# id -> list of reasons it failed, one entry per round. An id that keeps failing
# for a DIFFERENT reason each round is oscillating, not unlucky.
$seenReasons = @{}
$history = @()
$repaired = @()

for ($round = 1; $round -le $Rounds; $round++) {
    $a = PostRaw "/datasets/$Id/angles" @{}
    if ($null -eq $a) { Write-Host "angle/shot-type scoring failed - stopping." -ForegroundColor Red; exit 1 }
    $l = PostRaw "/datasets/$Id/likeness" @{}
    if ($null -eq $l) { Write-Host "likeness scoring failed - stopping." -ForegroundColor Red; exit 1 }
    $d = Invoke-RestMethod "$BaseUrl/datasets/$Id" -TimeoutSec 60

    $bad = @{}
    foreach ($r in $a.rows) {
        $why = @()
        if ($r.ok -eq $false) { $why += ("angle: " + $r.note) }
        if ($r.framing_ok -eq $false) { $why += ("shot type: " + $r.framing_note) }
        if ($r.crop_ok -eq $false) { $why += ("crop: " + $r.crop_note) }
        if ($why.Count) { $bad[$r.id] = $why }
    }
    foreach ($it in $d.items) {
        if ($it.qc -and $it.qc.same_person -eq $false) {
            $s = if ($null -ne $it.qc.identity_score) { $it.qc.identity_score } else { "?" }
            $msg = "identity: " + $s + " against the " + $it.qc.identity_baseline + " baseline - below the different-person floor"
            if ($bad.ContainsKey($it.id)) { $bad[$it.id] += $msg } else { $bad[$it.id] = @($msg) }
        }
    }

    $okCount = $d.items.Count - $bad.Count
    Write-Host ""
    Write-Host ("=== round " + $round + ": " + $okCount + " clean, " + $bad.Count + " to repair ===") -ForegroundColor Cyan
    $history += [pscustomobject]@{ round = $round; clean = $okCount; wrong = $bad.Count }

    if ($bad.Count -eq 0) { Write-Host "  nothing left to repair." -ForegroundColor Green; break }

    foreach ($k in ($bad.Keys | Sort-Object)) {
        $it = $d.items | Where-Object { $_.id -eq $k }
        Write-Host ("  " + $k + "  " + $it.framing + "  " + $it.angle)
        foreach ($w in $bad[$k]) { Write-Host ("      " + $w) }
        $kinds = @($bad[$k] | ForEach-Object { ($_ -split ":")[0] })
        if (-not $seenReasons.ContainsKey($k)) { $seenReasons[$k] = @() }
        $seenReasons[$k] += ($kinds -join "+")
    }

    if ($DryRun) { Write-Host "  -DryRun: not rendering." -ForegroundColor Yellow; break }

    $ids = @($bad.Keys)
    Write-Host ("  re-rendering " + $ids.Count + " image(s)...") -ForegroundColor Yellow
    $r = PostRaw "/datasets/$Id/generate" @{ item_ids = $ids; overwrite = $true }
    if ($null -eq $r) { Write-Host "  render request failed - stopping." -ForegroundColor Red; break }
    if ($r.started -eq $false) { Write-Host "  server had nothing to do - stopping." -ForegroundColor Yellow; break }
    $null = WaitRun $Id "render"
    $repaired += $ids
}

# The vision-model answers (one person, artifacts, outfit) belong to the image
# that WAS there. A repaired row carries a stale verdict until it is re-checked.
$repaired = @($repaired | Sort-Object -Unique)
if ($repaired.Count -and -not $DryRun -and -not $SkipFinalQc) {
    Write-Host ""
    Write-Host ("re-running vision QC on the " + $repaired.Count + " repaired row(s) - their 'one person' and 'artifacts' answers belong to the old images...") -ForegroundColor Yellow
    $r = PostRaw "/datasets/$Id/qc" @{ item_ids = $repaired; overwrite = $true }
    if ($null -ne $r -and $r.started -ne $false) { $null = WaitRun $Id "qc" }
}

$a = PostRaw "/datasets/$Id/angles" @{}
$l = PostRaw "/datasets/$Id/likeness" @{}
$d = Invoke-RestMethod "$BaseUrl/datasets/$Id" -TimeoutSec 60

Write-Host ""
Write-Host "=== FINAL ===" -ForegroundColor Cyan
Write-Host "  angle:" -ForegroundColor DarkGray
foreach ($p in $a.by_angle.PSObject.Properties) {
    $b = $p.Value
    $med = if ($null -eq $b.yaw_median) { "  -  " } else { "{0,6:N1}" -f $b.yaw_median }
    Write-Host ("    {0,-20} n={1,-3} ok {2,-3} miss {3,-3} unmeasured {4,-3}  median yaw {5}" -f $p.Name, $b.n, $b.ok, $b.miss, $b.unmeasured, $med)
}
Write-Host "  shot type:" -ForegroundColor DarkGray
foreach ($p in $a.by_framing.PSObject.Properties) {
    $b = $p.Value
    Write-Host ("    {0,-10} n={1,-3} ok {2,-3} miss {3,-3} unmeasured {4,-3}" -f $p.Name, $b.n, $b.ok, $b.miss, $b.unmeasured)
}
Write-Host "  identity, per baseline:" -ForegroundColor DarkGray
foreach ($p in $l.by_baseline.PSObject.Properties) {
    $b = $p.Value
    $med = if ($null -eq $b.median) { " - " } else { "{0,6:N3}" -f $b.median }
    Write-Host ("    {0,-8} n={1,-3} scored {2,-3} median {3}  min {4,6:N3}  below match {5}  (from {6} reference(s))" -f $p.Name, $b.n, $b.scored, $med, $b.min, $b.below_match, $b.baselines)
}
Write-Host ("  flagged: " + $d.flags.flagged + " of " + $d.items.Count)

Write-Host ""
Write-Host "  rounds:" -ForegroundColor DarkGray
$history | Format-Table -AutoSize

$osc = @($seenReasons.Keys | Where-Object { (@($seenReasons[$_] | Sort-Object -Unique)).Count -gt 1 })
if ($osc.Count) {
    Write-Host ("  " + $osc.Count + " row(s) OSCILLATED - fixed on one measure, broken on another:") -ForegroundColor Yellow
    foreach ($k in $osc) { Write-Host ("    " + $k + "  " + ($seenReasons[$k] -join "  ->  ")) }
    Write-Host "  Those are a plan problem, not bad luck. More rendering will not settle them." -ForegroundColor Yellow
}

$stillBad = @($d.items | Where-Object { $_.qc.ok -eq $false })
if ($stillBad.Count) {
    Write-Host ("  " + $stillBad.Count + " row(s) still failing:") -ForegroundColor Yellow
    foreach ($m in $stillBad) { Write-Host ("    " + $m.id + "  " + $m.framing + "  " + $m.angle + "  " + (($m.qc.issues) -join "; ")) }
}

$a | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $PSScriptRoot "_diag\angles.json") -Encoding UTF8
$l | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $PSScriptRoot "_diag\likeness.json") -Encoding UTF8
Write-Host ""
Write-Host "Tell Claude 'repair is done'." -ForegroundColor Green
