# RBMN likeness diagnostic. ASCII only, single-line statements, nothing to mangle.
#
#   powershell -ExecutionPolicy Bypass -File scripts\diag_likeness.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\diag_likeness.ps1 -Id dorian-v1-b1966f
#
# Start run.bat first. Everything here is read-only and costs no GPU.

param(
    [string]$Id = "",
    [switch]$Rescore,
    [string]$BaseUrl = "http://127.0.0.1:8899/api/lora"
)

$ErrorActionPreference = "Stop"

function Head($t) { Write-Host ""; Write-Host ("=== " + $t + " ===") -ForegroundColor Cyan }

# ---------------------------------------------------------------- reachable?
try {
    $null = Invoke-RestMethod "$BaseUrl/health" -TimeoutSec 10
    $ver = (Invoke-RestMethod "http://127.0.0.1:8899/api/health" -TimeoutSec 10).version
    Write-Host ("running version: " + $ver) -ForegroundColor Green
} catch {
    Write-Host "Cannot reach the backend at $BaseUrl" -ForegroundColor Red
    Write-Host "Start run.bat and wait for it to finish booting, then run this again." -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------- which set?
if (-not $Id) {
    $all = (Invoke-RestMethod "$BaseUrl/datasets").datasets
    if (-not $all) { Write-Host "No datasets found." -ForegroundColor Yellow; exit 1 }
    # v1.245: NEWEST, not most-rendered. Sorting by "rendered" kept measuring the
    # 40-image dorian set while a new character was the thing being tested.
    $Id = $all[0].id
}
Write-Host ("dataset: " + $Id) -ForegroundColor Green

if ($Rescore) {
    Write-Host "re-scoring with the current code (CPU only, model already cached)..." -ForegroundColor Yellow
    $r = Invoke-RestMethod -Method Post "$BaseUrl/datasets/$Id/likeness" -TimeoutSec 900
    Write-Host ("rescored " + $r.scored + " images against " + $r.baselines.Count + " baselines")
    Write-Host ("sanity: " + $r.distribution.sanity)
    $r.distribution.bands | Format-Table -AutoSize
    if ($r.flags.PSObject.Properties.Name -contains "back_low_likeness") {
        Write-Host ("back_low_likeness (informational, no longer failed): " + $r.flags.back_low_likeness)
    }
    Write-Host ("identity_off (real identity failures): " + $r.flags.identity_off)
    Write-Host ("no_face: " + $r.flags.no_face)
}

$d = Invoke-RestMethod "$BaseUrl/datasets/$Id"
$items = @($d.items)
$scored = @($items | Where-Object { $_.qc -and $_.qc.identity_score -ne $null })
Write-Host ("items: " + $items.Count + "   qc-checked: " + @($items | Where-Object { $_.qc }).Count + "   arcface-scored: " + $scored.Count)

# ---------------------------------------------------------------- A
Head "A. the weakest images, and what they were asked to be"
$worst = $scored | Sort-Object { $_.qc.identity_score } | Select-Object -First 8
$worst | Select-Object id, framing, angle, expression, outfit, @{n = "score"; e = { [math]::Round($_.qc.identity_score, 3) } }, @{n = "verdict"; e = { $_.qc.identity_verdict } }, @{n = "base_used"; e = { $_.identity } } | Format-Table -AutoSize

Head "A2. the no-face rows (expected: back shots)"
$noface = @($items | Where-Object { $_.qc -and $_.qc.identity_method -eq "arcface" -and $_.qc.identity_score -eq $null })
if ($noface.Count -eq 0) { Write-Host "  none tagged. These values were written by whichever code ran the LAST scan -- re-run with -Rescore to refresh them." -ForegroundColor Yellow }
$noface | Select-Object id, framing, angle, @{n = "base_used"; e = { $_.identity } } | Format-Table -AutoSize

# ---------------------------------------------------------------- B
Head "B. identity by ANGLE  (the question that matters most)"
$rowsB = @()
foreach ($g in ($scored | Group-Object angle)) {
    $vals = @($g.Group | ForEach-Object { $_.qc.identity_score } | Sort-Object)
    $rowsB += [pscustomobject]@{
        angle  = $g.Name
        n      = $g.Count
        min    = [math]::Round($vals[0], 3)
        median = [math]::Round($vals[[int][math]::Floor($vals.Count / 2)], 3)
        max    = [math]::Round($vals[$vals.Count - 1], 3)
    }
}
$rowsB | Sort-Object median | Format-Table -AutoSize

Head "B2. identity by FRAMING"
$rowsB2 = @()
foreach ($g in ($scored | Group-Object framing)) {
    $vals = @($g.Group | ForEach-Object { $_.qc.identity_score } | Sort-Object)
    $rowsB2 += [pscustomobject]@{
        framing = $g.Name
        n       = $g.Count
        median  = [math]::Round($vals[[int][math]::Floor($vals.Count / 2)], 3)
    }
}
$rowsB2 | Sort-Object median | Format-Table -AutoSize

Head "B3. identity by WHICH BASE IMAGE was used"
$rowsB3 = @()
foreach ($g in ($scored | Group-Object identity)) {
    $vals = @($g.Group | ForEach-Object { $_.qc.identity_score } | Sort-Object)
    $rowsB3 += [pscustomobject]@{
        base   = $g.Name
        n      = $g.Count
        median = [math]::Round($vals[[int][math]::Floor($vals.Count / 2)], 3)
    }
}
$rowsB3 | Sort-Object median | Format-Table -AutoSize

# ---------------------------------------------------------------- C
Head "C1. angle misses, by the angle that was PLANNED"
@($items | Where-Object { $_.qc -and $_.qc.angle_ok -eq $false }) | Group-Object angle | Select-Object Name, Count | Sort-Object Count -Descending | Format-Table -AutoSize

Head "C2. every planned angle, with how many missed"
$rowsC = @()
foreach ($g in ($items | Where-Object { $_.qc } | Group-Object angle)) {
    $miss = @($g.Group | Where-Object { $_.qc.angle_ok -eq $false }).Count
    $rowsC += [pscustomobject]@{ angle = $g.Name; planned = $g.Count; missed = $miss; pct = [int](100 * $miss / $g.Count) }
}
$rowsC | Sort-Object pct -Descending | Format-Table -AutoSize

Head "C3. expression misses, by framing"
@($items | Where-Object { $_.qc -and $_.qc.expression_ok -eq $false }) | Group-Object framing | Select-Object Name, Count | Sort-Object Count -Descending | Format-Table -AutoSize

Head "C4. the character's references (a missing view explains a lot)"
try {
    $slug = $d.char_slug
    $c = Invoke-RestMethod ("http://127.0.0.1:8899/api/klein3/characters/" + $slug)
    Write-Host ("character: " + $slug)
    if ($c.PSObject.Properties.Name -contains "base_mode") { Write-Host ("base_mode: " + $c.base_mode) }
    if ($c.PSObject.Properties.Name -contains "base_sources") { $c.base_sources | ConvertTo-Json -Depth 4 }
    Write-Host "reference tags present:"
    @($c.refs) | Group-Object tag | Select-Object Name, Count | Format-Table -AutoSize
    if ($c.PSObject.Properties.Name -contains "missing_views") { Write-Host ("missing views: " + ($c.missing_views -join ", ")) }
} catch {
    Write-Host ("could not read the character: " + $_.Exception.Message) -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. Copy everything above." -ForegroundColor Green
