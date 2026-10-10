param([switch]$BoundaryDiagnostic)
$ErrorActionPreference = 'Stop'
$outputDirectory = Join-Path $PSScriptRoot 'bin'
if ($BoundaryDiagnostic) { $outputDirectory = Join-Path $outputDirectory 'boundary' }
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$sourcePath = Join-Path $PSScriptRoot $(if ($BoundaryDiagnostic) { 'boundary-payload.cpp' } else { 'main.cpp' })
$binaryName = if ($BoundaryDiagnostic) { 'rg.exe' } else { 'research-sandbox.exe' }
$compiler = Get-Command cl.exe -ErrorAction SilentlyContinue
if ($compiler -and $env:INCLUDE -and $env:LIB) {
    Push-Location $outputDirectory
    try {
        & $compiler.Source /nologo /std:c++17 /EHsc /W4 /O2 /utf-8 /D_WIN32_WINNT=0x0601 $sourcePath "/Fe:$binaryName" /link advapi32.lib
        if ($LASTEXITCODE -ne 0) { throw 'Native helper compilation failed' }
    } finally { Pop-Location }
} else {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    if (!(Test-Path -LiteralPath $vswhere)) { throw 'Use a Visual Studio C++ SDK developer shell; no compiler is installed by this script.' }
    $installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (!$installation) { throw 'Visual Studio C++ Build Tools unavailable' }
    $vcvars = Join-Path $installation 'VC/Auxiliary/Build/vcvarsall.bat'
    # All interpolated paths are local script/compiler paths, never user query text.
    $command = 'call "{0}" x64 >nul && cl.exe /nologo /std:c++17 /EHsc /W4 /O2 /utf-8 /D_WIN32_WINNT=0x0601 "{1}" /Fe:{2} /link advapi32.lib' -f $vcvars, $sourcePath, $binaryName
    Push-Location $outputDirectory
    try {
        & cmd.exe /d /s /c $command
        if ($LASTEXITCODE -ne 0) { throw 'Native helper compilation failed' }
    } finally { Pop-Location }
}
Write-Output (Join-Path $outputDirectory $binaryName)
