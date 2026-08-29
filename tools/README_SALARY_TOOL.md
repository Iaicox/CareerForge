# Salary Benchmark Tool

## What is this?

The salary lookup tool (`tools/salary_lookup.py`) lets you benchmark company salaries against a baseline from your own data. It's used during the `/apply` workflow to show how a company's compensation compares to market rates.

**This tool is optional.** If you don't have salary data, the salary step is simply skipped during `/apply`.

## How it works

The tool reads `data/profile/salary_data.json`, which sits with the rest of your own data. It uses fuzzy matching to find companies by name, folding diacritics, stripping legal suffixes (`A/S`, `GmbH`, `Lda`, `S.A.`) and region words, and tolerating common spelling variations. The lists it strips live in `data/config/config.toml` under `[salary]`, so it works in any Latin-script market.

The data format supports any index-based or absolute salary data. For example:
- Index 100 = median salary, higher is better
- Absolute salary values in your currency
- Any custom metric you want to track

The `vs Baseline` column is always a percentage **of the baseline**, so both kinds of dataset read correctly: an index of 112.5 against a baseline of 100 shows `+12.5%`, and a salary of 105,000 against a baseline of 62,166 shows `+68.9%` rather than a meaningless `+42,834%`.

## Data format

The tool expects `data/profile/salary_data.json` with this structure:

```json
{
  "metadata": {
    "source": "My Union Statistics 2025",
    "index_baseline": 100,
    "index_label": "Index",
    "baseline_description": "Index 100 = median salary for private sector"
  },
  "companies": [
    {
      "company": "Novo Nordisk A/S",
      "city": "Bagsværd",
      "categories": {
        "all_employees": { "count": 500, "index": 108.5 },
        "engineering": { "count": 120, "index": 112.3 }
      }
    },
    {
      "company": "Ørsted A/S",
      "city": "Fredericia",
      "categories": {
        "all_employees": { "count": 200, "index": 105.2 }
      }
    }
  ]
}
```

### Fields

- **metadata.source**: Where the data comes from (for reference)
- **metadata.index_baseline**: What every value is compared against (e.g. 100 for index data, or the market median for absolute salaries). Set it to `0` and the comparison column stays empty, because there is nothing to compare to
- **metadata.index_label**: Label for the value column in output, and the place to state the unit — `--json` returns only the company entries, so a reader of that output sees no metadata at all
- **metadata.baseline_description**: Human-readable explanation of the baseline
- **metadata.baseline_unit** (optional): the unit suffix the baseline is in, e.g. `eur_gross_annual`. Only categories ending with it get the `vs Baseline` column. When absent it is derived from the suffix every category shares; `add --new-category` writes it down the first time a category in another unit arrives
- **companies[].company**: Company name (required)
- **companies[].city**: City/location — one record is one company in one city; `add` matches on both
- **companies[].categories**: Named salary categories, each with `count` and/or `index`
- **companies[].source**, **as_of**, **note**, **origin** (optional): where a figure came from and when, the lead behind an unknown, and `research` for records the `/apply` research step wrote. Shown by the lookup, kept by `--json`

## Setup options

### Option A: Create data/profile/salary_data.json manually

Create the file by hand with data from any source: union statistics, Glassdoor, salary surveys, networking, or personal research.

### Option B: Convert from Excel

If you have salary data in an Excel file:

```bash
pip install openpyxl
python tools/convert_salary_excel.py path/to/salary-data.xlsx \
  --source "My Salary Data 2025" \
  --baseline 100 \
  --baseline-desc "Index 100 = median salary"
```

The converter auto-detects the Excel layout:
- Looks for a "Company"/"Firma" column and an optional "City"/"By" column
- Treats remaining columns as salary data (auto-pairs count/index columns)

### Option C: Build from research

Start with a template and add companies as you research them. For a market with no published index, put absolute money in `index` and set the baseline to the median you want everything measured against:

```json
{
  "metadata": {
    "source": "Personal research, August 2026",
    "index_baseline": 62166,
    "index_label": "EUR gross/year",
    "baseline_description": "62,166 = Lisbon senior median, gross annual"
  },
  "companies": [
    {
      "company": "Example Corp Lda",
      "city": "Lisboa",
      "categories": {
        "senior_frontend_eur_gross_annual": { "count": 4, "index": 72000 },
        "senior_fullstack_eur_gross_annual": { "count": 3, "index": 75000 }
      }
    }
  ]
}
```

Two things worth deciding before you start typing, because changing them later means revisiting every row:

- **One unit, stated in the category name.** Gross or net, annual or monthly — pick one and never mix. In markets that pay 14 salaries a year, €5,000 monthly is not €60,000 annually. The name carries the unit because `--json` drops the metadata.
- **`count` is how much evidence stands behind the number** — data points, offers, sources. It is not part of the comparison; it is there so you can see which rows to trust.

## Usage

```bash
python tools/salary_lookup.py "Novo Nordisk"
python tools/salary_lookup.py "Ørsted" --city "Fredericia"
python tools/salary_lookup.py "COWI" --json
python tools/salary_lookup.py --list-all
```

### Recording a figure: `add`

`/apply`'s research step asks what a company pays for the role, and records
what it finds here with its source and date:

```bash
python tools/salary_lookup.py add --company "Acme Lda" --city "Lisboa" \
  --category senior_frontend_eur_gross_annual --index 65000 --count 2 \
  --source https://www.glassdoor.com/... --as-of 2026-08

python tools/salary_lookup.py add --company "Acme Lda" --city "Lisboa" --unknown \
  --note "US only: $150-190k (levels.fyi, 2026-05)" --as-of 2026-08
```

- A record is **one company in one city**. A figure for another location is
  never this location's benchmark: `--unknown` records that nothing was found
  for it, with the other-market figure in the note as a lead, so the next
  application to the company shows "unknown" instead of researching again.
- **No unit conversion.** The figure goes in as the source states it, in the
  unit its category name carries. A category the file has never seen is
  refused unless `--new-category` says it really is a new unit or role — a
  `..._usd_gross_annual` figure for a US posting, say. The first such category
  pins `metadata.baseline_unit`, and figures in other units are shown without
  the `vs Baseline` column.
- A figure already there is not overwritten unless `--force`; both values are
  printed so you can decide.

## Important notes

- The data file lives in `data/profile/`, so it is **excluded from git** with the rest of your data. Salary figures are often confidential, and some are shared with you in confidence.
- If the data file is missing, `tools/salary_lookup.py` exits with a helpful error message and the `/apply` workflow skips the salary benchmark step.
- The fuzzy matcher absorbs the usual company-name variation: legal suffixes, diacritics, region words, anglicised spellings and partial matches. Write names as the postings write them.
