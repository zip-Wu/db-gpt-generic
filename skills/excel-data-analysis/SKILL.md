---
name: excel-data-analysis
description: >
  Analyze Excel (.xlsx/.xls) files with descriptive statistics, filtering, aggregation,
  pivot tables, and data export. Use when the user asks to: (1) inspect or explore
  an Excel file structure, (2) compute statistics on numeric columns, (3) filter rows
  by conditions, (4) group by and aggregate data, (5) create pivot tables, or
  (6) export processed data to CSV. Also use for any Excel data exploration, summary,
  or transformation task.
---

# Excel Data Analysis

Analyze Excel files using the bundled `analyze_excel.py` script. All commands output JSON for easy parsing.

## Quick Start

```bash
# Inspect file structure (sheets, columns, dtypes)
python scripts/analyze_excel.py <file.xlsx> inspect

# Get descriptive statistics
python scripts/analyze_excel.py <file.xlsx> stats

# Show first N rows
python scripts/analyze_excel.py <file.xlsx> head --rows 10
```

## Workflow

1. **Always start with `inspect`** to understand the file structure before any analysis.
2. Use `stats` to get an overview of data distributions.
3. Apply `filter`, `aggregate`, or `pivot` based on the user's question.
4. Use `export` when the user needs processed data saved to CSV.

## Subcommands Reference

### inspect
Show sheet names, column names, dtypes, null counts, and unique counts.
```
python scripts/analyze_excel.py <file> inspect
```

### stats
Descriptive statistics (count, mean, std, min, quartiles, max) for numeric columns.
Top-10 value counts for categorical columns.
```
python scripts/analyze_excel.py <file> stats [--sheet SheetName]
```

### head
Show first N rows of data.
```
python scripts/analyze_excel.py <file> head [--rows 10] [--sheet SheetName]
```

### filter
Filter rows by a condition. Operators: `eq`, `ne`, `gt`, `lt`, `gte`, `lte`, `contains`, `in`.
```
# Exact match
python scripts/analyze_excel.py <file> filter --col "Region" --op eq --val "East"

# Numeric comparison
python scripts/analyze_excel.py <file> filter --col "Revenue" --op gt --val 1000

# Contains (case-insensitive)
python scripts/analyze_excel.py <file> filter --col "Product" --op contains --val "laptop"

# In list (comma-separated)
python scripts/analyze_excel.py <file> filter --col "Status" --op in --val "Active,Pending"
```

### aggregate
Group by column(s) and aggregate. Format: `"Func:Col,Func:Col"`.
Supported functions: `Sum`, `Mean`, `Min`, `Max`, `Count`, `Std`.
```
# Sum revenue by category
python scripts/analyze_excel.py <file> aggregate --group "Category" --agg "Sum:Revenue"

# Multiple aggregations
python scripts/analyze_excel.py <file> aggregate --group "Region,Year" --agg "Sum:Revenue,Mean:Cost,Count:ID"
```

### pivot
Create a pivot table.
```
python scripts/analyze_excel.py <file> pivot --index "Category" --columns "Year" --values "Revenue" --aggfunc sum
```

### export
Export (optionally filtered) data to CSV.
```
# Export all data
python scripts/analyze_excel.py <file> export --out /tmp/result.csv

# Export filtered subset with specific columns
python scripts/analyze_excel.py <file> export --out /tmp/result.csv --col "Region" --op eq --val "East" --cols "Name,Revenue,Date"
```

## Tips

- For large files, use `--rows` to limit output size.
- Column names must match exactly (case-sensitive). Use `inspect` first to verify.
- For multi-sheet files, specify `--sheet` to target a specific sheet.
- When the user asks a business question, combine subcommands: inspect → stats → filter/aggregate → export.
- If pandas/openpyxl is missing, install with: `pip install pandas openpyxl`
