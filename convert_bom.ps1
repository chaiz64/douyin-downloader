# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  convert_bom.ps1  ·  Stream Toolkit                                         ║
# ║  repository : https://github.com/chaiz64/NotebookLM.git                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
#
#  Converts PowerShell scripts (.ps1) in the workspace to UTF-8 with BOM.
#  This resolves "Unexpected token" and Unicode rendering errors in PowerShell.

$ErrorActionPreference = "Stop"

# Get all .ps1 files in the current folder (excluding this script itself)
$files = Get-ChildItem -Path $PSScriptRoot -Filter *.ps1 -File | Where-Object { $_.Name -ne "convert_bom.ps1" }

if ($files.Count -eq 0) {
    Write-Host "No PowerShell script files (.ps1) found." -ForegroundColor Yellow
    Exit
}

Write-Host "Converting $($files.Count) file(s) to UTF-8 with BOM..." -ForegroundColor Cyan

foreach ($file in $files) {
    try {
        # Read file contents using UTF-8 (auto-detecting BOM if present)
        $utf8NoBOM = New-Object System.Text.UTF8Encoding($false)
        $reader = New-Object System.IO.StreamReader($file.FullName, $utf8NoBOM)
        $content = $reader.ReadToEnd()
        $reader.Close()
        
        # Write back using UTF-8 with BOM (default UTF8Encoding constructor)
        $utf8WithBOM = New-Object System.Text.UTF8Encoding($true)
        $writer = New-Object System.IO.StreamWriter($file.FullName, $false, $utf8WithBOM)
        $writer.Write($content)
        $writer.Close()
        
        Write-Host "  [✓] Converted: $($file.Name)" -ForegroundColor Green
    }
    catch {
        Write-Host "  [✗] Failed to convert $($file.Name): $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host "Done!" -ForegroundColor Cyan
