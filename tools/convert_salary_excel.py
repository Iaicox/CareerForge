#!/usr/bin/env python3
"""
Convert salary data from Excel to JSON format.

This script converts an Excel file containing company salary data
into the JSON format expected by tools/salary_lookup.py.

Prerequisites:
    pip install openpyxl

Usage:
    python tools/convert_salary_excel.py <path-to-excel-file>
    python tools/convert_salary_excel.py <path-to-excel-file> --source "My Union Stats 2025"
    python tools/convert_salary_excel.py <path-to-excel-file> --baseline 100 --baseline-desc "Index 100 = median salary"

The output file is written to data/profile/salary_data.json, alongside the rest of
your own data.

Expected Excel format:
    - A header row with column names
    - A "Company" or "Firma" column (required)
    - An optional "City" or "By" column
    - Any number of numeric data columns (salary index, count, etc.)

The script auto-detects the header row and column layout. For Excel files
with paired count/index columns per category, it groups them automatically.
"""

import json
import re
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402


# Column name patterns for auto-detection
COMPANY_PATTERNS = {"firma", "company", "virksomhed", "employer", "arbejdsgiver"}
CITY_PATTERNS = {"by", "city", "kommune", "location", "lokation", "sted"}
COUNT_PATTERNS = {"antal", "count", "number", "n", "employees", "medarbejdere"}
INDEX_PATTERNS = {"indeks", "index", "idx", "salary", "løn", "median", "average", "gennemsnit"}


# A marker this short is only trusted as a whole word: "n" is a real count
# header on its own, but as a substring it is in "Indeks", "Median" and
# "Engineering" alike, which classified every column as a count and left the
# count/index pairing below unreachable.
MIN_SUBSTRING_MARKER = 3


def header_words(header):
    """The header split into lowercase words, keeping Danish letters intact."""
    return re.findall(r"[^\W_]+", (header or "").lower(), flags=re.UNICODE)


def marker_in(word):
    """Where a count/index marker sits inside one word, if it does at all.

    Returns (end position, kind) for the marker ending last, or None. Danish and
    German weld these into one word -- "Lønindeks", "Medarbejderantal" -- and
    those compounds are head-final, so the last marker is the one that says what
    the column holds.
    """
    hits = [
        (word.rindex(p) + len(p), kind)
        for kind, patterns in (("count", COUNT_PATTERNS), ("index", INDEX_PATTERNS))
        for p in patterns
        if len(p) >= MIN_SUBSTRING_MARKER and p in word
    ]
    return max(hits) if hits else None


def detect_column_type(header):
    """Detect whether a column header refers to count or index data.

    A whole word decides outright; only then are substrings considered, and only
    for the longer markers. Whole words alone read "Lønindeks" and
    "Medarbejderantal" as neither, and an unclassified column is stored below as
    a salary index -- so a headcount came out the far end of salary_lookup as a
    benchmark figure.
    """
    words = header_words(header)
    if set(words) & COUNT_PATTERNS:
        return "count"
    if set(words) & INDEX_PATTERNS:
        return "index"
    hits = [m for m in (marker_in(w) for w in words) if m]
    return hits[-1][1] if hits else None


def category_name(header, patterns):
    """The header with its count/index marker words removed.

    Only the words that actually matched are dropped, so "Antal Engineering"
    becomes "engineering". Removing the patterns as substrings instead took the
    letters with them -- "atal egieerig" -- and did it in set-iteration order,
    so the result was not even stable between runs. A word that merely contains
    a marker goes too, or the compound keeps it: "lønindeks_engineering".
    """
    return "_".join(
        w for w in header_words(header)
        if w not in patterns
        and not any(len(p) >= MIN_SUBSTRING_MARKER and p in w for p in patterns)
    )


