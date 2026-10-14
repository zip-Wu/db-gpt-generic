#!/usr/bin/env python3
"""Excel Data Analysis Tool

Subcommands:
  inspect   - Show sheet names, column info, dtypes, shape
  stats     - Descriptive statistics for numeric columns
  head      - Show first N rows
  filter    - Filter rows by condition
  aggregate - Group by and aggregate
  pivot     - Create pivot table
  export    - Export filtered/processed data to CSV

Usage:
  python analyze_excel.py <file> <subcommand> [options]
  python analyze_excel.py <file> inspect
  python analyze_excel.py <file> stats --sheet Sheet1
  python analyze_excel.py <file> head --rows 10
  python analyze_excel.py <file> filter --col "Region" --op eq --val "East"
  python analyze_excel.py <file> aggregate --group "Category" --agg "Sum:Revenue"
  python analyze_excel.py <file> pivot --index "Category" --columns "Year" --values "Revenue"
  python analyze_excel.py <file> export --out /tmp/result.csv --col "Region" --op eq --val "East"
"""

import argparse
import json
import sys
import os

try:
    import pandas as pd
except ImportError:
    print(json.dumps({"error": "pandas not installed. Run: pip install pandas openpyxl"}))
    sys.exit(1)


def load_excel(file_path, sheet_name=None):
    """Load Excel file into DataFrame."""
    if not os.path.exists(file_path):
        print(json.dumps({"error": f"File not found: {file_path}"}))
        sys.exit(1)
    
    try:
        if sheet_name:
            df = pd.read_excel(file_path, sheet_name=sheet_name)
        else:
            df = pd.read_excel(file_path, sheet_name=0)
        return df
    except Exception as e:
        print(json.dumps({"error": f"Failed to read Excel: {str(e)}"}))
        sys.exit(1)


def get_sheets(file_path):
    """Get all sheet names."""
    try:
        xl = pd.ExcelFile(file_path)
        return xl.sheet_names
    except Exception as e:
        return [f"Error: {str(e)}"]


