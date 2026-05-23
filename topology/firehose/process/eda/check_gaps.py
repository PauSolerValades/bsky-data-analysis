"""Check for time gaps in the Bluesky firehose directory tree.

Reads only directory names (no file I/O) to find missing months or days
in /data/nfs/datasets/bluesky/firehose/non-posts/YYYY-MM/DD/.
"""
import os
from datetime import date, timedelta

BASE = "/data/nfs/datasets/bluesky/firehose/non-posts"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "missing_days.csv")

# Collect all (year, month, day) tuples from the directory tree
present: set[tuple[int, int, int]] = set()
for entry in sorted(os.listdir(BASE)):
    month_path = os.path.join(BASE, entry)
    if not os.path.isdir(month_path):
        continue
    try:
        y, m = map(int, entry.split("-"))
    except ValueError:
        continue
    for day_entry in os.listdir(month_path):
        day_path = os.path.join(month_path, day_entry)
        if os.path.isdir(day_path):
            try:
                d = int(day_entry)
            except ValueError:
                continue
            present.add((y, m, d))

if not present:
    print("No data found!")
    exit(1)

# Determine the date range
dates = sorted(date(y, m, d) for y, m, d in present)
first, last = dates[0], dates[-1]

print(f"Data range: {first} → {last}")
print(f"Days with data: {len(present)}")
print(f"Total calendar days: {(last - first).days + 1}")
print()

# Check for missing months and days
current = first
gaps: list[tuple[date, date]] = []
while current <= last:
    t = (current.year, current.month, current.day)
    if t not in present:
        gap_start = current
        while current <= last and (current.year, current.month, current.day) not in present:
            current += timedelta(days=1)
        gap_end = current - timedelta(days=1)
        gaps.append((gap_start, gap_end))
    current += timedelta(days=1)

if gaps:
    print(f"Found {len(gaps)} gap(s):\n")
    with open(OUT, "w") as csv:
        csv.write("date\n")
        for s, e in gaps:
            days = (e - s).days + 1
            print(f"  {s} → {e}  ({days} day{'s' if days > 1 else ''})")
            d = s
            while d <= e:
                csv.write(f"{d}\n")
                d += timedelta(days=1)
    total_missing = sum((e - s).days + 1 for s, e in gaps)
    print(f"\nWrote {total_missing} missing days to missing_days.csv")
else:
    print("No gaps — every day is present. \u2705")

# Monthly summary
print(f"\n{'Month':<10} {'Days present':>12} {'Days in month':>14} {'Status'}")
print("-" * 45)
current = first
while current <= last:
    y, m = current.year, current.month
    # Days in this calendar month within range
    month_start = date(y, m, 1)
    if m == 12:
        month_end = date(y + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(y, m + 1, 1) - timedelta(days=1)
    # Clamp to data range
    eff_start = max(month_start, first)
    eff_end = min(month_end, last)
    total_in_range = (eff_end - eff_start).days + 1
    days_present = sum(1 for d in range(total_in_range)
                       if (y, m, eff_start.day + d) in present)
    status = "FULL" if days_present == total_in_range else f"MISSING {total_in_range - days_present}d"
    print(f"{y}-{m:02d}     {days_present:>12} {total_in_range:>14}   {status}")
    # Advance to next month
    if m == 12:
        current = date(y + 1, 1, 1)
    else:
        current = date(y, m + 1, 1)
