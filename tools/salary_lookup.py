#!/usr/bin/env python3
"""
Salary Benchmark Lookup Tool

Looks up company salary data from a user-provided dataset.
Supports any salary data source: union statistics, Glassdoor exports,
manually collected benchmarks, whatever you have.

This tool requires a data file (profile/salary_data.json) that you create
from your own salary data. See tools/README_SALARY_TOOL.md for
instructions on the expected format and how to convert from Excel.

Usage:
    python tools/salary_lookup.py "Company Name"
    python tools/salary_lookup.py "Company Name" --city "Lisbon"
    python tools/salary_lookup.py "Company Name" --json
    python tools/salary_lookup.py --list-all
"""

import json
import sys
import re
import argparse
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA_FILE = REPO / "profile" / "salary_data.json"

# Company-name normalisation. The lists live in config/config.toml under
# [salary] so this works in any market; the values below are only the fallback
# for a workspace with no configuration yet.
def _load_normalisation():
    legal = [
        "a/s", "aps", "i/s", "p/s", "k/s", "ivs", "amba",
        "gmbh", "mbh", "ag", "kg", "ug",
        "ltd", "limited", "plc", "llp", "inc", "incorporated", "corp",
        "corporation", "llc", "co", "company",
        "bv", "nv", "sa", "sas", "sarl", "srl", "spa", "ab", "as", "oy",
        "lda", "unipessoal", "sl", "sll", "pte", "pty",
    ]
    regions = [
        "europe", "emea", "nordic", "nordics", "scandinavia",
        "international", "global", "group", "holding",
    ]
    try:
        import tomllib

        cfg = REPO / "config" / "config.toml"
        if cfg.exists():
            with cfg.open("rb") as fh:
                data = tomllib.load(fh).get("salary", {})
            legal = data.get("strip_legal_forms", legal)
            regions = data.get("strip_regions", regions)
    except Exception:
        pass  # a broken config must not break salary lookup
    return legal, regions


_LEGAL_FORMS, _REGIONS = _load_normalisation()

# Build the strip patterns from the configured lists. The word boundaries
# matter: without them "co" would be cut out of the middle of a name.
STRIP_PATTERNS = (
    [rf"\b{re.escape(f)}\b" for f in _LEGAL_FORMS + _REGIONS]
    + [
        r"\(.*?\)",  # parentheticals
        r",\s*.*$",  # everything after a comma (sub-entities)
    ]
)


def load_data():
    if not DATA_FILE.exists():
        print("Error: profile/salary_data.json not found.", file=sys.stderr)
        print("", file=sys.stderr)
        print("This tool requires a salary data file.", file=sys.stderr)
        print("See tools/README_SALARY_TOOL.md for setup instructions.", file=sys.stderr)
        print("", file=sys.stderr)
        print("If you don't have salary data, the salary lookup", file=sys.stderr)
        print("step will be skipped during /apply.", file=sys.stderr)
        sys.exit(1)
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def fold(s):
    """Strip diacritics so "Nestlé" and "Nestle" compare equal.

    Handles the ligature-style letters NFKD leaves alone (ø, æ, ß, ...), then
    decomposes the rest. Works for any Latin-script market, not just one.
    """
    s = s.lower()
    for src, dst in (("ø", "o"), ("æ", "ae"), ("å", "aa"), ("ß", "ss"), ("ł", "l")):
        s = s.replace(src, dst)
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def strip_noise(s):
    """Apply the configured strip patterns, one at a time.

    A pattern that would leave nothing behind is skipped: for a company
    actually named "Company Ltd" the generic words are the name, and an empty
    key would match every entry in the dataset.
    """
    for pat in STRIP_PATTERNS:
        candidate = re.sub(pat, "", s)
        if re.sub(r"[^a-z0-9]", "", candidate):
            s = candidate
    return s


def normalize(s):
    """Normalize string for robust fuzzy matching."""
    # Periods hide legal forms from the word-boundary patterns: "S.A." has to
    # reduce to "sa" before they run. Nothing downstream needs them.
    s = fold(s).strip().replace(".", "")
    return re.sub(r"[^a-z0-9]", "", strip_noise(s)).strip()


def extract_core_words(s):
    """Extract meaningful words from a company name, ignoring noise."""
    words = re.findall(r"[a-z0-9]+", strip_noise(fold(s).replace(".", "")))
    return [w for w in words if len(w) > 1]


def shared_words(a, b):
    """The meaningful words two company names have in common."""
    return set(extract_core_words(a)) & set(extract_core_words(b))


def match_score(query, entry_name):
    """Compute a match score between 0 and 100 for ranking results."""
    q_norm = normalize(query)
    n_norm = normalize(entry_name)

    if not q_norm or not n_norm:
        return 0

    if q_norm == n_norm:
        return 100

    # One name contains the other. A short string inside a much longer one is
    # weak evidence on its own -- "abc" sits inside "Abcdef Holdings" -- so
    # that case has to be backed by a word the two names actually share.
    if q_norm in n_norm:
        ratio = len(q_norm) / len(n_norm)
        if len(q_norm) <= 4 and ratio < 0.5:
            if shared_words(query, entry_name):
                return 80 + int(ratio * 10)
        else:
            return 80 + int(ratio * 10)

    if n_norm in q_norm:
        ratio = len(n_norm) / len(q_norm)
        if len(n_norm) <= 4 and ratio < 0.5:
            # The mirror image, scored lower: the dataset entry is the short
            # side, so the query carries words the entry never had.
            if shared_words(query, entry_name):
                return 75
        else:
            return 80 + int(ratio * 10)

    q_words = set(extract_core_words(query))
    n_words = set(extract_core_words(entry_name))
    if not q_words or not n_words:
        return 0

    overlap = q_words & n_words
    if overlap:
        # A one-word query that overlaps at all is that word, matched whole.
        if len(q_words) == 1:
            return 70
        coverage = len(overlap) / len(q_words)
        return int(30 + coverage * 40)

    return 0


