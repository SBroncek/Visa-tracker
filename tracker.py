from __future__ import annotations

import argparse
import csv
import os
import webbrowser
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


def sum_in_window(flags: Sequence[int], start_index: int, window_days: int) -> int:
    n = len(flags)
    if n == 0:
        return 0
    end = min(start_index + window_days - 1, n - 1)
    prefix = [0] * (n + 1)
    for i, v in enumerate(flags):
        prefix[i + 1] = prefix[i] + v
    return prefix[end + 1] - prefix[start_index]


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


# ---------- Visualization ----------

def render_terminal_grid(
    flags: Sequence[int],
    base_date: date,
    period_end: date,
    today: date,
) -> None:
    """Render a simple month-by-month grid in the terminal.

    Uses colored squares (ANSI) per day:
    - green: not abroad
    - red: abroad
    - black: future (after today)
    """
    # ANSI colors
    RED = "\x1b[41m\x1b[30m"  # red bg, black text
    GREEN = "\x1b[42m\x1b[30m"
    BLACK = "\x1b[40m\x1b[37m"
    RESET = "\x1b[0m"

    # Iterate months
    cur = date(base_date.year, base_date.month, 1)
    while cur <= period_end:
        # Determine month range within assessment
        next_month = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_start = max(cur, base_date)
        month_end = min(next_month - timedelta(days=1), period_end)
        # Header
        print(f"\n{cur.strftime('%B %Y')}")
        # Weekday header
        print("Mo Tu We Th Fr Sa Su")
        # Leading spaces
        first_weekday = (month_start.weekday() + 1) % 7  # Monday=0 -> Monday=1 style
        # Compute column offset: Monday=0..Sunday=6
        monday_based = month_start.weekday()  # 0..6
        col = monday_based
        # Print leading blanks
        if month_start.day != 1:
            # Need to align to the correct weekday for day 1
            first_of_month = month_start.replace(day=1)
            col = first_of_month.weekday()
            print("   " * col, end="")
        else:
            print("   " * col, end="")

        d = month_start
        while d <= month_end:
            idx = (d - base_date).days
            # choose color
            if d > today:
                color = BLACK
            else:
                color = RED if flags[idx] == 1 else GREEN
            # print two-char day as colored block with day number
            print(f"{color}{d.day:02d}{RESET} ", end="")
            if d.weekday() == 6:  # Sunday -> newline
                print()
            d += timedelta(days=1)
        print()
        cur = next_month


