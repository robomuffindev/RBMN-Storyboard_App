# Batched: re-render + re-QC one shot type, straight against the API.
#
#   powershell -ExecutionPolicy Bypass -File scripts\crop_test.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\crop_test.ps1 -Framing upper
#   powershell -ExecutionPolicy Bypass -File scripts\crop_test.ps1 -QcOnly
#
# Bypasses the UI completely, so if the panel says a run started and nothing
# happens, this shows the actual server response instead of a friendly message.

param(
    [string]$Id = "",
    [string]$Framing = "full",
    [switch]$QcOnly,
    [switch]$Replan,
    [switch]$ListDatasets,
    [string]$BaseUrl = "http://127.0.0.1:8899/api/lora"
)

$ErrorActionPreference = "Stop"

function Show($label, $obj) {
    Write-Host ("  " + $label + ": ") -NoNewline
    Write-Host ($obj | ConvertTo-Json -Depth 4 -Compress)
}

function PostRaw($path, $bodyObj) {
    $json = $bodyObj | ConvertTo-Json -Depth 6
    try {
        return Invoke-RestMethod -Method Post "$BaseUrl$path" -Body $json -ContentType "application/json" -TimeoutSec 300
    } catch {
        Write-Host ("  REQUEST FAILED: POST " + $path) -ForegroundColor Red
        Write-Host ("  " + $_.Exception.Message) -ForegroundColor Red
        $r = $_.Exception.Response
        if ($r) {
            try {
                $sr = New-Object System.IO.StreamReader($r.GetResponseStream())
                Write-Host ("  server said: " + $sr.ReadToEnd()) -ForegroundColor Red
            } catch { }
        }
        return $null
    }
}

function WaitRun($dsId, $what) {
    $t = 0; $lastDetail = ""
    while ($true) {
        Start-Sleep -Seconds 5; $t += 5
        try { $s = Invoke-RestMethod "$BaseUrl/datasets/$dsId" -TimeoutSec 30 } catch { continue }
        if (-not $s.run -or $s.run.status -ne "running") {
            if ($s.run) { Write-Host ("  " + $what + " ended: " + $s.run.status + " " + $s.run.detail) }
            if ($s.run.error) { Write-Host ("  error: " + $s.run.error) -ForegroundColor Red }
            return $s
        }
        if ($s.run.detail -ne $lastDetail) {
            Write-Host ("  " + $what + ": " + $s.run.detail + "   (" + $t + "s)")
            $lastDetail = $s.run.detail
        } elseif ($t % 30 -eq 0) {
            Write-Host ("  " + $what + ": still " + $s.run.detail + "   (" + $t + "s, no change)") -ForegroundColor DarkGray
        }
    }
}

$ver = (Invoke-RestMethod "http://127.0.0.1:8899/api/health" -TimeoutSec 10).version
Write-Host ("backend " + $ver) -ForegroundColor Green
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
Write-Host ("dataset " + $Id)

# is something already believed to be running? that alone blocks a new run
if ($d.run -and $d.run.status -eq "running") {
    Write-Host ("  A run is already marked RUNNING (" + $d.run.kind + " " + $d.run.detail + ").") -ForegroundColor Yellow
    Write-Host "  A new render or QC will be refused with 409 until it clears." -ForegroundColor Yellow
}

$sel = @($d.items | Where-Object { $_.framing -eq $Framing })
$ids = @($sel | ForEach-Object { $_.id })
$croppedBefore = @($sel | Where-Object { $_.qc.cropped_badly }).Count
$framingBefore = @($sel | Where-Object { $_.qc.framing_ok -eq $false }).Count
Write-Host ("shot type '" + $Framing + "': " + $sel.Count + " images, " + $croppedBefore +
            " cropped, " + $framingBefore + " framing-flagged")
Write-Host ("rendered at: " + (($sel | ForEach-Object { "" + $_.width + "x" + $_.height } | Sort-Object -Unique) -join ", "))
if ($sel.Count -eq 0) { Write-Host "nothing to do."; exit 0 }

if ($Replan) {
    Write-Host ""
    Write-Host "re-planning so the rows pick up the new canvas size..." -ForegroundColor Yellow
    $r = PostRaw "/datasets/$Id/plan-preview" @{}
    if ($r) { Show "would discard" $r.impact }
    $r = PostRaw "/datasets/$Id/plan" @{ force = $true }
    if ($r) { Show "impact" $r.impact }
    $d = Invoke-RestMethod "$BaseUrl/datasets/$Id"
    $sel = @($d.items | Where-Object { $_.framing -eq $Framing })
    $ids = @($sel | ForEach-Object { $_.id })
    Write-Host ("now rendered at: " + (($sel | ForEach-Object { "" + $_.width + "x" + $_.height } | Sort-Object -Unique) -join ", "))
}

if (-not $QcOnly) {
    Write-Host ""
    Write-Host ("re-rendering " + $ids.Count + " images...") -ForegroundColor Yellow
    $r = PostRaw "/datasets/$Id/generate" @{ item_ids = $ids; overwrite = $true }
    if ($null -eq $r) { Write-Host "render request failed - stopping." -ForegroundColor Red; exit 1 }
    Show "server replied" $r
    if ($r.started -eq $false) { Write-Host "  server had nothing to do." -ForegroundColor Yellow }
    else { $null = WaitRun $Id "render" }
}

Write-Host ""
Write-Host ("QC on " + $ids.Count + " images...") -ForegroundColor Yellow
$r = PostRaw "/datasets/$Id/qc" @{ item_ids = $ids; overwrite = $true }
if ($null -eq $r) { Write-Host "QC request failed - see the error above." -ForegroundColor Red; exit 1 }
Show "server replied" $r
if ($r.started -eq $false) {
    Write-Host "  server had nothing to do." -ForegroundColor Yellow
} else {
    $null = WaitRun $Id "qc"
}

$d = Invoke-RestMethod "$BaseUrl/datasets/$Id"
$sel = @($d.items | Where-Object { $_.framing -eq $Framing })
$croppedAfter = @($sel | Where-Object { $_.qc.cropped_badly }).Count
$framingAfter = @($sel | Where-Object { $_.qc.framing_ok -eq $false }).Count
Write-Host ""
Write-Host "=== RESULT ===" -ForegroundColor Cyan
Write-Host ("  shot type        : " + $Framing + "  (" + $sel.Count + " images)")
Write-Host ("  cropped          : " + $croppedBefore + "  ->  " + $croppedAfter)
Write-Host ("  framing flagged  : " + $framingBefore + "  ->  " + $framingAfter)
Write-Host ("  whole dataset    : ") -NoNewline
Write-Host ($d.flags | ConvertTo-Json -Depth 2 -Compress)
Write-Host ""
$sel | Select-Object id, angle, @{n="crop";e={$_.qc.cropped_badly}}, @{n="framing_ok";e={$_.qc.framing_ok}}, @{n="issues";e={($_.qc.issues) -join "; "}} | Format-Table -AutoSize -Wrap
