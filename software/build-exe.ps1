# Requires: PyInstaller installed in .venv, running on Windows
$ErrorActionPreference = "Stop"

$AppName = "MYSTAT"
$Version = (Select-String -Path "version.py" -Pattern "__version__\s*=\s*['\`"]([^'\`"]+)['\`"]").Matches[0].Groups[1].Value
$Icon = "icon\icon.ico"   # Windows needs .ico, not .png

Write-Host "Building $AppName version $Version..."

# Clean previous builds
Remove-Item -Recurse -Force "build", "dist", "$AppName.spec" -ErrorAction SilentlyContinue

# Build with PyInstaller
.venv\Scripts\pyinstaller.exe --windowed --noconsole --name $AppName --icon $Icon --onefile mystat.py

# Rename to include version
$FinalName = "$AppName-$Version-x64.exe"
Rename-Item -Path "dist\$AppName.exe" -NewName $FinalName

Write-Host "Done"