def render_html_grid(
    flags: Sequence[int],
    base_date: date,
    period_end: date,
    today: date,
    out_path: str,
) -> None:
    """Write an HTML with rows per year and columns per month; each cell shows a mini calendar."""

    def cell_class(d: date) -> str:
        idx = (d - base_date).days
        is_abroad = flags[idx] == 1 if 0 <= idx < len(flags) else False
        if is_abroad:
            return "abroad"  # future abroad should still be red
        if d > today:
            return "future"
        return "home"

    # Determine years and months within period; extend to one year after last abroad day
    last_abroad_idx = -1
    for i, v in enumerate(flags):
        if v == 1:
            last_abroad_idx = i
    last_abroad_year = (base_date + timedelta(days=last_abroad_idx)).year if last_abroad_idx >= 0 else period_end.year
    end_year = max(period_end.year, last_abroad_year + 1)
    years = list(range(base_date.year, end_year + 1))

    html_parts: List[str] = []
    html_parts.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    html_parts.append("<title>Abroad Days Grid</title>")
    html_parts.append(
        "<style>\n"
        ":root{--size:12px;--gap:1px;}\n"
        "body{font-family:system-ui,Segoe UI,Arial,sans-serif;padding:16px;}\n"
        ".legend{margin-bottom:12px} .legend span{display:inline-block;margin-right:12px}\n"
        ".sw{display:inline-block;width:12px;height:12px;margin-right:6px;vertical-align:middle;}\n"
        ".sw.home{background:#2ecc71} .sw.abroad{background:#e74c3c} .sw.future{background:#111} .sw.planned{background:#f39c12} \n"
        ".outer{display:grid;grid-template-columns:80px repeat(12, 1fr);gap:8px;align-items:start;}\n"
        ".year{font-weight:700;align-self:center;}\n"
        ".month{border:1px solid #eee;padding:6px;border-radius:4px;}\n"
        ".month-name{font-size:12px;color:#333;margin-bottom:4px;text-align:center;}\n"
        ".mini{display:grid;grid-template-columns:repeat(7,var(--size));grid-auto-rows:var(--size);gap:var(--gap);justify-content:center;}\n"
        ".cell{width:var(--size);height:var(--size);}\n"
        ".home{background:#2ecc71} .abroad{background:#e74c3c} .future{background:#111} .planned{background:#f39c12} \n"
        ".empty{background:transparent} \n"
        ".months-header{display:grid;grid-template-columns:80px repeat(12, 1fr);gap:8px;margin-bottom:6px;color:#555;font-size:12px;}\n"
        ".months-header div{ text-align:center }\n"
        ".panel{margin:12px 0;display:flex;gap:12px;align-items:center;flex-wrap:wrap;}\n"
        ".panel input[type=date]{padding:4px 6px} .panel .metric{font-size:13px;color:#333}\n"
        "</style>"
    )
    html_parts.append("</head><body>")
    html_parts.append("<h2>Abroad Days</h2>")
    html_parts.append("<div class='legend'>"
                     "<span><span class='sw home'></span>Home</span>"
                     "<span><span class='sw abroad'></span>Abroad</span>"
                     "<span><span class='sw planned'></span>Planned</span>"
                     "<span><span class='sw future'></span>Future (home)</span>"
                     "</div>")
    # Interaction panel
    html_parts.append("<div class='panel'>"
                     "<label>Query start: <input id='qstart' type='date'></label>"
                     "<span class='metric' id='remaining'></span>"
                     "<span class='metric' id='worst'></span>"
                     "<span class='metric' id='status'></span>"
                     "<label style='margin-left:12px'>Plan trip: <input id='pstart' type='date'> – <input id='pend' type='date'></label>"
                     "<button id='addPlan' type='button'>Add planned</button>"
                     "<button id='clearPlan' type='button'>Clear planned</button>"
                     "</div>")

    # Month header row
    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    html_parts.append("<div class='months-header'>")
    html_parts.append("<div></div>")  # placeholder over year column
    for m in month_names:
        html_parts.append(f"<div>{m}</div>")
    html_parts.append("</div>")

    html_parts.append("<div class='outer'>")
    for y in years:
        # Year label
        html_parts.append(f"<div class='year'>{y}</div>")
        # 12 months
        for m in range(1, 13):
            # Month boundaries
            first_of_month = date(y, m, 1)
            next_month = (first_of_month.replace(day=28) + timedelta(days=4)).replace(day=1)
            last_of_month = next_month - timedelta(days=1)
            # Determine overlap with assessment period
            if last_of_month < base_date or first_of_month > period_end:
                # outside period -> render empty cell
                html_parts.append("<div class='month'></div>")
                continue
            month_start = max(first_of_month, base_date)
            month_end = min(last_of_month, period_end)

            html_parts.append("<div class='month'>")
            html_parts.append(f"<div class='month-name'>{first_of_month.strftime('%b')}</div>")
            html_parts.append("<div class='mini'>")
            # Leading blanks to align first day of the month to weekday (Mon=0)
            col_offset = first_of_month.weekday()
            for _ in range(col_offset):
                html_parts.append("<div class='cell empty'></div>")
            # Days of the month (full month rendered, but only color within period)
            d = first_of_month
            while d <= last_of_month:
                cls = "empty"
                if base_date <= d <= period_end:
                    cls = cell_class(d)
                    idx = (d - base_date).days
                    html_parts.append(f"<div class='cell {cls}' data-idx='{idx}' title='{d.isoformat()}'></div>")
                else:
                    html_parts.append("<div class='cell empty'></div>")
                d += timedelta(days=1)
            html_parts.append("</div>")  # mini
            html_parts.append("</div>")  # month
    html_parts.append("</div>")  # outer

    # Embed data and JS for interactivity
    total_days = (period_end - base_date).days + 1
    flags_js = ",".join(str(int(v)) for v in flags)
    html_parts.append("<script>")
    html_parts.append(
        f"const baseDate = new Date('{base_date.isoformat()}');\n"
        f"const periodEnd = new Date('{period_end.isoformat()}');\n"
        f"const windowDays = 365;\n"
        f"const maxDays = 180;\n"
        f"const flags = new Uint8Array([{flags_js}]);\n"
        f"const planned = new Uint8Array({total_days});\n"
        "function sumRange(arr, start, end){let s=0; for(let i=start;i<=end;i++){s+=arr[i]||0;} return s;}\n"
        "function worstWindow(){let n=flags.length;let best=0; for(let i=0;i<n;i++){let e=Math.min(n-1,i+windowDays-1); let sum=0; for(let j=i;j<=e;j++){sum+=(flags[j]||0) | (planned[j]||0);} if(sum>best) best=sum;} return best;}\n"
        "function remainingFrom(startIdx){let n=flags.length; let e=Math.min(n-1,startIdx+windowDays-1); let sum=0; for(let j=startIdx;j<=e;j++){sum+=(flags[j]||0) | (planned[j]||0);} return Math.max(0, maxDays - sum);}\n"
        "function updateMetrics(){\n"
        "  const w = worstWindow();\n"
        "  document.getElementById('worst').textContent = `Worst 12-month: ${w} days`;\n"
        "  document.getElementById('status').textContent = w>maxDays ? 'Status: NOT compliant' : 'Status: Compliant';\n"
        "  const qs = document.getElementById('qstart').value;\n"
        "  if(qs){ const d = new Date(qs); const idx = Math.floor((d - baseDate)/(24*3600*1000)); if(idx>=0){ const rem = remainingFrom(idx); document.getElementById('remaining').textContent = `Remaining from ${qs}: ${rem}`; } }\n"
        "}\n"
        "function applyClasses(){\n"
        "  document.querySelectorAll('.cell[data-idx]').forEach(el => {\n"
        "    const i = parseInt(el.getAttribute('data-idx'));\n"
        "    const abroad = flags[i]===1;\n"
        "    const plan = planned[i]===1;\n"
        "    let cls = 'home';\n"
        "    if(abroad){ cls = 'abroad'; } else if(plan){ cls = 'planned'; } else {\n"
        "      const d = new Date(baseDate.getTime() + i*24*3600*1000);\n"
        "      const now = new Date(); now.setHours(0,0,0,0);\n"
        "      if(d > now){ cls = 'future'; }\n"
        "    }\n"
        "    el.className = 'cell ' + cls;\n"
        "  });\n"
        "}\n"
        "document.addEventListener('click', (e)=>{\n"
        "  const el = e.target.closest('.cell[data-idx]'); if(!el) return;\n"
        "  const i = parseInt(el.getAttribute('data-idx'));\n"
        "  if(flags[i]===1) return; // cannot toggle base abroad\n"
        "  planned[i] = planned[i] ? 0 : 1;\n"
        "  applyClasses(); updateMetrics();\n"
        "});\n"
        "document.getElementById('qstart').addEventListener('change', updateMetrics);\n"
        "document.getElementById('addPlan').addEventListener('click', ()=>{\n"
        "  const ps = document.getElementById('pstart').value; const pe = document.getElementById('pend').value; if(!ps||!pe) return;\n"
        "  const ds = new Date(ps); const de = new Date(pe); if(de<ds) return;\n"
        "  const startIdx = Math.max(0, Math.floor((ds - baseDate)/(24*3600*1000)));\n"
        "  const endIdx = Math.min(flags.length-1, Math.floor((de - baseDate)/(24*3600*1000)));\n"
        "  for(let i=startIdx;i<=endIdx;i++){ if(flags[i]!==1) planned[i]=1; }\n"
        "  applyClasses(); updateMetrics();\n"
        "});\n"
        "document.getElementById('clearPlan').addEventListener('click', ()=>{\n"
        "  planned.fill(0); applyClasses(); updateMetrics();\n"
        "});\n"
        "applyClasses(); updateMetrics();\n"
    )
    html_parts.append("</script>")
    html_parts.append("</body></html>")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(html_parts))


