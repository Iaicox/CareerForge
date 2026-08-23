# Builds application documents: markdown -> docx (pandoc) -> pdf (MS Word or LibreOffice).
#
# Usage:
#   tools\build.ps1 -Path applications\acme                 # build cv_*.md + cover*.md in the folder
#   tools\build.ps1 -Path processing\acme                   # same; works in any stage folder
#   tools\build.ps1 -Path applications\acme\cv_jane_doe.md  # one document
#   tools\build.ps1 -Path applications\acme -Force          # rebuild docx even if hand-edited
#   tools\build.ps1 -Path applications\acme -PdfOnly        # only docx -> pdf (keep manual edits)
#   tools\build.ps1 -Path applications\acme -Engine libreoffice
#
# Rules:
#   - Folder mode builds only cv_*.md and cover*.md; other markdown (job.md,
#     interview prep, notes) is skipped. Build extras by passing the file path.
#   - cv_*.md      uses templates\reference_cv.docx
#   - cover_*.md   uses templates\reference_cover.docx
#   - Page limits come from config/config.toml ([documents]), not from this script.
#   - If the .docx is newer than the .md (edited by hand in Word), the md->docx step
#     is skipped to protect manual edits; only the PDF is refreshed. Use -Force to override.
#   - Engine 'auto' prefers MS Word (exact page counts) and falls back to LibreOffice.

