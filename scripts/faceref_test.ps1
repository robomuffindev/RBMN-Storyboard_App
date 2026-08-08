# Does giving `upper` and `full` rows the FACE reference fix identity?
#
#   powershell -ExecutionPolicy Bypass -File scripts\faceref_test.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\faceref_test.ps1 -Id redv1-bca382
#   powershell -ExecutionPolicy Bypass -File scripts\faceref_test.ps1 -ListDatasets
#
# WHY
#   redv1's three worst identity scores - 0.19, 0.20, 0.21 - are all `upper` or
#   `full` rows. Those are exactly the framings that DO NOT get the character's
#   face reference; only face and headshot do. Its base is a wide full-body
#   photograph where the face is about a twelfth of the frame height, so on those
#   rows Klein has almost no facial detail to preserve and invents it.
#
#   This renders the upper and full rows both ways and compares identity. Nothing
#   else is touched. About 2 x (however many upper+full rows there are).
#
# Needs backend v1.249 or newer.

param(
    [string]$Id = "",
    [switch]$ListDatasets,
    [string]$BaseUrl = "http://127.0.0.1:8899/api/lora"
)

$ErrorActionPreference = "Stop"

function Req($method, $path, $bodyObj) {
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
            return $s
        }
        if ($s.run.detail -ne $lastDetail) {
            Write-Host ("    " + $what + ": " + $s.run.detail + "   (" + $t + "s)")
            $lastDetail = $s.run.detail
        }
    }
}

function ScoreRows($dsId, $ids) {
    $null = Req "Post" "/datasets/$dsId/angles" @{}
    $l = Req "Post" "/datasets/$dsId/likeness" @{}
    $d = Invoke-RestMethod "$BaseUrl/datasets/$dsId" -TimeoutSec 60
    $out = @()
    foreach ($it in $d.items) {
        if ($ids -notcontains $it.id) { continue }
        $out += [pscustomobject]@{
            id = $it.id; framing = $it.framing; angle = $it.angle
            score = $it.qc.identity_score
            baseline = $it.qc.identity_baseline
            face_ref = $it.face_ref_used
            angle_ok = $it.qc.angle_ok
        }
    }
    return $out
}

function Summarise($rows, $label) {
    $vals = @($rows | Where-Object { $null -ne $_.score } | ForEach-Object { $_.score } | Sort-Object)
    if (-not $vals.Count) { Write-Host ("  " + $label + ": nothing scored"); return $null }
    $med = $vals[[int]($vals.Count / 2)]
    $below = @($vals | Where-Object { $_ -lt 0.45 }).Count
    $notHim = @($vals | Where-Object { $_ -lt 0.25 }).Count
    Write-Host ("  {0,-10} n={1,-3} median {2,6:N3}   min {3,6:N3}   max {4,6:N3}   below match {5}   NOT HIM {6}" -f $label, $vals.Count, $med, $vals[0], $vals[-1], $below, $notHim)
    return [pscustomobject]@{ label = $label; n = $vals.Count; median = [math]::Round($med, 4); min = [math]::Round($vals[0], 4); max = [math]::Round($vals[-1], 4); below_match = $below; not_him = $notHim }
}

$ver = (Invoke-RestMethod "http://127.0.0.1:8899/api/health" -TimeoutSec 10).version
Write-Host ("backend " + $ver) -ForegroundColor Green
if (([version]$ver) -lt ([version]"1.249.0")) {
    Write-Host "  This needs v1.249 or newer. Restart run.bat and try again." -ForegroundColor Yellow
    exit 1
}

$allDs = @((Invoke-RestMethod "$BaseUrl/datasets").datasets)
if ($ListDatasets) {
    $allDs | Select-Object id, name, char_slug, total, rendered, flagged, created_at | Format-Table -AutoSize
    exit 0
}
if (-not $Id) { $Id = $allDs[0].id }
$dsInfo = $allDs | Where-Object { $_.id -eq $Id }
if (-not $dsInfo) { Write-Host ("No dataset with id '" + $Id + "'.") -ForegroundColor Red; exit 1 }
Write-Host ("DATASET: " + $dsInfo.name + "   character: " + $dsInfo.char_slug) -ForegroundColor Cyan

