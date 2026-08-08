# Watch what the backend is ACTUALLY doing, straight from the API.
#
#   powershell -ExecutionPolicy Bypass -File scripts\watch_run.ps1
#
# Answers "is it running or is it hung" without trusting the UI.
# Read-only. Ctrl+C to stop.

param(
    [string]$Id = "",
    [int]$Every = 3,
    [switch]$ListDatasets,
    [string]$BaseUrl = "http://127.0.0.1:8899/api/lora"
)

$ErrorActionPreference = "Stop"

try {
    $ver = (Invoke-RestMethod "http://127.0.0.1:8899/api/health" -TimeoutSec 10).version
    Write-Host ("backend up, version " + $ver) -ForegroundColor Green
    $script:CanAnswer = ([version]$ver) -ge ([version]"1.231.0")
    if (-not $script:CanAnswer) {
        Write-Host ("  NOTE: this backend predates v1.231, so it cannot report when QC last ran.") -ForegroundColor Yellow
        Write-Host ("  Restart run.bat to get a real answer - until then 'no run' only means") -ForegroundColor Yellow
        Write-Host ("  'nothing running in the CURRENT process', which a restart always resets.") -ForegroundColor Yellow
    }
} catch {
    Write-Host "Backend is NOT reachable. That alone explains a dead UI." -ForegroundColor Red
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
Write-Host ("watching: " + $Id)

# how many vision servers are there? one server = strictly sequential = slow
try {
    $h = Invoke-RestMethod "$BaseUrl/health"
    Write-Host ("vision servers: " + $h.vision.servers + "   model: " + $h.vision.model)
    if ($h.vision.servers -le 1) {
        Write-Host "  only one Ollama server - images are checked one at a time, so a 40-image pass is minutes, not seconds." -ForegroundColor Yellow
    }
} catch { Write-Host "could not read /health" -ForegroundColor Yellow }

Write-Host ""
Write-Host "Ctrl+C to stop." -ForegroundColor Cyan
$last = ""
$idle = 0
while ($true) {
    try {
        $d = Invoke-RestMethod "$BaseUrl/datasets/$Id" -TimeoutSec 30
    } catch {
        Write-Host ((Get-Date -Format "HH:mm:ss") + "  REFRESH FAILED: " + $_.Exception.Message) -ForegroundColor Red
        Start-Sleep -Seconds $Every; continue
    }
    $r = $d.run
    $stamp = Get-Date -Format "HH:mm:ss"
    if (-not $r) {
        # v1.231: _RUNS is in-memory and dies with the process, so "no run" is
        # ambiguous after a restart. The durable answer is in the data.
        $la = $d.last_activity
        if ($la -and $la.qc_last) {
            $age = [int]$la.qc_age_s
            $when = if ($age -lt 90) { "$age seconds ago" }
                    elseif ($age -lt 5400) { "" + [math]::Round($age / 60) + " minutes ago" }
                    else { "" + [math]::Round($age / 3600, 1) + " hours ago" }
            Write-Host ($stamp + "  nothing running NOW. Last QC: " + $la.qc_last_batch +
                        " image(s) " + $when + "  (" + $la.qc_count + " of " +
                        $d.items.Count + " ever checked, " + $la.rendered + " rendered)")
            if ($age -lt 600) {
                Write-Host "        that is recent - your run DID complete; the screen was stale." -ForegroundColor Green
            }
        } elseif (-not $script:CanAnswer) {
            $checked = @($d.items | Where-Object { $_.qc }).Count
            Write-Host ($stamp + "  nothing running in this process. This backend cannot say WHEN " +
                        "QC last ran, but " + $checked + " of " + $d.items.Count + " images carry a QC result.")
            Write-Host "        Restart run.bat for the timestamps." -ForegroundColor Yellow
        } else {
            Write-Host ($stamp + "  no run recorded, and no image carries a QC result yet")
        }
        $idle++
    } elseif ($r.status -eq "running") {
        $idle = 0
        $busy = @()
        if ($r.tasks) {
            $busy = @($r.tasks.PSObject.Properties | Where-Object { $_.Value.status -eq "running" } |
                      ForEach-Object { "#" + $_.Name + "@" + ($_.Value.server, $_.Value.worker -ne $null)[0] })
        }
        $line = $stamp + "  RUNNING " + $r.kind + "  " + $r.detail + "  " + ($busy -join " ")
        Write-Host $line -ForegroundColor Green
        if ($line -eq $last) { Write-Host "        (no change since last tick)" -ForegroundColor DarkGray }
        $last = $line
    } else {
        Write-Host ($stamp + "  run finished: " + $r.status + "  " + $r.detail) -ForegroundColor Cyan
        if ($r.error) { Write-Host ("        error: " + $r.error) -ForegroundColor Red }
        $checked = @($d.items | Where-Object { $_.qc }).Count
        $flagged = @($d.items | Where-Object { $_.qc.ok -eq $false }).Count
        Write-Host ("        checked " + $checked + " of " + $d.items.Count + ", flagged " + $flagged)
        if ($d.flags) {
            Write-Host ("        framing_off " + $d.flags.framing_off + "  angle_off " + $d.flags.angle_off +
                        "  expression_off " + $d.flags.expression_off + "  identity_off " + $d.flags.identity_off)
        }
        break
    }
    if ($idle -ge 3) { Write-Host "nothing running. Exiting." -ForegroundColor Yellow; break }
    Start-Sleep -Seconds $Every
}
