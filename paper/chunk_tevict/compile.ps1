# Build paper/chunk_tevict/main.pdf
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "[1/4] pdflatex (pass 1)..."
pdflatex -interaction=nonstopmode main.tex
if ($LASTEXITCODE -ne 0) { Write-Host "pdflatex pass 1 had errors (see main.log)" }

Write-Host "[2/4] bibtex..."
bibtex main
if ($LASTEXITCODE -ne 0) { Write-Host "bibtex had errors (see main.blg)" }

Write-Host "[3/4] pdflatex (pass 2)..."
pdflatex -interaction=nonstopmode main.tex | Out-Null

Write-Host "[4/4] pdflatex (pass 3)..."
pdflatex -interaction=nonstopmode main.tex | Out-Null

if (Test-Path "main.pdf") {
    $size = (Get-Item "main.pdf").Length
    Write-Host "OK: main.pdf ($([math]::Round($size/1KB,1)) KB)"
} else {
    Write-Error "main.pdf not produced; check main.log"
    exit 1
}
