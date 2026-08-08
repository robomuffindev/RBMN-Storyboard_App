# SUPERSEDED by scripts\repair.ps1 as of v1.248.
#
# This script re-rendered until the ANGLE was right and checked nothing else.
# Measured on redv1: of the three rows it repaired, TWO came back scoring 0.19
# and 0.21 on ArcFace - below the "different person" floor. It fixed the angle
# and broke the face, and nothing noticed until a separate likeness run.
#
# scripts\repair.ps1 re-measures angle, shot type, crop AND identity every round.

param([string]$Id = "", [int]$Rounds = 3, [switch]$DryRun, [switch]$ListDatasets,
      [string]$BaseUrl = "http://127.0.0.1:8899/api/lora")

Write-Host "repair_angles.ps1 is superseded - it only checked the angle, and that" -ForegroundColor Yellow
Write-Host "let a repaired row come back as a different person without anything noticing." -ForegroundColor Yellow
Write-Host ""
Write-Host "Use this instead:" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\repair.ps1" -ForegroundColor Cyan
exit 1
