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
    python tools/salary_lookup.py add --company "Acme" --city "Lisbon" \
        --category senior_frontend_eur_gross_annual --index 62000 --count 2 \
        --source https://... --as-of 2026-08
    python tools/salary_lookup.py add --company "Acme" --city "Lisbon" --unknown \
        --note "US only: $150-190k (levels.fyi, 2026-05)" --as-of 2026-08

`add` is how a figure found during company research lands here, with its
source and date. A record is one company in one city; a figure for another
location never becomes this location's benchmark -- `--unknown` records that
nothing was found for it, with the lead in the note.
"""

import json
import os
import tempfile
import sys
import re
import argparse
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402

def data_file() -> Path:
    """Read at call time, never captured: paths.configure() has to be able
    to move the whole layout, which a module-level constant outlives."""
    return paths.PROFILE / "salary_data.json"

# Company-name normalisation. The lists live in config/config.toml under
# [salary] so this works in any market; the values below are only the fallback
# for a workspace with no configuration yet.
DEFAULT_LEGAL_FORMS = [
    "a/s", "aps", "i/s", "p/s", "k/s", "ivs", "amba",
    "gmbh", "mbh", "ag", "kg", "ug",
    "ltd", "limited", "plc", "llp", "inc", "incorporated", "corp",
    "corporation", "llc", "co", "company",
    "bv", "nv", "sa", "sas", "sarl", "srl", "spa", "ab", "as", "oy", "sp z oo",
    "lda", "unipessoal", "sl", "sll", "pte", "pty",
]
DEFAULT_REGIONS = [
    "europe", "emea", "nordic", "nordics", "scandinavia",
    "international", "global", "group", "holding",
]


def _load_normalisation():
    # A configured list replaces the default outright, so config.example.toml
    # has to carry every entry these do -- a copy of it that is missing one
    # silently normalises worse than no configuration at all. A test pins the
    # two together.
    legal = list(DEFAULT_LEGAL_FORMS)
    regions = list(DEFAULT_REGIONS)
    try:
        import tomllib

        cfg = paths.CONFIG
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
    path = data_file()
    if not path.exists():
        print("Error: data/profile/salary_data.json not found.", file=sys.stderr)
        print("", file=sys.stderr)
        print("This tool requires a salary data file.", file=sys.stderr)
        print("See tools/README_SALARY_TOOL.md for setup instructions.", file=sys.stderr)
        print("", file=sys.stderr)
        print("If you don't have salary data, the salary lookup", file=sys.stderr)
        print("step will be skipped during /apply.", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    """Write the file back whole, atomically, metadata and all."""
    path = data_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".salary_data-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def all_categories(data):
    """Every category name the file uses, in first-seen order."""
    seen = []
    for entry in data.get("companies", []):
        for name in (entry.get("categories") or {}):
            if name not in seen:
                seen.append(name)
    return seen


def baseline_unit(data):
    """The unit the baseline is in, as a category-name suffix.

    metadata.baseline_unit when the file states it; otherwise the trailing
    tokens every category name shares (senior_frontend_eur_gross_annual and
    senior_fullstack_eur_gross_annual -> eur_gross_annual). Only categories
    ending with it are compared to the baseline: a USD figure against a EUR
    median is not a percentage of anything.
    """
    stated = (data.get("metadata") or {}).get("baseline_unit")
    if stated:
        return stated
    names = all_categories(data)
    if not names:
        return None
    parts = [n.split("_") for n in names]
    common = []
    for tokens in zip(*(reversed(p) for p in parts)):
        if len(set(tokens)) != 1:
            break
        common.append(tokens[0])
    if not common:
        # Nothing shared between the category names: no unit to read.
        return None
    return "_".join(reversed(common))


def compares_to_baseline(label, unit):
    return not unit or label == unit or label.endswith("_" + unit)


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
    for key, heading in (("source", "Source"), ("as_of", "As of"), ("note", "Note")):
        if entry.get(key):
            body.append(f"  {heading}: {entry[key]}")
    heading_ends = len(body)

    if categories:
        index_label = metadata.get("index_label", "Index")
        baseline = metadata.get("index_baseline", 100)
        unit = metadata.get("_baseline_unit")

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
                diff_str = fmt_difference(value, baseline) if compares_to_baseline(label, unit) else ""
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
    elif entry.get("note") or entry.get("origin"):
        body.append("  No figure for this location -- see the note above." if entry.get("note")
                    else "  No figure for this location.")
    else:
        # Simple format: just show all non-standard fields
        skip_keys = {"company", "city", "categories", "source", "as_of", "note", "origin"}
        for key, value in entry.items():
            if key not in skip_keys:
                body.append(f"  {fmt_label(key)}: {value}")

    banner = "=" * max(len(line) for line in body)
    return "\n".join(["", banner, *body[:heading_ends], banner, *body[heading_ends:]])


def find_record(data, company, city):
    """The record for this company in this city, if any.

    Company by the same normalisation the lookup uses at its exact-match
    level; city folded, so "Lisbon" and "Lisboa" are still two cities -- the
    dataset decides how it spells a place, this tool does not translate.
    """
    want_company = normalize(company)
    want_city = fold(city or "").strip()
    for entry in data.get("companies", []):
        if normalize(entry.get("company", "")) != want_company:
            continue
        if fold(entry.get("city") or "").strip() == want_city:
            return entry
    return None


def add_record(data, company, city, category=None, index=None, count=None,
               source=None, as_of=None, note=None, unknown=False,
               force=False, new_category=False):
    """Add or update one company-in-one-city record. Returns a message.

    Refuses to overwrite a figure that is already there unless forced, and
    refuses a category name the file has never seen unless told it is new --
    that is what keeps a monthly figure out of an annual column by accident.
    Never converts units.
    """
    if unknown:
        if category or index is not None:
            raise ValueError("--unknown records that nothing was found; drop --category/--index")
    else:
        if not category or index is None:
            raise ValueError("a figure needs --category and --index (or pass --unknown)")
        if category not in all_categories(data) and not new_category:
            known = ", ".join(all_categories(data)) or "(none yet)"
            raise ValueError(
                f"category {category!r} is not in the file; the ones there are: {known}. "
                "Pass --new-category if it really is a new unit or role, e.g. a USD figure "
                "for a US posting."
            )

    if not unknown and category not in all_categories(data):
        # The first foreign category (a USD figure in a EUR file) is where the
        # file's own unit has to be written down, or the derived suffix would
        # shrink to what both units share and the comparison would lie.
        metadata = data.setdefault("metadata", {})
        if not metadata.get("baseline_unit"):
            unit = baseline_unit(data)
            if unit:
                metadata["baseline_unit"] = unit

    entry = find_record(data, company, city)
    created = entry is None
    if created:
        entry = {"company": company, "city": city, "categories": {}}
        data.setdefault("companies", []).append(entry)
    cats = entry.setdefault("categories", {})

    if unknown:
        if cats:
            raise ValueError(
                f"{entry['company']} ({entry.get('city')}) already carries a figure: "
                f"{', '.join(cats)}. Nothing recorded."
            )
        entry["origin"] = "research"
    else:
        old = cats.get(category)
        if old is not None and not force:
            raise ValueError(
                f"{entry['company']} ({entry.get('city')}) already has {category} = "
                f"{fmt_number(old.get('index'))} (count {old.get('count')}); new figure "
                f"{fmt_number(index)} (count {count}). Pass --force to overwrite."
            )
        cats[category] = {"count": count, "index": index}
        entry["origin"] = entry.get("origin") or "research"

    for key, value in (("source", source), ("as_of", as_of), ("note", note)):
        if value:
            entry[key] = value

    what = "recorded" if created else "updated"
    if unknown:
        return f"{what} {entry['company']} ({city}): no figure for this location"
    return f"{what} {entry['company']} ({city}): {category} = {fmt_number(index)}"


def main_add(argv):
    parser = argparse.ArgumentParser(
        prog="salary_lookup.py add",
        description="Record a salary figure, or that none was found for a location",
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--city", required=True, help="the posting's location; one record per company and city")
    parser.add_argument("--category", help="e.g. senior_frontend_eur_gross_annual -- the unit is in the name")
    parser.add_argument("--index", type=float, help="the figure, in the category's unit")
    parser.add_argument("--count", type=int, help="how many independent sources stand behind it")
    parser.add_argument("--source", help="where it came from -- a URL")
    parser.add_argument("--as-of", dest="as_of", help="YYYY-MM the figure is for")
    parser.add_argument("--note", help="the lead, the caveat, the other-market figure")
    parser.add_argument("--unknown", action="store_true",
                        help="record that nothing was found for this company at this location")
    parser.add_argument("--force", action="store_true", help="overwrite a figure already there")
    parser.add_argument("--new-category", dest="new_category", action="store_true",
                        help="the category is new to the file (a new unit or role)")
    args = parser.parse_args(argv)

    data = load_data()
    try:
        message = add_record(
            data, args.company, args.city, category=args.category, index=args.index,
            count=args.count, source=args.source, as_of=args.as_of, note=args.note,
            unknown=args.unknown, force=args.force, new_category=args.new_category,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    save_data(data)
    print(message)
    return 0


def main():
    if sys.argv[1:2] == ["add"]:
        sys.exit(main_add(sys.argv[2:]))

    parser = argparse.ArgumentParser(description="Salary Benchmark Lookup")
    parser.add_argument("company", nargs="?", help="Company name to search for")
    parser.add_argument("--city", help="Filter by city name")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--list-all", action="store_true", help="List all companies")
    args = parser.parse_args()

    data = load_data()
    metadata = dict(data.get("metadata", {}))
    metadata["_baseline_unit"] = baseline_unit(data)
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