def search_company(data, query, city=None):
    """Search for a company by name. Returns matching entries sorted by relevance."""
    companies = data.get("companies", [])
    scored = []

    for entry in companies:
        if city:
            city_lower = city.lower()
            entry_city = entry.get("city", "").lower()
            if city_lower not in entry_city and fold(city_lower) not in fold(entry_city):
                continue

        score = match_score(query, entry["company"])
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: (-x[0], x[1]["company"]))

    min_score = 30
    return [entry for score, entry in scored if score >= min_score]


def fmt_number(value):
    """Render a metric without inventing precision it does not have.

    An index keeps its decimal; a salary in euros reads as 105,000 rather
    than 105000.0.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.1f}"


def fmt_label(label):
    """Unfold a category key into a heading, leaving its casing alone.

    .title() rewrites "eur" as "Eur". How a dataset spells its own units is
    not this tool's decision to make.
    """
    text = label.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def fmt_difference(value, baseline):
    """The distance from the baseline, as a percentage of it.

    Subtracting the raw values only reads as a percentage when the baseline
    happens to be 100. On a dataset denominated in euros, 105,000 against a
    62,166 baseline is +68.9%, not the +42,834% the subtraction claims. With
    no baseline configured there is nothing to compare against, so the column
    stays empty rather than inventing a comparison.
    """
    if not baseline:
        return ""
    try:
        return f"{(float(value) - float(baseline)) / float(baseline) * 100:+.1f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return ""


def format_entry(entry, metadata):
    """Format a single company entry for display."""
    # Get category data (everything except company/city fields)
    categories = entry.get("categories", {})
    if not categories:
        # Fallback: treat any numeric fields as categories
        skip_keys = {"company", "city", "categories"}
        for key, value in entry.items():
            if key not in skip_keys and isinstance(value, dict):
                categories[key] = value

    body = [f"  {entry['company']}"]
    if entry.get("city"):
        body.append(f"  Location: {entry['city']}")
    heading_ends = len(body)

    if categories:
        index_label = metadata.get("index_label", "Index")
        baseline = metadata.get("index_baseline", 100)

        rows, withheld = [], False
        for label, data in categories.items():
            count, value = data.get("count"), data.get("index")
            if count is None and value is None:
                continue
            if value is None:
                withheld = True
                value_str, diff_str = "N/A*", ""
            else:
                value_str = fmt_number(value)
                diff_str = fmt_difference(value, baseline)
            # A count of 0 is a fact; only a missing one is a dash.
            count_str = "-" if count is None else str(count)
            rows.append((fmt_label(label), count_str, value_str, diff_str))

        if rows:
            # The columns are sized from the data. A category name that
            # carries its own unit runs well past the 22 characters this
            # table used to assume, and the header drifted off the values.
            heads = ("Category", "Count", index_label, "vs Baseline")
            widths = [
                max(len(head), max(len(row[i]) for row in rows))
                for i, head in enumerate(heads)
            ]
            body.append(
                f"  {heads[0]:<{widths[0]}}  {heads[1]:>{widths[1]}}"
                f"  {heads[2]:>{widths[2]}}  {heads[3]:>{widths[3]}}"
            )
            body.append("  " + "-" * (sum(widths) + 6))
            for label, count, value_str, diff_str in rows:
                body.append(
                    f"  {label:<{widths[0]}}  {count:>{widths[1]}}"
                    f"  {value_str:>{widths[2]}}  {diff_str:>{widths[3]}}"
                )

        notes = []
        if withheld:
            notes.append("  * N/A = the dataset carries no value for this category")
        if metadata.get("baseline_description"):
            notes.append(f"  {metadata['baseline_description']}")
        elif baseline:
            notes.append(f"  {index_label} {fmt_number(baseline)} = baseline")
        if notes:
            if rows:
                body.append("")
            body.extend(notes)
    else:
        # Simple format: just show all non-standard fields
        skip_keys = {"company", "city", "categories"}
        for key, value in entry.items():
            if key not in skip_keys:
                body.append(f"  {fmt_label(key)}: {value}")

    banner = "=" * max(len(line) for line in body)
    return "\n".join(["", banner, *body[:heading_ends], banner, *body[heading_ends:]])


def main():
    parser = argparse.ArgumentParser(description="Salary Benchmark Lookup")
    parser.add_argument("company", nargs="?", help="Company name to search for")
    parser.add_argument("--city", help="Filter by city name")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--list-all", action="store_true", help="List all companies")
    args = parser.parse_args()

    data = load_data()
    metadata = data.get("metadata", {})
    companies = data.get("companies", [])

    if args.list_all:
        for entry in companies:
            city = entry.get("city", "")
            city_str = f" ({city})" if city else ""
            print(f"{entry['company']}{city_str}")
        return

    if not args.company:
        parser.print_help()
        sys.exit(1)

    results = search_company(data, args.company, args.city)

    if not results:
        print(f"No results found for '{args.company}'")
        if args.city:
            print(f"  (filtered by city: {args.city})")
        print("\nTry a shorter or different name. Company names in the dataset")
        print("may include legal suffixes like 'A/S' or 'ApS'.")
        sys.exit(1)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"\nFound {len(results)} match(es) for '{args.company}':")
        for entry in results:
            print(format_entry(entry, metadata))
        print()


if __name__ == "__main__":
    from console import use_utf8
    use_utf8()
    main()
