# Save the images Claude needs to LOOK at into scripts\_diag\images\.
#
#   powershell -ExecutionPolicy Bypass -File scripts\dump_images.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\dump_images.ps1 -Framing full
#   powershell -ExecutionPolicy Bypass -File scripts\dump_images.ps1 -Ids 0029,0035,0040
#   powershell -ExecutionPolicy Bypass -File scripts\dump_images.ps1 -Flagged
#
# The repo folder is the one place Claude can read directly, so this is how he
# stops guessing about pictures he cannot see. Read-only; copies, never moves.

param(
    [string]$Id = "",
    [string]$Framing = "",
    [string[]]$Ids = @(),
    [switch]$Flagged,
    [switch]$Cropped,
    [int]$Max = 16,
    [switch]$IncludeBase,
    [switch]$ListDatasets,
    [string]$BaseUrl = "http://127.0.0.1:8899/api/lora"
)

$ErrorActionPreference = "Stop"
$out = Join-Path $PSScriptRoot "_diag\images"
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Path $out -Force | Out-Null

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

$sel = @($d.items | Where-Object { $_.has_image })
# PowerShell parses an unquoted 0029 as the NUMBER 29, so "-Ids 0001,0021" used
# to arrive as "1","21" and match nothing at all. Normalise both sides to the
# 4-digit form rather than making the caller remember quotes.
$want = @()
foreach ($x in $Ids) { foreach ($piece in ([string]$x -split '[,;\s]+')) { if ($piece -match '[0-9]') { $want += ("{0:0000}" -f [int]($piece -replace '[^0-9]','')) } } }
if ($want.Count) {
    Write-Host ("looking for: " + ($want -join ", "))
    $sel = @($sel | Where-Object { $want -contains ("{0:0000}" -f [int]("" + $_.id -replace '[^0-9]','')) })
    if ($sel.Count -eq 0) { Write-Host ("rendered ids in this dataset: " + ((@($d.items | Where-Object { $_.has_image }) | ForEach-Object { $_.id }) -join ", ")) -ForegroundColor Yellow }
}
if ($Framing)    { $sel = @($sel | Where-Object { $_.framing -eq $Framing }) }
if ($Cropped)    { $sel = @($sel | Where-Object { $_.qc.cropped_badly }) }
if ($Flagged)    { $sel = @($sel | Where-Object { $_.qc.ok -eq $false }) }
$sel = @($sel | Select-Object -First $Max)

if ($sel.Count -eq 0) { Write-Host "nothing matched." -ForegroundColor Yellow; exit 0 }
Write-Host ("saving " + $sel.Count + " image(s) to " + $out)

$notes = @()
foreach ($it in $sel) {
    # the filename carries the facts, so the picture is self-describing
    $crop = if ($it.qc.cropped_badly) { "CROP" } else { "ok" }
    $fok  = if ($it.qc.framing_ok -eq $false) { "FRAMINGBAD" } else { "framingok" }
    $name = "{0}_{1}_{2}_{3}_{4}.png" -f $it.id, $it.framing, $it.angle, $crop, $fok
    $url  = "http://127.0.0.1:8899" + $it.url
    try {
        Invoke-WebRequest $url -OutFile (Join-Path $out $name) -TimeoutSec 60
        Write-Host ("  " + $name)
    } catch {
        Write-Host ("  FAILED " + $it.id + ": " + $_.Exception.Message) -ForegroundColor Yellow
        continue
    }
    $notes += [pscustomobject]@{
        id = $it.id; file = $name; framing = $it.framing; angle = $it.angle
        expression = $it.expression; width = $it.width; height = $it.height
        base_used = $it.identity
        framing_ok = $it.qc.framing_ok; cropped_badly = $it.qc.cropped_badly
        angle_ok = $it.qc.angle_ok; expression_ok = $it.qc.expression_ok
        identity_score = $it.qc.identity_score
        issues = $it.qc.issues
    }
}

# the reference images, so the renders can be compared against what they came from
if ($IncludeBase) {
    try {
        $c = Invoke-RestMethod ("http://127.0.0.1:8899/api/klein3/characters/" + $d.char_slug)
        foreach ($r in $c.refs) {
            $n = "REF_" + $r.tag + "_" + $r.id + ".png"
            Invoke-WebRequest ("http://127.0.0.1:8899" + $r.url) -OutFile (Join-Path $out $n) -TimeoutSec 60
            Write-Host ("  " + $n)
        }
        if ($c.active_base_url) {
            Invoke-WebRequest ("http://127.0.0.1:8899" + $c.active_base_url) -OutFile (Join-Path $out "REF_activebase.png") -TimeoutSec 60
            Write-Host "  REF_activebase.png"
        }
    } catch { Write-Host ("  could not fetch references: " + $_.Exception.Message) -ForegroundColor Yellow }
}

$notes | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $out "_notes.json") -Encoding UTF8
Write-Host ""
Write-Host ("done - " + $sel.Count + " images + _notes.json") -ForegroundColor Green
Write-Host "Tell Claude 'images are ready'." -ForegroundColor Green
