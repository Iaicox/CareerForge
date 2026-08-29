# Builds application documents: markdown -> docx (pandoc) -> pdf (MS Word or LibreOffice).
#
# Usage:
#   tools\build.ps1 -Path data\pipeline\applications\acme                 # build cv_*.md + cover*.md in the folder
#   tools\build.ps1 -Path data\pipeline\processing\acme                   # same; works in any stage folder
#   tools\build.ps1 -Path data\pipeline\applications\acme\cv_jane_doe.md  # one document
#   tools\build.ps1 -Path data\pipeline\applications\acme -Force          # rebuild docx even if hand-edited
#   tools\build.ps1 -Path data\pipeline\applications\acme -PdfOnly        # only docx -> pdf (keep manual edits)
#   tools\build.ps1 -Path data\pipeline\applications\acme -Engine libreoffice
#
# Rules:
#   - Folder mode builds only cv_*.md and cover*.md; other markdown (job.md,
#     interview prep, notes) is skipped. Build extras by passing the file path.
#   - cv_*.md      uses templates\reference_cv.docx
#   - cover_*.md   uses templates\reference_cover.docx
#   - Page limits come from data/config/config.toml ([documents]), not from this script.
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
# Settings (single source of truth: data/config/config.toml)
# ---------------------------------------------------------------------------
$docSettings = $null
try {
    $docSettings = & python (Join-Path $PSScriptRoot 'config_get.py') 'documents' | ConvertFrom-Json
} catch {
    Write-Warning 'could not read data/config/config.toml; falling back to 2-page CV / 1-page letter'
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
$atsDetails = @()
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

        # ATS text layer: a PDF can look perfect and extract as mojibake, which
        # is invisible until an employer's parser reads nothing.
        #
        # 2>&1 makes Windows PowerShell wrap every stderr line in an ErrorRecord,
        # which $ErrorActionPreference = 'Stop' then treats as terminating. There
        # is no catch around this block, so the one step that is only ever meant
        # to warn was aborting the whole build -- and atscheck writes to stderr in
        # exactly the case reported below as 'no extractor'. Setting the
        # preference inside the scriptblock scopes it to this one call.
        #
        # It also demotes a missing python to non-terminating, and a command that
        # never ran leaves $LASTEXITCODE at whatever pandoc last set it to -- 0.
        # So the table said 'ATS: OK' about a check that had not happened.
        # Clearing it first makes "did not run" a state of its own, and the catch
        # keeps the CommandNotFoundException out of the console and in $atsOutput
        # with everything else this step has to say.
        $atsOutput = & {
            $ErrorActionPreference = 'Continue'
            $global:LASTEXITCODE = $null
            try {
                & python (Join-Path $PSScriptRoot 'atscheck.py') $pdf --source $md.FullName 2>&1
            } catch { $_ }
        }
        $atsCode = $LASTEXITCODE
        if ($null -eq $atsCode) {
            $ats = 'not run'
            $atsDetails += "$($md.Name): ATS check did not run - is python on PATH?"
            $atsDetails += $atsOutput
        } else {
            $ats = switch ($atsCode) {
                0 { 'OK' }
                3 { 'no extractor' }
                default { 'see below' }
            }
            # Anything that says 'see below' has to actually appear below.
            if ($atsCode -ne 0 -and $atsCode -ne 3) { $atsDetails += $atsOutput }
        }

        $ok = if ($pages -le $limit) { 'OK' } else { "OVER LIMIT ($limit)" }
        $results += [pscustomobject]@{
            Document = $md.Name; Steps = $step; Engine = $engineUsed
            Pages = $pages; Limit = $limit; Status = $ok; ATS = $ats
        }
    }
}
finally {
    if ($word) { $word.Quit(); [Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null }
}

$results | Format-Table -AutoSize
if ($atsDetails) {
    Write-Output 'ATS findings (warnings - the document still built):'
    $atsDetails | ForEach-Object { Write-Output "  $_" }
}
# Exit 2 means one thing: a document is over its page limit. Falling off the end
# instead of exiting returned whatever $LASTEXITCODE happened to hold, and the
# last thing to set it is atscheck -- which exits 2 on a finding. So a document
# that built fine and merely failed the ATS text check reported the same code as
# one that is too long, and the answer to that is to cut content. build.sh has
# always ended `exit $(( over ? 2 : 0 ))`; this is the same contract.
if ($results | Where-Object { $_.Pages -gt $_.Limit }) { exit 2 }
exit 0