$d = Invoke-RestMethod "$BaseUrl/datasets/$Id" -TimeoutSec 60
$targets = @($d.items | Where-Object { $_.framing -eq "upper" -or $_.framing -eq "full" })
$ids = @($targets | ForEach-Object { $_.id })
Write-Host ("  upper + full rows: " + $ids.Count)
if ($ids.Count -eq 0) { Write-Host "nothing to test."; exit 0 }

$orig = if ($d.options.face_ref) { $d.options.face_ref } else { "closeups (default)" }
Write-Host ("  current face_ref setting: " + $orig)

$results = @{}
foreach ($mode in @("closeups", "always")) {
    Write-Host ""
    Write-Host ("=== face_ref = " + $mode.ToUpper() + " ===") -ForegroundColor Cyan
    $set = Req "Put" "/datasets/$Id/face-ref" @{ mode = $mode }
    if ($null -eq $set) { Write-Host "  could not set - skipping." -ForegroundColor Yellow; continue }
    Write-Host ("  shot types that get the face reference: " + ($set.framings -join ", ")) -ForegroundColor DarkGray
    if (-not $set.character_has_face_reference) {
        Write-Host "  THIS CHARACTER HAS NO TAGGED FACE REFERENCE." -ForegroundColor Red
        Write-Host "  Tag one in Klein 3.0 first - this test cannot mean anything without it." -ForegroundColor Red
        exit 1
    }

    Write-Host ("  rendering " + $ids.Count + " rows...") -ForegroundColor Yellow
    $r = Req "Post" "/datasets/$Id/generate" @{ item_ids = $ids; overwrite = $true }
    if ($null -eq $r -or $r.started -eq $false) { Write-Host "  render did not start - skipping." -ForegroundColor Red; continue }
    $null = WaitRun $Id "render"

    $rows = ScoreRows $Id $ids
    $results[$mode] = $rows
    Write-Host ""
    $null = Summarise $rows "all"
    $null = Summarise @($rows | Where-Object { $_.framing -eq "upper" }) "upper"
    $null = Summarise @($rows | Where-Object { $_.framing -eq "full" }) "full"
}

Write-Host ""
Write-Host "=== RESULT ===" -ForegroundColor Cyan
$summary = @()
foreach ($mode in @("closeups", "always")) {
    if (-not $results.ContainsKey($mode)) { continue }
    $s = Summarise $results[$mode] $mode
    if ($s) { $summary += $s }
}

if ($summary.Count -eq 2) {
    $a = $summary | Where-Object { $_.label -eq "closeups" }
    $b = $summary | Where-Object { $_.label -eq "always" }
    $delta = [math]::Round($b.median - $a.median, 4)
    Write-Host ""
    Write-Host ("  median identity moved " + $delta + " (" + $a.median + " -> " + $b.median + ")")
    Write-Host ("  below the match line: " + $a.below_match + " -> " + $b.below_match)
    Write-Host ("  scored as NOT HIM:    " + $a.not_him + " -> " + $b.not_him)
    if ($delta -gt 0.05) {
        Write-Host "  ALWAYS is clearly better. Leaving the dataset set to it." -ForegroundColor Green
        $null = Req "Put" "/datasets/$Id/face-ref" @{ mode = "always" }
    } elseif ($delta -lt -0.05) {
        Write-Host "  CLOSEUPS is better. Leaving the dataset set to it." -ForegroundColor Green
        $null = Req "Put" "/datasets/$Id/face-ref" @{ mode = "closeups" }
    } else {
        Write-Host "  No clear difference on this many rows. Left on closeups." -ForegroundColor Yellow
        $null = Req "Put" "/datasets/$Id/face-ref" @{ mode = "closeups" }
    }
}

$payload = @{ dataset = $Id; summary = $summary; rows = $results }
$payload | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $PSScriptRoot "_diag\faceref.json") -Encoding UTF8
Write-Host ""
Write-Host ("wrote " + (Join-Path $PSScriptRoot "_diag\faceref.json")) -ForegroundColor Green
Write-Host "Tell Claude 'faceref test is done'." -ForegroundColor Green