def cmd_inspect(args):
    """Inspect file structure."""
    sheets = get_sheets(args.file)
    result = {"file": args.file, "sheets": sheets, "details": {}}
    
    for sheet in sheets[:5]:  # Limit to 5 sheets
        try:
            df = pd.read_excel(args.file, sheet_name=sheet)
            col_info = {}
            for col in df.columns:
                col_info[str(col)] = {
                    "dtype": str(df[col].dtype),
                    "non_null": int(df[col].notna().sum()),
                    "null": int(df[col].isna().sum()),
                    "unique": int(df[col].nunique()),
                }
            result["details"][sheet] = {
                "shape": list(df.shape),
                "columns": [str(c) for c in df.columns],
                "column_info": col_info,
            }
        except Exception as e:
            result["details"][sheet] = {"error": str(e)}
    
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def cmd_stats(args):
    """Descriptive statistics."""
    df = load_excel(args.file, args.sheet)
    numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
    
    result = {
        "file": args.file,
        "sheet": args.sheet or "Sheet1",
        "shape": list(df.shape),
        "numeric_columns": [str(c) for c in numeric_cols],
        "stats": {},
    }
    
    for col in numeric_cols[:20]:  # Limit to 20 columns
        s = df[col].dropna()
        result["stats"][str(col)] = {
            "count": int(s.count()),
            "mean": round(float(s.mean()), 4) if s.count() > 0 else None,
            "std": round(float(s.std()), 4) if s.count() > 1 else None,
            "min": round(float(s.min()), 4) if s.count() > 0 else None,
            "25%": round(float(s.quantile(0.25)), 4) if s.count() > 0 else None,
            "50%": round(float(s.quantile(0.5)), 4) if s.count() > 0 else None,
            "75%": round(float(s.quantile(0.75)), 4) if s.count() > 0 else None,
            "max": round(float(s.max()), 4) if s.count() > 0 else None,
        }
    
    # Categorical column value counts
    cat_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()
    result["categorical"] = {}
    for col in cat_cols[:10]:
        vc = df[col].value_counts().head(10)
        result["categorical"][str(col)] = {
            "unique": int(df[col].nunique()),
            "top_values": {str(k): int(v) for k, v in vc.items()},
        }
    
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def cmd_head(args):
    """Show first N rows."""
    df = load_excel(args.file, args.sheet)
    n = args.rows if args.rows else 10
    result = {
        "file": args.file,
        "total_rows": len(df),
        "columns": [str(c) for c in df.columns],
        "data": df.head(n).to_dict(orient='records'),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def apply_filter(df, col, op, val):
    """Apply a filter condition to DataFrame."""
    try:
        val = str(val)
        if op == 'eq':
            return df[df[col].astype(str) == val]
        elif op == 'ne':
            return df[df[col].astype(str) != val]
        elif op == 'gt':
            return df[df[col] > float(val)]
        elif op == 'lt':
            return df[df[col] < float(val)]
        elif op == 'gte':
            return df[df[col] >= float(val)]
        elif op == 'lte':
            return df[df[col] <= float(val)]
        elif op == 'contains':
            return df[df[col].astype(str).str.contains(val, case=False, na=False)]
        elif op == 'in':
            vals = [v.strip() for v in val.split(',')]
            return df[df[col].astype(str).isin(vals)]
        else:
            return df
    except Exception as e:
        print(json.dumps({"error": f"Filter failed: {str(e)}"}))
        sys.exit(1)


def cmd_filter(args):
    """Filter rows."""
    df = load_excel(args.file, args.sheet)
    filtered = apply_filter(df, args.col, args.op, args.val)
    result = {
        "file": args.file,
        "original_rows": len(df),
        "filtered_rows": len(filtered),
        "filter": {"col": args.col, "op": args.op, "val": args.val},
        "columns": [str(c) for c in filtered.columns],
        "data": filtered.head(args.rows if args.rows else 20).to_dict(orient='records'),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def cmd_aggregate(args):
    """Group by and aggregate."""
    df = load_excel(args.file, args.sheet)
    
    # Parse agg specs: "Sum:Revenue,Avg:Cost,Count:ID"
    agg_specs = {}
    for spec in args.agg.split(','):
        if ':' in spec:
            func, col = spec.split(':', 1)
            agg_specs[col.strip()] = func.strip().lower()
        else:
            agg_specs[spec.strip()] = 'count'
    
    try:
        grouped = df.groupby(args.group).agg(agg_specs)
        result = {
            "file": args.file,
            "group_by": args.group,
            "aggregations": agg_specs,
            "rows": len(grouped),
            "data": grouped.reset_index().to_dict(orient='records'),
        }
    except Exception as e:
        result = {"error": f"Aggregation failed: {str(e)}"}
    
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def cmd_pivot(args):
    """Create pivot table."""
    df = load_excel(args.file, args.sheet)
    
    try:
        pivot = pd.pivot_table(
            df,
            index=args.index,
            columns=args.columns,
            values=args.values,
            aggfunc=args.aggfunc.lower() if args.aggfunc else 'sum',
            fill_value=0,
        )
        result = {
            "file": args.file,
            "pivot": {
                "index": args.index,
                "columns": args.columns,
                "values": args.values,
                "aggfunc": args.aggfunc or 'sum',
            },
            "shape": list(pivot.shape),
            "data": pivot.reset_index().to_dict(orient='records'),
        }
    except Exception as e:
        result = {"error": f"Pivot failed: {str(e)}"}
    
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def cmd_export(args):
    """Export data to CSV."""
    df = load_excel(args.file, args.sheet)
    
    if args.col and args.op and args.val:
        df = apply_filter(df, args.col, args.op, args.val)
    
    if args.cols:
        cols = [c.strip() for c in args.cols.split(',')]
        df = df[cols]
    
    out_path = args.out if args.out else os.path.splitext(args.file)[0] + "_export.csv"
    df.to_csv(out_path, index=False)
    
    print(json.dumps({
        "success": True,
        "output": out_path,
        "rows": len(df),
        "columns": [str(c) for c in df.columns],
    }, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description='Excel Data Analysis Tool')
    parser.add_argument('file', help='Path to Excel file')
    parser.add_argument('command', choices=['inspect', 'stats', 'head', 'filter', 'aggregate', 'pivot', 'export'])
    parser.add_argument('--sheet', help='Sheet name (default: first)')
    parser.add_argument('--rows', type=int, help='Number of rows to show')
    parser.add_argument('--col', help='Column name for filter/export')
    parser.add_argument('--op', choices=['eq', 'ne', 'gt', 'lt', 'gte', 'lte', 'contains', 'in'], help='Filter operator')
    parser.add_argument('--val', help='Filter value')
    parser.add_argument('--group', help='Group by column(s), comma-separated')
    parser.add_argument('--agg', help='Aggregation specs: "Sum:Revenue,Avg:Cost"')
    parser.add_argument('--index', help='Pivot index column')
    parser.add_argument('--columns', help='Pivot columns')
    parser.add_argument('--values', help='Pivot values column')
    parser.add_argument('--aggfunc', help='Pivot aggregation function')
    parser.add_argument('--out', help='Output file path for export')
    parser.add_argument('--cols', help='Columns to export, comma-separated')
    
    args = parser.parse_args()
    
    commands = {
        'inspect': cmd_inspect,
        'stats': cmd_stats,
        'head': cmd_head,
        'filter': cmd_filter,
        'aggregate': cmd_aggregate,
        'pivot': cmd_pivot,
        'export': cmd_export,
    }
    
    commands[args.command](args)


if __name__ == '__main__':
    main()
