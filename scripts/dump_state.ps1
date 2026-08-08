# Dump EVERYTHING Claude might need into scripts\_diag\ as raw JSON.
#
#   powershell -ExecutionPolicy Bypass -File scripts\dump_state.ps1
#
# Read-only. No GPU, no re-scoring, no writes to your datasets.
# The output lands inside the repo folder, which Claude can read directly --
# so there is nothing to copy, paste, or reformat.

param(
    [string]$Root = "http://127.0.0.1:8899",
    [int]$LogLines = 400
)

$ErrorActionPreference = "Continue"
$out = Join-Path $PSScriptRoot "_diag"
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Path $out | Out-Null

$manifest = @()

function Grab($name, $url, $method = "GET", $body = $null) {
    try {
        if ($method -eq "POST") {
            $r = Invoke-RestMethod -Method Post $url -Body $body -ContentType "application/json" -TimeoutSec 120
        } else {
            $r = Invoke-RestMethod $url -TimeoutSec 120
        }
        $p = Join-Path $out ($name + ".json")
        $r | ConvertTo-Json -Depth 40 | Set-Content -Path $p -Encoding UTF8
        $kb = [math]::Round((Get-Item $p).Length / 1KB, 1)
        Write-Host ("  ok    " + $name + "  (" + $kb + " KB)")
        $script:manifest += [pscustomobject]@{ name = $name; url = $url; ok = $true; kb = $kb }
        return $r
    } catch {
        $msg = $_.Exception.Message
        Set-Content -Path (Join-Path $out ($name + ".ERROR.txt")) -Value ($url + "`n" + $msg) -Encoding UTF8
        Write-Host ("  FAIL  " + $name + "  " + $msg) -ForegroundColor Yellow
        $script:manifest += [pscustomobject]@{ name = $name; url = $url; ok = $false; error = $msg }
        return $null
    }
}

Write-Host "collecting..." -ForegroundColor Cyan

# ---- app level -----------------------------------------------------------
Grab "app_health"        "$Root/api/health" | Out-Null
Grab "lora_health"       "$Root/api/lora/health" | Out-Null
Grab "likeness_health"   "$Root/api/lora/likeness-health" | Out-Null
Grab "klein3_health"     "$Root/api/klein3/health" | Out-Null
Grab "gpu_status"        "$Root/api/settings/gpu-status" | Out-Null

# ---- every dataset, in full ---------------------------------------------
$list = Grab "datasets_list" "$Root/api/lora/datasets"
$slugs = @()
if ($list) {
    foreach ($d in $list.datasets) {
        $full = Grab ("dataset__" + $d.id) ("$Root/api/lora/datasets/" + $d.id)
        if ($full) {
            $slugs += $full.char_slug
            # what a no-op re-plan WOULD do -- read-only, and it exposes any
            # drift between the stored plan and what the planner produces now
            $b = @{} | ConvertTo-Json
            Grab ("planpreview__" + $d.id) ("$Root/api/lora/datasets/" + $d.id + "/plan-preview") "POST" $b | Out-Null
        }
        Grab ("outfits__" + $d.id) ("$Root/api/lora/datasets/" + $d.id + "/outfits") | Out-Null
    }
}

# ---- every character referenced by a dataset ----------------------------
Grab "characters_list" "$Root/api/klein3/characters" | Out-Null
foreach ($s in ($slugs | Sort-Object -Unique)) {
    if ($s) { Grab ("character__" + $s) ("$Root/api/klein3/characters/" + $s) | Out-Null }
}

# ---- the recipe / constants the planner uses ----------------------------
Grab "lora_recipe" "$Root/api/lora/recipe" | Out-Null

# ---- logs, if any are sitting in the repo -------------------------------
$logs = Get-ChildItem -Path (Join-Path $PSScriptRoot "..") -Filter "*.log" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 3
foreach ($l in $logs) {
    $dest = Join-Path $out ("log__" + $l.Name + ".txt")
    Get-Content $l.FullName -Tail $LogLines | Set-Content -Path $dest -Encoding UTF8
    Write-Host ("  ok    log " + $l.Name + " (last " + $LogLines + " lines)")
}

# ---- environment --------------------------------------------------------
$env = [pscustomobject]@{
    version_file = (Get-Content (Join-Path $PSScriptRoot "..\VERSION") -ErrorAction SilentlyContinue)
    powershell   = $PSVersionTable.PSVersion.ToString()
    os           = [System.Environment]::OSVersion.VersionString
    collected_at = (Get-Date).ToString("s")
}
$env | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $out "environment.json") -Encoding UTF8
$manifest | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $out "_manifest.json") -Encoding UTF8

Write-Host ""
$okCount = @($manifest | Where-Object { $_.ok }).Count
Write-Host ("done: " + $okCount + " of " + $manifest.Count + " collected") -ForegroundColor Green
Write-Host ("written to: " + $out)
Write-Host ""
Write-Host "Tell Claude 'dump is ready' - he can read these directly, nothing to paste." -ForegroundColor Green
