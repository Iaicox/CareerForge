# Generates templates/reference_cv.docx and templates/reference_cover.docx
# from pandoc's default reference.docx, restyled to match templates/CV_template.docx
# (Roboto/Roboto Light, A4, gray secondary text, right-aligned tab stops).
#
# Run after changing style constants below, or to regenerate lost reference files:
#   powershell -File tools\make_reference.ps1
#
# Quick manual tweaks are also fine: open the reference .docx in Word, modify a
# style (right-click style > Modify), save. Pandoc reads styles by name.

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

$repo = Split-Path $PSScriptRoot -Parent
$templates = Join-Path $repo 'templates'
$work = Join-Path $env:TEMP ("make_reference_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $templates | Out-Null

function New-Reference {
    param(
        [string]$OutFile,
        [int]$MarginTwips,     # page margins, all sides
        [string]$BodySpacing   # w:before/w:after for Body Text paragraphs
    )

    # A4 content width in twips = 11906 - 2*margin; right tab stop sits at the right margin
    $tabPos = 11906 - 2 * $MarginTwips

    $dir = Join-Path $work ([IO.Path]::GetFileNameWithoutExtension($OutFile))
    New-Item -ItemType Directory -Force $dir | Out-Null
    $default = Join-Path $work 'ref_default.docx'
    if (-not (Test-Path $default)) {
        pandoc -o $default --print-default-data-file reference.docx
        if ($LASTEXITCODE -ne 0) { throw 'pandoc failed to produce default reference.docx' }
    }
    [System.IO.Compression.ZipFile]::ExtractToDirectory($default, $dir)

    # ---- styles.xml: replace whole style blocks by styleId ----
    $stylesPath = Join-Path $dir 'word\styles.xml'
    $s = Get-Content $stylesPath -Raw

    function Replace-Style([string]$xml, [string]$id, [string]$new) {
        $pattern = '<w:style [^>]*w:styleId="' + $id + '".*?</w:style>'
        $m = [regex]::Match($xml, $pattern, 'Singleline')
        if (-not $m.Success) { throw "style $id not found in default reference" }
        return $xml.Substring(0, $m.Index) + $new + $xml.Substring($m.Index + $m.Length)
    }

    $s = Replace-Style $s 'Normal' @"
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal" /><w:qFormat /><w:pPr><w:spacing w:before="0" w:after="0" w:line="276" w:lineRule="auto" /></w:pPr><w:rPr><w:rFonts w:ascii="Roboto Light" w:hAnsi="Roboto Light" w:cs="Roboto Light" /><w:sz w:val="20" /><w:szCs w:val="20" /></w:rPr></w:style>
"@

    $s = Replace-Style $s 'BodyText' @"
<w:style w:type="paragraph" w:styleId="BodyText"><w:name w:val="Body Text" /><w:basedOn w:val="Normal" /><w:link w:val="BodyTextChar" /><w:qFormat /><w:pPr><w:spacing $BodySpacing /></w:pPr></w:style>
"@

    $s = Replace-Style $s 'Compact' @"
<w:style w:type="paragraph" w:customStyle="1" w:styleId="Compact"><w:name w:val="Compact" /><w:basedOn w:val="BodyText" /><w:qFormat /><w:pPr><w:spacing w:before="0" w:after="0" w:line="276" w:lineRule="auto" /></w:pPr></w:style>
"@

    $s = Replace-Style $s 'Title' @"
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title" /><w:basedOn w:val="Normal" /><w:next w:val="BodyText" /><w:link w:val="TitleChar" /><w:uiPriority w:val="10" /><w:qFormat /><w:pPr><w:spacing w:before="0" w:after="80" w:line="240" w:lineRule="auto" /><w:jc w:val="center" /></w:pPr><w:rPr><w:rFonts w:ascii="Roboto" w:hAnsi="Roboto" w:cs="Roboto" /><w:sz w:val="32" /><w:szCs w:val="32" /></w:rPr></w:style>
"@

    $s = Replace-Style $s 'Subtitle' @"
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle" /><w:basedOn w:val="Normal" /><w:next w:val="BodyText" /><w:link w:val="SubtitleChar" /><w:uiPriority w:val="11" /><w:qFormat /><w:pPr><w:spacing w:before="0" w:after="80" w:line="240" w:lineRule="auto" /><w:jc w:val="center" /></w:pPr><w:rPr><w:rFonts w:ascii="Roboto Light" w:hAnsi="Roboto Light" w:cs="Roboto Light" /><w:sz w:val="24" /><w:szCs w:val="24" /></w:rPr></w:style>
"@

    $s = Replace-Style $s 'Heading1' @"
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1" /><w:basedOn w:val="Normal" /><w:next w:val="BodyText" /><w:link w:val="Heading1Char" /><w:uiPriority w:val="9" /><w:qFormat /><w:pPr><w:keepNext /><w:keepLines /><w:spacing w:before="200" w:after="20" w:line="240" w:lineRule="auto" /><w:outlineLvl w:val="0" /></w:pPr><w:rPr><w:rFonts w:ascii="Roboto" w:hAnsi="Roboto" w:cs="Roboto" /><w:color w:val="000000" /><w:sz w:val="26" /><w:szCs w:val="26" /></w:rPr></w:style>
"@

    $s = Replace-Style $s 'Heading2' @"
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2" /><w:basedOn w:val="Normal" /><w:next w:val="BodyText" /><w:link w:val="Heading2Char" /><w:uiPriority w:val="9" /><w:qFormat /><w:pPr><w:keepNext /><w:keepLines /><w:spacing w:before="120" w:after="20" w:line="240" w:lineRule="auto" /><w:outlineLvl w:val="1" /></w:pPr><w:rPr><w:rFonts w:ascii="Roboto" w:hAnsi="Roboto" w:cs="Roboto" /><w:color w:val="000000" /><w:sz w:val="22" /><w:szCs w:val="22" /></w:rPr></w:style>
"@

    $s = Replace-Style $s 'Hyperlink' @"
<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink" /><w:basedOn w:val="BodyTextChar" /><w:rPr><w:color w:val="1155CC" /><w:u w:val="single" /></w:rPr></w:style>
"@

    # ---- custom styles used by the markdown masters (custom-style divs/spans) ----
    $custom = @"
<w:style w:type="paragraph" w:customStyle="1" w:styleId="Contact"><w:name w:val="Contact" /><w:basedOn w:val="Normal" /><w:next w:val="BodyText" /><w:qFormat /><w:pPr><w:spacing w:before="0" w:after="200" w:line="240" w:lineRule="auto" /><w:jc w:val="center" /></w:pPr></w:style>
<w:style w:type="paragraph" w:customStyle="1" w:styleId="CompanyLine"><w:name w:val="CompanyLine" /><w:basedOn w:val="Normal" /><w:next w:val="RoleLine" /><w:qFormat /><w:pPr><w:keepNext /><w:spacing w:before="200" w:after="0" w:line="276" w:lineRule="auto" /><w:tabs><w:tab w:val="right" w:leader="none" w:pos="$tabPos" /></w:tabs></w:pPr></w:style>
<w:style w:type="paragraph" w:customStyle="1" w:styleId="RoleLine"><w:name w:val="RoleLine" /><w:basedOn w:val="Normal" /><w:next w:val="BodyText" /><w:qFormat /><w:pPr><w:keepNext /><w:spacing w:before="40" w:after="40" w:line="276" w:lineRule="auto" /><w:tabs><w:tab w:val="right" w:leader="none" w:pos="$tabPos" /></w:tabs></w:pPr><w:rPr><w:rFonts w:ascii="Roboto" w:hAnsi="Roboto" w:cs="Roboto" /><w:sz w:val="22" /><w:szCs w:val="22" /></w:rPr></w:style>
<w:style w:type="paragraph" w:customStyle="1" w:styleId="TabLine"><w:name w:val="TabLine" /><w:basedOn w:val="Normal" /><w:qFormat /><w:pPr><w:spacing w:before="80" w:after="0" w:line="276" w:lineRule="auto" /><w:tabs><w:tab w:val="right" w:leader="none" w:pos="$tabPos" /></w:tabs></w:pPr></w:style>
<w:style w:type="paragraph" w:customStyle="1" w:styleId="StackLine"><w:name w:val="StackLine" /><w:basedOn w:val="Normal" /><w:qFormat /><w:pPr><w:keepNext /><w:spacing w:before="20" w:after="40" w:line="276" w:lineRule="auto" /></w:pPr><w:rPr><w:rFonts w:ascii="Roboto Light" w:hAnsi="Roboto Light" w:cs="Roboto Light" /><w:color w:val="999999" /><w:sz w:val="20" /><w:szCs w:val="20" /></w:rPr></w:style>
<w:style w:type="character" w:customStyle="1" w:styleId="Muted"><w:name w:val="Muted" /><w:qFormat /><w:rPr><w:rFonts w:ascii="Roboto Light" w:hAnsi="Roboto Light" w:cs="Roboto Light" /><w:color w:val="999999" /><w:sz w:val="20" /><w:szCs w:val="20" /></w:rPr></w:style>
"@
    $s = $s -replace '</w:styles>', ($custom + '</w:styles>')
    Set-Content $stylesPath $s -Encoding UTF8 -NoNewline

    # ---- document.xml: A4 page size and margins in sectPr ----
    $docPath = Join-Path $dir 'word\document.xml'
    $d = Get-Content $docPath -Raw
    $sect = '<w:sectPr><w:footnotePr><w:numRestart w:val="eachSect" /></w:footnotePr>' +
            '<w:pgSz w:h="16838" w:w="11906" w:orient="portrait" />' +
            "<w:pgMar w:top=`"$MarginTwips`" w:right=`"$MarginTwips`" w:bottom=`"$MarginTwips`" w:left=`"$MarginTwips`" w:header=`"720`" w:footer=`"720`" />" +
            '</w:sectPr>'
    $d = [regex]::Replace($d, '<w:sectPr>.*?</w:sectPr>', $sect, 'Singleline')
    Set-Content $docPath $d -Encoding UTF8 -NoNewline

    # ---- repack ----
    $out = Join-Path $templates $OutFile
    if (Test-Path $out) { Remove-Item $out -Force }
    [System.IO.Compression.ZipFile]::CreateFromDirectory($dir, $out)
    Write-Output "created $out"
}

try {
    # CV: 0.5" margins like CV_template.docx
    New-Reference -OutFile 'reference_cv.docx' -MarginTwips 720 -BodySpacing 'w:before="40" w:after="80" w:line="276" w:lineRule="auto"'
    # Cover letter: 2cm margins, roomier paragraph spacing
    New-Reference -OutFile 'reference_cover.docx' -MarginTwips 1134 -BodySpacing 'w:before="0" w:after="160" w:line="276" w:lineRule="auto"'
}
finally {
    if (Test-Path $work) { Remove-Item $work -Recurse -Force }
}