def parse_sheet(ws):
    """Parse a single worksheet into a list of company entries and detected categories."""
    # Find header row
    header_row = None
    for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=False), start=1):
        for cell in row:
            if cell.value and str(cell.value).strip().lower() in COMPANY_PATTERNS:
                header_row = row_idx
                break
        if header_row:
            break

    if header_row is None:
        print(f"Warning: Could not find header row in sheet '{ws.title}'. Skipping.", file=sys.stderr)
        return []

    # Read headers
    headers = []
    for cell in ws[header_row]:
        headers.append(str(cell.value).strip() if cell.value else "")

    # Find company and city columns
    company_col = None
    city_col = None
    for i, h in enumerate(headers):
        h_lower = h.lower()
        if h_lower in COMPANY_PATTERNS:
            company_col = i
        elif h_lower in CITY_PATTERNS:
            city_col = i

    if company_col is None:
        print(f"Warning: Could not find company column in sheet '{ws.title}'.", file=sys.stderr)
        return []

    # Identify data columns (everything that's not company/city)
    data_cols = []
    for i, h in enumerate(headers):
        if i == company_col or i == city_col or not h:
            continue
        data_cols.append((i, h))

    # Try to detect paired count/index columns per category
    # Heuristic: if columns come in pairs and alternate count/index, group them
    categories = []
    unclassified = []
    i = 0
    while i < len(data_cols):
        col_idx, col_header = data_cols[i]
        col_type = detect_column_type(col_header)

        if i + 1 < len(data_cols):
            next_col_idx, next_col_header = data_cols[i + 1]
            next_col_type = detect_column_type(next_col_header)

            # If we have a count/index pair, group them
            if col_type == "count" and next_col_type == "index":
                # Use the header minus the count/index suffix as category name
                cat_name = category_name(col_header, COUNT_PATTERNS)
                if not cat_name:
                    cat_name = f"category_{len(categories)+1}"
                categories.append({
                    "name": cat_name,
                    "count_col": col_idx,
                    "index_col": next_col_idx,
                })
                i += 2
                continue
            elif col_type == "index" and next_col_type == "count":
                cat_name = category_name(col_header, INDEX_PATTERNS)
                if not cat_name:
                    cat_name = f"category_{len(categories)+1}"
                categories.append({
                    "name": cat_name,
                    "index_col": col_idx,
                    "count_col": next_col_idx,
                })
                i += 2
                continue

        # Single column - treat as a standalone value, under the field its own
        # header names. Filing everything as "index" put headcounts where
        # salary_lookup reads a salary and compared them to the baseline.
        if col_type is None:
            unclassified.append(col_header)
        categories.append({
            "name": col_header.lower().replace(" ", "_"),
            "value_col": col_idx,
            "value_type": col_type or "index",
        })
        i += 1

    if unclassified:
        print(
            f"Warning: in sheet '{ws.title}', could not tell whether these "
            "columns hold a headcount or a salary index; stored as index: "
            + ", ".join(unclassified),
            file=sys.stderr,
        )

    # Parse data rows
    companies = []
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        if not row[company_col]:
            continue

        company_name = str(row[company_col]).strip()
        city_name = str(row[city_col]).strip() if city_col is not None and row[city_col] else ""

        entry = {
            "company": company_name,
            "city": city_name,
            "categories": {},
        }

        for cat in categories:
            cat_name = cat["name"]
            if "count_col" in cat and "index_col" in cat:
                count_val = None
                index_val = None
                if cat["count_col"] < len(row) and row[cat["count_col"]] is not None:
                    try:
                        count_val = int(row[cat["count_col"]])
                    except (ValueError, TypeError):
                        pass
                if cat["index_col"] < len(row) and row[cat["index_col"]] is not None:
                    try:
                        index_val = float(row[cat["index_col"]])
                    except (ValueError, TypeError):
                        pass
                entry["categories"][cat_name] = {"count": count_val, "index": index_val}
            elif "value_col" in cat:
                if cat["value_col"] < len(row) and row[cat["value_col"]] is not None:
                    val = row[cat["value_col"]]
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        val = str(val)
                    entry["categories"][cat_name] = {cat["value_type"]: val}

        companies.append(entry)

    return companies


def main():
    parser = argparse.ArgumentParser(
        description="Convert salary Excel data to JSON"
    )
    parser.add_argument("excel_file", help="Path to the Excel file with salary data")
    parser.add_argument(
        "--output", default=None,
        help="Output JSON file path (default: data/profile/salary_data.json)",
    )
    parser.add_argument(
        "--source", default=None,
        help="Name of the data source (e.g., 'Union Statistics 2025')",
    )
    parser.add_argument(
        "--baseline", type=float, default=100,
        help="Baseline value for index comparison (default: 100)",
    )
    parser.add_argument(
        "--baseline-desc", default=None,
        help="Description of what the baseline means (e.g., 'Index 100 = median salary')",
    )
    args = parser.parse_args()

    # Imported here rather than at module level so --help still works on a
    # machine that has never installed the one optional dependency.
    try:
        import openpyxl
    except ImportError:
        print("Error: openpyxl is required. Install it with: pip install openpyxl", file=sys.stderr)
        sys.exit(1)

    excel_path = Path(args.excel_file)
    if not excel_path.exists():
        print(f"Error: File not found: {excel_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output) if args.output else paths.PROFILE / "salary_data.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Reading: {excel_path}")
    wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)

    all_companies = []
    for sheet_name in wb.sheetnames:
        print(f"  Parsing sheet: {sheet_name}")
        ws = wb[sheet_name]
        companies = parse_sheet(ws)
        all_companies.extend(companies)

    wb.close()

    if not all_companies:
        print("Error: No data could be parsed from the Excel file.", file=sys.stderr)
        print("Make sure the Excel file has a header row with a 'Company'/'Firma' column.", file=sys.stderr)
        sys.exit(1)

    # Build output
    output = {
        "metadata": {
            "source": args.source or excel_path.stem,
            "index_baseline": args.baseline,
            "index_label": "Index",
            "baseline_description": args.baseline_desc or f"Index {args.baseline} = baseline",
        },
        "companies": all_companies,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nDone! Wrote {len(all_companies)} company entries to {output_path}")


if __name__ == "__main__":
    from console import use_utf8
    use_utf8()
    main()
