from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional, Sequence, Tuple


# ---------- Domain ----------

@dataclass(frozen=True)
class Trip:
    """A continuous period abroad (inclusive of both start and end)."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("Trip end date cannot be before start date")


Period = Tuple[date, date]


def parse_date(value: str) -> date:
    value = value.strip()
    # Accept common separators
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date format: {value}. Use YYYY-MM-DD (e.g., 2024-04-01)")


def clamp_trip_to_period(trip: Trip, period: Period) -> Optional[Trip]:
    p_start, p_end = period
    start = max(trip.start, p_start)
    end = min(trip.end, p_end)
    if end < start:
        return None
    return Trip(start, end)


def merge_overlapping_trips(trips: Sequence[Trip]) -> List[Trip]:
    if not trips:
        return []
    trips_sorted = sorted(trips, key=lambda t: t.start)
    merged: List[Trip] = []
    cur = trips_sorted[0]
    for t in trips_sorted[1:]:
        if t.start <= (cur.end + timedelta(days=1)):
            # Overlapping or contiguous; merge
            cur = Trip(cur.start, max(cur.end, t.end))
        else:
            merged.append(cur)
            cur = t
    merged.append(cur)
    return merged


def build_abroad_day_flags(trips: Sequence[Trip], period: Period) -> Tuple[List[int], date]:
    """Return an array flags[i] = 1 if the i-th day in [period_start, period_end] is abroad.

    Also returns the period_start to map indices back to dates.
    """
    p_start, p_end = period
    total_days = (p_end - p_start).days + 1
    flags = [0] * total_days
    if not trips:
        return flags, p_start

    for trip in trips:
        s = max(trip.start, p_start)
        e = min(trip.end, p_end)
        if e < s:
            continue
        si = (s - p_start).days
        ei = (e - p_start).days
        for i in range(si, ei + 1):
            flags[i] = 1
    return flags, p_start


def rolling_window_violations(
    abroad_flags: Sequence[int],
    window_days: int,
    max_allowed: int,
) -> List[Tuple[int, int, int]]:
    """Find windows where sum(flags[i..j]) > max_allowed.

    Returns list of tuples: (start_index, end_index_inclusive, abroad_days_in_window)
    where end_index = min(start_index + window_days - 1, len(flags)-1).
    """
    n = len(abroad_flags)
    if n == 0:
        return []

    # Prefix sum for O(1) range sums
    prefix = [0] * (n + 1)
    for i, v in enumerate(abroad_flags):
        prefix[i + 1] = prefix[i] + v

    violations: List[Tuple[int, int, int]] = []
    for start in range(n):
        end = min(start + window_days - 1, n - 1)
        abroad_days = prefix[end + 1] - prefix[start]
        if abroad_days > max_allowed:
            violations.append((start, end, abroad_days))
    return violations


def check_abroad_days(
    trips: Sequence[Trip],
    assessment_period: Period,
    window_days: int = 365,
    max_allowed: int = 180,
) -> Tuple[bool, List[Tuple[date, date, int]]]:
    """Core check: whether any rolling window exceeds the maximum allowed abroad days.

    Returns (is_compliant, violating_windows). Each violating window is returned as
    (window_start_date, window_end_date, abroad_days_in_window).
    """
    # Clamp and merge trips to the assessment period first
    clamped = [t for t in (clamp_trip_to_period(t, assessment_period) for t in trips) if t]
    merged = merge_overlapping_trips(clamped)

    flags, base = build_abroad_day_flags(merged, assessment_period)
    raw = rolling_window_violations(flags, window_days, max_allowed)

    humanized: List[Tuple[date, date, int]] = []
    for si, ei, days_in_window in raw:
        humanized.append((base + timedelta(days=si), base + timedelta(days=ei), days_in_window))

    return (len(humanized) == 0), humanized


# ---------- IO Helpers ----------

def read_trips_from_csv(path: str) -> List[Trip]:
    trips: List[Trip] = []
    with open(path, newline="", encoding="utf-8") as f:
        # Try DictReader first (headered), fall back to reader (no header)
        sample = f.read(1024)
        f.seek(0)
        has_header = csv.Sniffer().has_header(sample)
        if has_header:
            reader = csv.DictReader(f)
            for row in reader:
                start_raw = row.get("start") or row.get("Start") or row.get("from") or row.get("From")
                end_raw = row.get("end") or row.get("End") or row.get("to") or row.get("To")
                if not start_raw or not end_raw:
                    raise ValueError("CSV must have columns 'start' and 'end' (or 'from'/'to')")
                trips.append(Trip(parse_date(start_raw), parse_date(end_raw)))
        else:
            reader2 = csv.reader(f)
            for idx, row in enumerate(reader2, start=1):
                if not row:
                    continue
                if len(row) < 2:
                    raise ValueError(f"CSV row {idx} should have at least 2 columns: start,end")
                trips.append(Trip(parse_date(row[0]), parse_date(row[1])))
    return trips


def read_trips_interactive() -> List[Trip]:
    print("Enter trip ranges one per line, format: YYYY-MM-DD to YYYY-MM-DD")
    print("Press ENTER on an empty line when done. Examples: 2024-04-10 to 2024-05-14")
    trips: List[Trip] = []
    while True:
        line = input("> ").strip()
        if not line:
            break
        # allow separators like '-', 'to'
        if " to " in line:
            parts = line.split(" to ", 1)
        elif "-" in line and line.count("-") >= 4 and " to " not in line:
            # could be "YYYY-MM-DD - YYYY-MM-DD"
            parts = [p.strip() for p in line.split("-", 1)]
        elif "," in line:
            parts = [p.strip() for p in line.split(",", 1)]
        else:
            parts = line.split()
        if len(parts) != 2:
            print("Could not parse line. Please use: YYYY-MM-DD to YYYY-MM-DD")
            continue
        try:
            s = parse_date(parts[0])
            e = parse_date(parts[1])
            trips.append(Trip(s, e))
        except Exception as ex:  # noqa: BLE001 - provide user-friendly message
            print(f"Error: {ex}")
            continue
    return trips


# ---------- CLI ----------

DEFAULT_PERIOD: Period = (date(2024, 4, 1), date(2027, 12, 31))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check that for any rolling 12-month period between 2024-04-01 and "
            "2027-12-31, total days abroad do not exceed 180."
        )
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        help="Path to CSV with trips: columns 'start','end' (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        default=DEFAULT_PERIOD[0].isoformat(),
        help="Assessment period start (YYYY-MM-DD). Default: 2024-04-01",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        default=DEFAULT_PERIOD[1].isoformat(),
        help="Assessment period end (YYYY-MM-DD). Default: 2027-12-31",
    )
    parser.add_argument(
        "--window-days",
        dest="window_days",
        type=int,
        default=365,
        help="Size of rolling window in days. Default: 365",
    )
    parser.add_argument(
        "--max-days",
        dest="max_days",
        type=int,
        default=180,
        help="Max allowed abroad days within any window. Default: 180",
    )

    args = parser.parse_args(argv)

    period: Period = (parse_date(args.from_date), parse_date(args.to_date))
    if period[1] < period[0]:
        raise SystemExit("Assessment period end must not be before start")

    if args.csv_path:
        trips = read_trips_from_csv(args.csv_path)
    else:
        trips = read_trips_interactive()

    is_ok, violations = check_abroad_days(
        trips=trips,
        assessment_period=period,
        window_days=args.window_days,
        max_allowed=args.max_days,
    )

    if is_ok:
        print("Compliant: No 12-month window exceeds the allowed abroad days.")
        return 0

    print("NOT compliant: Some 12-month windows exceed the allowed abroad days.")
    # Show up to 10 violating windows, with the worst first
    violations_sorted = sorted(violations, key=lambda w: w[2], reverse=True)
    limit = min(10, len(violations_sorted))
    print(f"Top {limit} violating windows (start to end: days_abroad):")
    for ws, we, days in violations_sorted[:limit]:
        print(f"- {ws.isoformat()} to {we.isoformat()}: {days} days")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