def ensure_unique_path(desired_path: str) -> str:
    """If desired_path exists, append an incrementing suffix before extension."""
    if not os.path.exists(desired_path):
        return desired_path
    root, ext = os.path.splitext(desired_path)
    i = 1
    while True:
        candidate = f"{root}_{i}{ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1


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
    parser.add_argument(
        "--visualize",
        dest="visualize",
        choices=["none", "terminal", "html"],
        default="none",
        help="Produce a visualization: 'terminal' or 'html' (writes --html-out)",
    )
    parser.add_argument(
        "--html-out",
        dest="html_out",
        default="abroad_days.html",
        help="Output path for HTML visualization (when --visualize html)",
    )
    parser.add_argument(
        "--open-html",
        dest="open_html",
        action="store_true",
        help="Open generated HTML in the default browser",
    )
    parser.add_argument(
        "--overwrite",
        dest="overwrite",
        action="store_true",
        help="Allow overwriting existing HTML output file",
    )
    # Planned trip
    parser.add_argument("--plan-start", dest="plan_start", help="Planned trip start date (YYYY-MM-DD)")
    parser.add_argument("--plan-end", dest="plan_end", help="Planned trip end date (YYYY-MM-DD)")
    parser.add_argument(
        "--plan-length",
        dest="plan_length",
        type=int,
        help="Planned trip length in days (inclusive). Use with --plan-start (or --plan-from)",
    )
    parser.add_argument(
        "--plan-from",
        dest="plan_from",
        help="When using --plan-length without --plan-start, start from this date (default: today)",
    )
    # Remaining days query
    parser.add_argument(
        "--query-start",
        dest="query_start",
        help="Start date of a 12-month window to compute remaining allowed days",
    )

    args = parser.parse_args(argv)

    period: Period = (parse_date(args.from_date), parse_date(args.to_date))
    if period[1] < period[0]:
        raise SystemExit("Assessment period end must not be before start")

    if args.csv_path:
        trips = read_trips_from_csv(args.csv_path)
    else:
        trips = read_trips_interactive()

    # Apply planned trip if provided
    planned: Optional[Trip] = None
    if args.plan_start and args.plan_end:
        planned = Trip(parse_date(args.plan_start), parse_date(args.plan_end))
    elif args.plan_length is not None:
        if args.plan_start:
            start_planned = parse_date(args.plan_start)
        else:
            start_planned = parse_date(args.plan_from) if args.plan_from else date.today()
        planned = Trip(start_planned, start_planned + timedelta(days=max(0, args.plan_length - 1)))
    if planned is not None:
        trips = list(trips) + [planned]

    is_ok, violations = check_abroad_days(
        trips=trips,
        assessment_period=period,
        window_days=args.window_days,
        max_allowed=args.max_days,
    )

    if is_ok:
        print("Compliant: No 12-month window exceeds the allowed abroad days.")
    else:
        print("NOT compliant: Some 12-month windows exceed the allowed abroad days.")
        # Show up to 10 violating windows, with the worst first
        violations_sorted = sorted(violations, key=lambda w: w[2], reverse=True)
        limit = min(10, len(violations_sorted))
        print(f"Top {limit} violating windows (start to end: days_abroad):")
        for ws, we, days in violations_sorted[:limit]:
            print(f"- {ws.isoformat()} to {we.isoformat()}: {days} days")

    # Remaining days query
    if args.query_start:
        q_start = parse_date(args.query_start)
        window_days = args.window_days
        # Build flags for the whole period (with planned if any)
        clamped = [t for t in (clamp_trip_to_period(t, period) for t in trips) if t]
        merged = merge_overlapping_trips(clamped)
        flags, base = build_abroad_day_flags(merged, period)
        if q_start < base:
            q_start = base
        q_end = min(q_start + timedelta(days=window_days - 1), period[1])
        si = (q_start - base).days
        abroad_in_q = sum_in_window(flags, si, window_days)
        remaining = max(0, args.max_days - abroad_in_q)
        print(f"Remaining allowed abroad days from {q_start.isoformat()} to {q_end.isoformat()}: {remaining}")

    # Visualization
    if args.visualize != "none":
        clamped = [t for t in (clamp_trip_to_period(t, period) for t in trips) if t]
        merged = merge_overlapping_trips(clamped)
        flags, base = build_abroad_day_flags(merged, period)
        today = date.today()
        if args.visualize == "terminal":
            render_terminal_grid(flags, base, period[1], today)
        elif args.visualize == "html":
            # Decide output filename: if default name, create a contextual, timestamped name
            desired = args.html_out
            if desired == "abroad_days.html":
                base_name = f"abroad_{period[0].isoformat()}_{period[1].isoformat()}"
                if planned is not None:
                    plan_len = (planned.end - planned.start).days + 1
                    base_name += f"_plan-{planned.start.isoformat()}-{plan_len}d"
                if args.query_start:
                    try:
                        qd = parse_date(args.query_start)
                        base_name += f"_query-{qd.isoformat()}"
                    except Exception:
                        pass
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                desired = f"{base_name}_{timestamp}.html"
            if not args.overwrite:
                desired = ensure_unique_path(desired)
            render_html_grid(flags, base, period[1], today, desired)
            print(f"Wrote HTML visualization to {desired}")
            if args.open_html:
                try:
                    webbrowser.open(desired)
                except Exception:
                    pass

    return 0 if is_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())