param(
    [Parameter(Mandatory = $true)][string]$Path,
    [switch]$Force,
    [switch]$PdfOnly,
    [ValidateSet('auto', 'word', 'libreoffice')][string]$Engine = ''
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent

# pandoc may have been installed by winget in this session; make sure it's on PATH
if (-not (Get-Command pandoc -ErrorAction SilentlyContinue)) {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
if (-not (Get-Command pandoc -ErrorAction SilentlyContinue)) {
    throw 'pandoc not found. Install it with: winget install JohnMacFarlane.Pandoc'
}

# ---------------------------------------------------------------------------
# Settings (single source of truth: config/config.toml)
# ---------------------------------------------------------------------------
$docSettings = $null
try {
    $docSettings = & python (Join-Path $PSScriptRoot 'config_get.py') 'documents' | ConvertFrom-Json
} catch {
    Write-Warning 'could not read config/config.toml; falling back to 2-page CV / 1-page letter'
}
$cvLimit = if ($docSettings) { [int]$docSettings.cv_max_pages } else { 2 }
$coverLimit = if ($docSettings) { [int]$docSettings.cover_max_pages } else { 1 }
if (-not $Engine) {
    $Engine = if ($docSettings -and $docSettings.engine) { $docSettings.engine } else { 'auto' }
}

# ---------------------------------------------------------------------------
# Fonts: install per-user on first run; Word substitutes silently otherwise
# ---------------------------------------------------------------------------
$fontReg = 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts'
$probe = (Get-ItemProperty $fontReg -ErrorAction SilentlyContinue).'Roboto Light (TrueType)'
if (-not $probe -or -not (Test-Path $probe)) {
    Write-Output 'Roboto fonts missing - installing from templates\fonts ...'
    $userFontDir = "$env:LOCALAPPDATA\Microsoft\Windows\Fonts"
    New-Item -ItemType Directory -Force $userFontDir | Out-Null
    if (-not (Test-Path $fontReg)) { New-Item -Path $fontReg -Force | Out-Null }
    $names = @{
        'Roboto-regular.ttf'          = 'Roboto (TrueType)'
        'Roboto-bold.ttf'             = 'Roboto Bold (TrueType)'
        'Roboto-italic.ttf'           = 'Roboto Italic (TrueType)'
        'Roboto-boldItalic.ttf'       = 'Roboto Bold Italic (TrueType)'
        'RobotoLight-regular.ttf'     = 'Roboto Light (TrueType)'
        'RobotoLight-bold.ttf'        = 'Roboto Light Bold (TrueType)'
        'RobotoLight-italic.ttf'      = 'Roboto Light Italic (TrueType)'
        'RobotoLight-boldItalic.ttf'  = 'Roboto Light Bold Italic (TrueType)'
    }
    foreach ($file in $names.Keys) {
        $src = Join-Path $repo "templates\fonts\$file"
        if (-not (Test-Path $src)) { continue }
        $dest = Join-Path $userFontDir $file
        Copy-Item $src $dest -Force
        New-ItemProperty -Path $fontReg -Name $names[$file] -Value $dest -PropertyType String -Force | Out-Null
    }
}

# ---------------------------------------------------------------------------
# Pick the PDF engine
# ---------------------------------------------------------------------------
function Find-Soffice {
    $cmd = Get-Command soffice -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
            "$env:ProgramFiles\LibreOffice\program\soffice.exe",
            "${env:ProgramFiles(x86)}\LibreOffice\program\soffice.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$soffice = Find-Soffice
$useWord = $false
if ($Engine -eq 'word') {
    $useWord = $true
} elseif ($Engine -eq 'libreoffice') {
    if (-not $soffice) { throw 'LibreOffice requested but soffice was not found. Install it, or use -Engine word.' }
} else {
    # auto: try Word, accept LibreOffice if Word is not scriptable
    try {
        $probeWord = New-Object -ComObject Word.Application
        $probeWord.Quit()
        [Runtime.InteropServices.Marshal]::ReleaseComObject($probeWord) | Out-Null
        $useWord = $true
    } catch {
        if (-not $soffice) {
            throw 'No PDF engine available: MS Word is not scriptable and LibreOffice was not found.'
        }
        Write-Output 'MS Word not available - using LibreOffice.'
    }
}

# ---------------------------------------------------------------------------
# Collect markdown sources
# ---------------------------------------------------------------------------
$target = Resolve-Path $Path
if (Test-Path $target -PathType Container) {
    $mdFiles = Get-ChildItem $target -Filter '*.md' | Where-Object { $_.Name -like 'cv_*' -or $_.Name -like 'cover*' }
} else {
    $mdFiles = @(Get-Item $target)
}
if (-not $mdFiles) { throw "no cv_*.md or cover*.md documents found in $target (pass a file path to build other markdown)" }

$results = @()
$word = $null
try {
    foreach ($md in $mdFiles) {
        $isCover = $md.BaseName -like 'cover*'
        $ref = if ($isCover) { Join-Path $repo 'templates\reference_cover.docx' } else { Join-Path $repo 'templates\reference_cv.docx' }
        $limit = if ($isCover) { $coverLimit } else { $cvLimit }
        $docx = [IO.Path]::ChangeExtension($md.FullName, '.docx')
        $pdf = [IO.Path]::ChangeExtension($md.FullName, '.pdf')

        # md -> docx
        $docxExists = Test-Path $docx
        $handEdited = $docxExists -and ((Get-Item $docx).LastWriteTime -gt $md.LastWriteTime)
        if ($PdfOnly -and -not $docxExists) { throw "-PdfOnly: $docx does not exist" }
        if (-not $PdfOnly -and (-not $docxExists -or $Force -or -not $handEdited)) {
            pandoc $md.FullName -o $docx --reference-doc $ref
            if ($LASTEXITCODE -ne 0) { throw "pandoc failed on $($md.Name)" }
            $step = 'md->docx->pdf'
        } elseif ($handEdited -and -not $Force) {
            $step = 'docx->pdf (docx newer than md - manual edits kept, use -Force to rebuild)'
        } else {
            $step = 'docx->pdf'
        }

        # docx -> pdf
        if ($useWord) {
            if ($null -eq $word) {
                $word = New-Object -ComObject Word.Application
                $word.Visible = $false
                $word.DisplayAlerts = 0
            }
            $doc = $word.Documents.Open($docx, $false, $true)   # no conversion dialog, read-only
            $doc.SaveAs2($pdf, 17)                              # 17 = wdFormatPDF
            $pages = $doc.ComputeStatistics(2)                  # 2 = wdStatisticPages
            $doc.Close(0)
            $engineUsed = 'word'
        } else {
            $outDir = Split-Path $docx -Parent
            & $soffice --headless --norestore --convert-to pdf --outdir $outDir $docx | Out-Null
            if (-not (Test-Path $pdf)) { throw "LibreOffice did not produce $pdf" }
            $pages = [int](& python (Join-Path $PSScriptRoot 'pagecount.py') $pdf)
            $engineUsed = 'libreoffice'
        }

        $ok = if ($pages -le $limit) { 'OK' } else { "OVER LIMIT ($limit)" }
        $results += [pscustomobject]@{
            Document = $md.Name; Steps = $step; Engine = $engineUsed
            Pages = $pages; Limit = $limit; Status = $ok
        }
    }
}
finally {
    if ($word) { $word.Quit(); [Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null }
}

$results | Format-Table -AutoSize
if ($results | Where-Object { $_.Pages -gt $_.Limit }) { exit 2 }
