#!/usr/bin/env bash
# Builds application documents on macOS / Linux: markdown -> docx (pandoc) -> pdf (LibreOffice).
#
# Usage:
#   tools/build.sh applications/acme                  # build cv_*.md + cover*.md in the folder
#   tools/build.sh applications/acme/cv_jane_doe.md   # one document
#   tools/build.sh applications/acme --force          # rebuild docx even if hand-edited
#   tools/build.sh applications/acme --pdf-only       # only docx -> pdf (keep manual edits)
#
# The Windows counterpart is tools/build.ps1, which prefers MS Word. Both read
# their page limits from config/config.toml, so they cannot drift apart.
#
# Install the fonts in templates/fonts/ into your user font directory once if
# documents render with substitutes (macOS: Font Book; Linux: cp to ~/.local/share/fonts && fc-cache).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"

TARGET=""
FORCE=0
PDF_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --force)    FORCE=1 ;;
    --pdf-only) PDF_ONLY=1 ;;
    -*)         echo "unknown option: $arg" >&2; exit 2 ;;
    *)          TARGET="$arg" ;;
  esac
done
[ -n "$TARGET" ] || { sed -n '2,12p' "${BASH_SOURCE[0]}" >&2; exit 2; }
[ -e "$TARGET" ] || { echo "error: no such path: $TARGET" >&2; exit 1; }

command -v pandoc >/dev/null || {
  echo "error: pandoc not found (brew install pandoc / apt install pandoc)" >&2; exit 1; }

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || PYTHON=python

SOFFICE=""
for c in soffice libreoffice /Applications/LibreOffice.app/Contents/MacOS/soffice; do
  if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then SOFFICE="$c"; break; fi
done
[ -n "$SOFFICE" ] || {
  echo "error: LibreOffice not found (brew install --cask libreoffice / apt install libreoffice)" >&2
  exit 1; }

# Page limits: config/config.toml is the single source of truth.
DOCCFG="$("$PYTHON" "$SCRIPT_DIR/config_get.py" documents 2>/dev/null || echo '{}')"
read_limit() {
  "$PYTHON" -c "import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2], sys.argv[3]))" \
    "$DOCCFG" "$1" "$2"
}
CV_LIMIT="$(read_limit cv_max_pages 2)"
COVER_LIMIT="$(read_limit cover_max_pages 1)"

FILES=()
if [ -d "$TARGET" ]; then
  # Folder mode: only the documents the pipeline owns.
  #
  # Read in a loop rather than with mapfile: mapfile is a bash 4 builtin and
  # macOS ships bash 3.2, where this is the documented way to run the script.
  while IFS= read -r f; do
    FILES+=("$f")
  done < <(find "$TARGET" -maxdepth 1 -type f \
    \( -name 'cv_*.md' -o -name 'cover*.md' \) | sort)
else
  FILES=("$TARGET")
fi
[ "${#FILES[@]}" -gt 0 ] || {
  echo "error: no cv_*.md or cover*.md in $TARGET (pass a file path to build other markdown)" >&2
  exit 1; }

printf '%-34s %-26s %6s %6s  %-11s %s\n' Document Steps Pages Limit Status ATS
over=0
ats_findings=""

for md in "${FILES[@]}"; do
  base="$(basename "$md")"
  docx="${md%.md}.docx"
  pdf="${md%.md}.pdf"
  case "$base" in
    cover*) ref="$REPO/templates/reference_cover.docx"; limit="$COVER_LIMIT" ;;
    *)      ref="$REPO/templates/reference_cv.docx";    limit="$CV_LIMIT" ;;
  esac

  steps="docx->pdf"
  hand_edited=0
  [ -f "$docx" ] && [ "$docx" -nt "$md" ] && hand_edited=1

  if [ "$PDF_ONLY" -eq 1 ]; then
    [ -f "$docx" ] || { echo "error: --pdf-only but $docx does not exist" >&2; exit 1; }
  elif [ ! -f "$docx" ] || [ "$FORCE" -eq 1 ] || [ "$hand_edited" -eq 0 ]; then
    pandoc "$md" -o "$docx" --reference-doc "$ref"
    steps="md->docx->pdf"
  else
    steps="docx->pdf (manual edits kept)"
  fi

  "$SOFFICE" --headless --norestore --convert-to pdf \
             --outdir "$(dirname "$docx")" "$docx" >/dev/null
  [ -f "$pdf" ] || { echo "error: LibreOffice did not produce $pdf" >&2; exit 1; }

  pages="$("$PYTHON" "$SCRIPT_DIR/pagecount.py" "$pdf")"
  if [ "$pages" -le "$limit" ]; then status="OK"; else status="OVER LIMIT"; over=1; fi

  # ATS text layer: a PDF can look perfect and extract as mojibake, which stays
  # invisible until an employer's parser reads nothing.
  ats_output="$("$PYTHON" "$SCRIPT_DIR/atscheck.py" "$pdf" --source "$md" 2>&1)" && ats="OK" || {
    case $? in
      3) ats="no extractor" ;;
      *) ats="see below"; ats_findings="${ats_findings}${ats_output}"$'\n' ;;
    esac
  }
  printf '%-34s %-26s %6s %6s  %-11s %s\n' "$base" "$steps" "$pages" "$limit" "$status" "$ats"
done

if [ -n "${ats_findings:-}" ]; then
  echo
  echo "ATS findings (warnings - the documents still built):"
  printf '%s' "$ats_findings" | sed 's/^/  /'
fi

exit $(( over ? 2 : 0 ))
