#!/usr/bin/env python3
"""Regenerates the LsETH Carry Fund page from live Morpho data. Run in place:
    python3 build_dashboard.py [outfile]

Layout and design system follow KPK's BTC Carry Fund board (assets/kpk-*.css).
Editable copy and the agent settings the page states live in config.json.
"""
import base64
import html
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
# Overridable so CI can write index.html directly (GitHub Pages serves that name)
# without a copy step. Defaults to dashboard.html for local runs.
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "dashboard.html"
CONFIG = json.loads((HERE / "config.json").read_text())
ETHERSCAN = "https://etherscan.io"

sys.path.insert(0, str(HERE))
import position_tracker as pt  # noqa: E402

data = pt.fetch_position()
hist = pt.fetch_apy_history()
start = pt.fetch_strategy_start()
lseth_apy = pt.fetch_lseth_staking_apy()


def esc(x):
    return html.escape(str(x), quote=True)


def utc(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def utc_ts(text):
    """Epoch seconds for a 'YYYY-MM-DD HH:MM' UTC string."""
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())


def sgn(x, dp=2):
    """Signed number with an ASCII minus, never U+2212."""
    return f"{x:+.{dp}f}"


def money(x):
    return ("-$" if x < 0 else "$") + f"{abs(x):,.0f}"


def nice_step(raw_max):
    for step in (0.5, 1, 2, 5, 10, 20):
        if raw_max / step <= 7:
            return step
    return 50


def percentile(values, pct):
    s = sorted(values)
    idx = min(len(s) - 1, int(len(s) * pct / 100))
    return s[idx]


def build_chart(hist, start=None):
    # The chart starts when the position opened, not at the market launch: the
    # earlier market and vault history is not the fund's track record.
    t_open = start["timestamp"] if start else 0

    def since_open(series):
        return [p for p in series if p["t"] >= t_open]

    borrow = since_open(hist["borrow_apy_pct"])
    yieldp = since_open(hist["yield_apy_pct"])
    # Second deploy vault; empty until its launch date, so every use below
    # guards on it and the chart degrades to the two-line version cleanly.
    rwap = since_open(hist.get("rwa_apy_pct") or [])
    blendp = since_open(hist.get("blended_apy_pct") or [])

    # The axis starts at the opening itself; the first daily sample lands within a day of it.
    x0 = t_open if start else min(borrow[0]["t"], yieldp[0]["t"])
    x1 = max(borrow[-1]["t"], yieldp[-1]["t"])

    # The vault's net APY was volatile (6-25%) for roughly its first two
    # weeks on thin TVL before settling near 6-7.5% -- scaling the axis to
    # that transient would flatten the whole rest of the series. The 90th
    # percentile is a robust cap: it rides with the steady-state band
    # regardless of exactly how long the noisy stretch turns out to be, no
    # hardcoded day count. True values still render/label/tooltip correctly;
    # see the cap note in the caption below.
    y_max_core = max(max(p["y"] for p in borrow), percentile([p["y"] for p in yieldp] + [p["y"] for p in rwap], 90))
    step = nice_step(y_max_core * 1.15)
    y_max = step * (int(y_max_core * 1.15 / step) + 1)
    y_min = 0

    pad_l, pad_r, pad_t, pad_b = 44, 56, 12, 30
    W, H = 840, 300
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b

    def sx(t):
        return pad_l + (t - x0) / (x1 - x0) * plot_w

    def sy(y):
        y = min(y, y_max)  # clamp for pixel placement only; true value kept for labels/tooltip
        return pad_t + (1 - (y - y_min) / (y_max - y_min)) * plot_h

    def path_for(series):
        return " ".join(f"{'M' if i == 0 else 'L'}{sx(p['t']):.1f},{sy(p['y']):.1f}" for i, p in enumerate(series))

    # y gridlines / ticks
    ticks = []
    n = int(y_max / step)
    for i in range(n + 1):
        yv = i * step
        ticks.append((sy(yv), yv))

    # x ticks: 5 evenly spaced dates across the domain
    x_ticks = []
    for i in range(5):
        t = x0 + (x1 - x0) * i / 4
        x_ticks.append((sx(t), utc(t).strftime("%-d %b")))

    clipped = [p for p in yieldp + rwap if p["y"] > y_max]
    cap_note = ""
    if clipped:
        peak = max(clipped, key=lambda p: p["y"])
        span_start = utc(min(p["t"] for p in clipped)).strftime("%-d %b")
        span_end = utc(max(p["t"] for p in clipped)).strftime("%-d %b")
        clipped_days = len({utc(p["t"]).strftime("%Y-%m-%d") for p in clipped})
        # Don't assert a cause here. These spikes were checked against vault TVL
        # and allocation history: the vault was ~6 months old and held $3M+ on the
        # first one, so they are NOT a seeding artefact, and no forceDeallocate
        # events exist on the vault. They are single-day jumps in a share-price-
        # derived APY, which a short lookback amplifies. State what is observed.
        cap_note = (
            f"The axis is capped at {y_max:g}%; vault rates were above it on {clipped_days} days "
            f"({span_start} to {span_end}), peak {peak['y']:.0f}%."
        )

    # The blended rate is what the fund earns, so it is the headline line against
    # the borrow rate; the two vaults are thin context lines without end labels.
    # Before the fund moved into the RWA vault the blend equals KPK USDC Yield.
    head = blendp or yieldp
    borrow_end = borrow[-1]
    head_end = head[-1]
    # End labels sit in the right margin, beside their dots, spread at least 15px
    # apart in value order so near-equal rates never overprint each other.
    ends = {"b": sy(borrow_end["y"]), "h": sy(head_end["y"])}
    if ends["h"] - ends["b"] < 15 and ends["b"] - ends["h"] < 15:
        lo, hi = sorted(ends, key=lambda k: ends[k])
        ends[hi] = ends[lo] + 15
    ey_b, ey_h = ends["b"], ends["h"]

    def context_line(series, color):
        if not series:
            return ""
        return (f'<path d="{path_for(series)}" fill="none" stroke="{color}" stroke-width="1.5" opacity="0.6" '
                f'stroke-linejoin="round" stroke-linecap="round"/>')

    grid_svg = "\n".join(
        f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W - pad_r}" y2="{y:.1f}" class="chart-grid"/>'
        f'<text x="{pad_l - 8}" y="{y:.1f}" class="chart-ytick" text-anchor="end" dominant-baseline="middle">{yv:g}%</text>'
        for y, yv in ticks
    )
    xtick_svg = "\n".join(
        f'<text x="{x:.1f}" y="{H - pad_b + 18:.1f}" class="chart-xtick" text-anchor="middle">{label}</text>'
        for x, label in x_ticks
    )

    svg = f"""
    <svg viewBox="0 0 {W} {H}" id="rateChart" class="chart-svg" role="img" aria-label="Borrow APY and the fund's blended KPK USDC vault yield, daily, since the position opened">
      <g>{grid_svg}</g>
      <g>{xtick_svg}</g>
      {context_line(yieldp, "var(--c-yield)")}
      {context_line(rwap, "var(--c-rwa)")}
      <path d="{path_for(borrow)}" fill="none" stroke="var(--c-borrow)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <path d="{path_for(head)}" fill="none" stroke="var(--kpk-ink)" stroke-width="2.75" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="{sx(borrow_end['t']):.1f}" cy="{ey_b:.1f}" r="4" fill="var(--c-borrow)" stroke="var(--kpk-surface-card)" stroke-width="2"/>
      <circle cx="{sx(head_end['t']):.1f}" cy="{ey_h:.1f}" r="4" fill="var(--kpk-ink)" stroke="var(--kpk-surface-card)" stroke-width="2"/>
      <text x="{sx(borrow_end['t']) + 9:.1f}" y="{ey_b:.1f}" text-anchor="start" dominant-baseline="middle" class="chart-endlabel" fill="var(--c-borrow)">{borrow_end['y']:.2f}%</text>
      <text x="{sx(head_end['t']) + 9:.1f}" y="{ey_h:.1f}" text-anchor="start" dominant-baseline="middle" class="chart-endlabel" fill="var(--kpk-ink)">{head_end['y']:.2f}%</text>
      <rect id="hoverRect" x="{pad_l}" y="{pad_t}" width="{plot_w}" height="{plot_h}" fill="transparent"/>
      <line id="crosshair" x1="0" y1="{pad_t}" x2="0" y2="{pad_t + plot_h}" class="chart-crosshair" visibility="hidden"/>
    </svg>
    """

    table_rows = []
    # Key on the UTC date, not the raw timestamp: the market's series ends with
    # a partial-day sample stamped at the current wall-clock second, which
    # never equals the vault's midnight-aligned stamp. Matching on timestamp
    # emitted a duplicate row for today with no yield; matching on date
    # pairs them, and keeping the newest sample per date lets the live partial
    # point supersede that date's earlier midnight one.
    yield_by_date = {}
    for p in yieldp:
        yield_by_date[utc(p["t"]).strftime("%Y-%m-%d")] = p["y"]
    rwa_by_date = {}
    for p in rwap:
        rwa_by_date[utc(p["t"]).strftime("%Y-%m-%d")] = p["y"]
    blend_by_date = {}
    for p in blendp:
        blend_by_date[utc(p["t"]).strftime("%Y-%m-%d")] = p["y"]

    def cell(v, fmt="{:.2f}%"):
        return fmt.format(v) if v is not None else "n/a"

    seen_dates = set()
    for p in sorted(borrow, key=lambda p: p["t"], reverse=True):
        d = utc(p["t"]).strftime("%Y-%m-%d")
        if d in seen_dates:
            continue
        seen_dates.add(d)
        bv = blend_by_date.get(d)
        delta_v = (bv - p["y"]) if bv is not None else None
        table_rows.append(
            f'<tr><td class="nowrap">{d}</td><td class="num">{p["y"]:.2f}%</td>'
            f'<td class="num">{cell(yield_by_date.get(d))}</td><td class="num">{cell(rwa_by_date.get(d))}</td>'
            f'<td class="num"><b>{cell(bv)}</b></td><td class="num">{cell(delta_v, "{:+.2f} pp")}</td></tr>'
        )

    # Left newest-first: the current rate is what a reader opening this table
    # wants, and it's the row that corroborates the headline above.

    borrow_js = json.dumps([[p["t"], round(p["y"], 4)] for p in borrow])
    yield_js = json.dumps([[p["t"], round(p["y"], 4)] for p in yieldp])
    rwa_js = json.dumps([[p["t"], round(p["y"], 4)] for p in rwap])
    blend_js = json.dumps([[p["t"], round(p["y"], 4)] for p in blendp])

    return {
        "svg": svg,
        "table_rows": "\n".join(table_rows),
        "borrow_js": borrow_js,
        "yield_js": yield_js,
        "rwa_js": rwa_js,
        "blend_js": blend_js,
        "cap_note": cap_note,
        "scale": {"x0": x0, "x1": x1, "y_min": y_min, "y_max": y_max,
                  "pad_l": pad_l, "plot_w": plot_w, "pad_t": pad_t, "plot_h": plot_h},
    }


chart = build_chart(hist, start)


# --- Share price in LsETH ------------------------------------------------------------------------
#
# share_price.jsonl holds one reading per line: t (UTC epoch), b (block), c (share price implied by
# KPK's NAV Calculator), r (share price the vault has recorded), s (share supply). It was backfilled
# once from the hourly NAV samples in karpatkey/curation-dune (scripts/upshift_lseth_nav_samples.json,
# read by archive RPC there). Each build appends one more reading from a public RPC, so CI needs
# no key; the workflow commits the file.
SHARES_PATH = HERE / "share_price.jsonl"
SHARE_CFG = CONFIG["share_chart"]
HALF_HOUR = 1800


def load_share_rows():
    """The committed readings, oldest first. A bad line is skipped, never fatal."""
    rows = []
    if SHARES_PATH.exists():
        for line in SHARES_PATH.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return sorted(rows, key=lambda r: r["t"])


def append_share_reading(rows):
    """Append one live reading to the file and to rows, unless it is stale, bad or unreachable."""
    try:
        reading = pt.fetch_share_price_reading(
            CONFIG["contracts"]["vault"], CONFIG["contracts"]["shares"], CONFIG["contracts"]["nav_calculator"])
    except Exception as e:  # a failed read must never fail the build
        print(f"share price reading skipped: {e}", file=sys.stderr)
        return
    if not reading or (rows and (reading["b"] <= rows[-1]["b"] or reading["t"] - rows[-1]["t"] < HALF_HOUR)):
        return
    reading = {"t": reading["t"], "b": reading["b"], "c": round(reading["c"], 9),
               "r": round(reading["r"], 9), "s": round(reading["s"], 6)}
    with SHARES_PATH.open("a") as f:
        f.write(json.dumps(reading, separators=(",", ":")) + "\n")
    rows.append(reading)


def build_share_chart(rows, start):
    """Share price in LsETH since the position opened: the vault's recorded price (a step at each
    NAV update) against the price implied by KPK's NAV Calculator (continuous)."""
    t_open = start["timestamp"] if start else 0
    rows = [r for r in rows if r["t"] >= t_open]
    if len(rows) < 2:
        return None

    x0, x1 = rows[0]["t"], rows[-1]["t"]
    lo = min(min(r["c"], r["r"]) for r in rows)
    hi = max(max(r["c"], r["r"]) for r in rows)
    room = max(hi - lo, 1e-5) * 0.12
    y_lo, y_hi = lo - room, hi + room
    raw = (y_hi - y_lo) / 5
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    decimals = max(3, math.ceil(-math.log10(step)))
    tick_vals = [k * step for k in range(math.ceil(y_lo / step), math.floor(y_hi / step) + 1)]

    pad_l, pad_r, pad_t, pad_b = 60, 64, 16, 30
    W, H = 840, 300
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b

    def sx(t):
        return pad_l + (t - x0) / (x1 - x0) * plot_w

    def sy(v):
        return pad_t + (1 - (v - y_lo) / (y_hi - y_lo)) * plot_h

    grid = "\n".join(
        f'<line x1="{pad_l}" y1="{sy(v):.1f}" x2="{W - pad_r}" y2="{sy(v):.1f}" class="chart-grid"/>'
        f'<text x="{pad_l - 8}" y="{sy(v):.1f}" class="chart-ytick" text-anchor="end" dominant-baseline="middle">{v:.{decimals}f}</text>'
        for v in tick_vals)
    xticks = "\n".join(
        f'<text x="{sx(x0 + (x1 - x0) * i / 4):.1f}" y="{H - pad_b + 18:.1f}" class="chart-xtick" text-anchor="middle">'
        f'{utc(x0 + (x1 - x0) * i / 4).strftime("%-d %b")}</text>' for i in range(5))

    # The NAV Calculator line, one point per reading, thinned to one per 6 hours for the page.
    calc, last_kept = [], None
    for r in rows:
        if last_kept is None or r["t"] - last_kept >= 6 * 3600:
            calc.append([r["t"], r["c"]])
            last_kept = r["t"]
    if calc[-1][0] != rows[-1]["t"]:
        calc.append([rows[-1]["t"], rows[-1]["c"]])

    # The recorded price holds its value until the next NAV update: a staircase.
    steps = [[rows[0]["t"], rows[0]["r"]]]
    for r in rows[1:]:
        if r["r"] != steps[-1][1]:
            steps.append([r["t"], r["r"]])
    d = [f"M{sx(steps[0][0]):.1f},{sy(steps[0][1]):.1f}"]
    for (_, prev), (t, v) in zip(steps, steps[1:]):
        d.append(f"L{sx(t):.1f},{sy(prev):.1f} L{sx(t):.1f},{sy(v):.1f}")
    d.append(f"L{sx(x1):.1f},{sy(steps[-1][1]):.1f}")
    rec_path = " ".join(d)
    calc_path = " ".join(f"{'M' if i == 0 else 'L'}{sx(t):.1f},{sy(v):.1f}" for i, (t, v) in enumerate(calc))

    # Marks: the dated events from config, plus any subscription or redemption that moved the supply.
    marks = [(utc_ts(e["t"]), e["label"]) for e in SHARE_CFG["events"]]
    min_flow = SHARE_CFG["min_flow_pct"] / 100
    for prev, cur in zip(rows, rows[1:]):
        if prev["s"] > 0 and abs(cur["s"] / prev["s"] - 1) >= min_flow:
            amount = abs(cur["s"] - prev["s"]) * cur["c"]
            marks.append((cur["t"], f"{amount:.0f} LsETH {'subscribed' if cur['s'] > prev['s'] else 'redeemed'}"))
    mark_svg = ""
    for t, label in sorted(marks):
        if x0 <= t <= x1:
            x = sx(t)
            mark_svg += (
                f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{pad_t + plot_h}" class="chart-mark"/>'
                f'<text class="chart-marklabel" transform="rotate(-90 {x - 5:.1f} {pad_t + 6})" '
                f'x="{x - 5:.1f}" y="{pad_t + 6}" text-anchor="end">{esc(label)}</text>')

    # End values: nearly equal, so spread the two labels apart in value order.
    ends = {"c": sy(rows[-1]["c"]), "r": sy(rows[-1]["r"])}
    upper, lower = sorted(ends, key=lambda k: ends[k])
    if ends[lower] - ends[upper] < 15:
        ends[lower] = ends[upper] + 15
    end_svg = ""
    for key, color in (("c", "var(--c-good)"), ("r", "var(--kpk-ink)")):
        # Coloured by inline style, which beats the grey `.chart-svg text` rule; the two ends are
        # a few pixels apart, so a dot each would overprint and the colour carries the match.
        end_svg += (
            f'<text x="{sx(x1) + 9:.1f}" y="{ends[key]:.1f}" text-anchor="start" dominant-baseline="middle" '
            f'class="chart-endlabel" style="fill:{color};font-weight:600">{rows[-1][key]:.5f}</text>')

    svg = f"""
    <svg viewBox="0 0 {W} {H}" id="shareChart" class="chart-svg" role="img" aria-label="Share price in LsETH since the position opened: the vault's recorded price against the price from KPK's NAV Calculator">
      <g>{grid}</g>
      <g>{xticks}</g>
      {mark_svg}
      <path d="{calc_path}" fill="none" stroke="var(--c-good)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <path d="{rec_path}" fill="none" stroke="var(--kpk-ink)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      {end_svg}
      <rect id="shareHover" x="{pad_l}" y="{pad_t}" width="{plot_w}" height="{plot_h}" fill="transparent"/>
      <line id="shareCrosshair" x1="0" y1="{pad_t}" x2="0" y2="{pad_t + plot_h}" class="chart-crosshair" visibility="hidden"/>
    </svg>
    """
    return {"svg": svg, "calc_js": json.dumps([[t, round(v, 7)] for t, v in calc]),
            "rec_js": json.dumps([[t, round(v, 7)] for t, v in steps]),
            "scale": {"x0": x0, "x1": x1, "pad_l": pad_l, "plot_w": plot_w}}


share_rows = load_share_rows()
append_share_reading(share_rows)
share_chart = build_share_chart(share_rows, start)

REBALANCE_CFG = CONFIG["rebalance_chart"]


def load_rebalance_history():
    """The wallet's Borrow and Repay history and the hourly oracle price. A failed read must
    never fail the build: the chart is simply left out until the next run."""
    try:
        return pt.fetch_rebalance_history(data["wallet"])
    except Exception as e:
        print(f"rebalance history skipped: {e}", file=sys.stderr)
        return None


def thin_hours(points):
    """One reading per six hours, plus the hours around any step in the liquidation price (a
    Borrow, Repay or collateral change), so the steps stay sharp while the page stays light."""
    def moved(a, b):
        if (a["liq"] is None) != (b["liq"] is None):
            return True
        return a["liq"] is not None and abs(b["liq"] / a["liq"] - 1) > 0.002

    jump = [i > 0 and moved(points[i - 1], p) for i, p in enumerate(points)]
    return [p for i, p in enumerate(points)
            if i % 6 == 0 or i == len(points) - 1 or jump[i] or (i + 1 < len(points) and jump[i + 1])]


def price_at(points, t):
    """Oracle price at time t, linear between the two hourly readings around it."""
    if t <= points[0]["t"]:
        return points[0]["px"]
    if t >= points[-1]["t"]:
        return points[-1]["px"]
    lo, hi = 0, len(points) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if points[mid]["t"] <= t else (lo, mid)
    a, b = points[lo], points[hi]
    return a["px"] + (b["px"] - a["px"]) * (t - a["t"]) / (b["t"] - a["t"])


def liq_at(points, t):
    """Liquidation price on the drawn line at time t, linear between the two readings around it;
    None outside the readings or where either reading has no debt."""
    for a, b in zip(points, points[1:]):
        if a["t"] <= t <= b["t"]:
            if a["liq"] is None or b["liq"] is None:
                return None
            return a["liq"] + (b["liq"] - a["liq"]) * (t - a["t"]) / (b["t"] - a["t"])
    return None


def band_spans(bands, points):
    """Each configured health factor band as the stretch of the chart it covers, [from, to, repay
    trigger, borrow trigger] in epoch seconds, cut to the readings. An open-ended band runs to the
    latest reading."""
    t0, t1 = points[0]["t"], points[-1]["t"]
    spans = []
    for b in bands:
        a = max(utc_ts(b["from"]), t0)
        z = min(utc_ts(b["to"]), t1) if b.get("to") else t1
        if z > a:
            spans.append([a, z, b["repay_below"], b["borrow_above"]])
    return sorted(spans)


def uncovered(spans, t0, t1, min_gap=6 * 3600):
    """The stretches of [t0, t1] that no band covers, ignoring any shorter than min_gap seconds."""
    gaps, cursor = [], t0
    for a, z, _, _ in spans:
        if a - cursor >= min_gap:
            gaps.append((cursor, a))
        cursor = max(cursor, z)
    if t1 - cursor >= min_gap:
        gaps.append((cursor, t1))
    return gaps


def build_rebalance_chart(history, start):
    """LsETH oracle price in USD since the position opened, against the liquidation price of the loan,
    the no-action corridor (liquidation price x the health factor band) and each Borrow and Repay
    the health factor manager sent. Prices come from the market's oracle through the Morpho API; the
    actions are the trading subaccount's own transactions on the LsETH/USDC market."""
    if not history or len(history["points"]) < 2:
        return None
    all_pts = history["points"]
    actions = history["actions"]
    t_open = start["timestamp"] if start else actions[0]["t"]
    x0, x1 = min(t_open, all_pts[0]["t"]), all_pts[-1]["t"]
    pts = thin_hours(all_pts)
    spans = band_spans(REBALANCE_CFG["bands"], pts)
    live = CONFIG["health_factor_manager"]
    if spans and spans[-1][2:] != [live["repay_below"], live["borrow_above"]]:
        print("rebalance chart: the latest band differs from health_factor_manager", file=sys.stderr)

    def band_edges(span):
        """The band's own stretch as (time, liquidation price x repay trigger, x borrow trigger)."""
        a, z, lo, hi = span
        inner = [(p["t"], p["liq"]) for p in pts if a < p["t"] < z]
        line = [(a, liq_at(pts, a))] + inner + [(z, liq_at(pts, z))]
        return [(t, v * lo, v * hi) for t, v in line if v is not None]

    corridors = [band_edges(sp) for sp in spans]
    values = [p["px"] for p in pts] + [p["liq"] for p in pts if p["liq"] is not None]
    values += [e[2] for c in corridors for e in c]
    y_lo, y_hi = min(values) * 0.97, max(values) * 1.03
    raw = (y_hi - y_lo) / 5
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    tick_vals = [k * step for k in range(math.ceil(y_lo / step), math.floor(y_hi / step) + 1)]

    pad_l, pad_r, pad_t, pad_b = 60, 78, 16, 30
    W, H = 840, 300
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b

    def sx(t):
        return pad_l + (t - x0) / (x1 - x0) * plot_w

    def sy(v):
        return pad_t + (1 - (v - y_lo) / (y_hi - y_lo)) * plot_h

    grid = "\n".join(
        f'<line x1="{pad_l}" y1="{sy(v):.1f}" x2="{W - pad_r}" y2="{sy(v):.1f}" class="chart-grid"/>'
        f'<text x="{pad_l - 8}" y="{sy(v):.1f}" class="chart-ytick" text-anchor="end" dominant-baseline="middle">${v:,.0f}</text>'
        for v in tick_vals)
    xticks = "\n".join(
        f'<text x="{sx(x0 + (x1 - x0) * i / 4):.1f}" y="{H - pad_b + 18:.1f}" class="chart-xtick" text-anchor="middle">'
        f'{utc(x0 + (x1 - x0) * i / 4).strftime("%-d %b")}</text>' for i in range(5))

    def poly_path(series):
        """Path through (t, value) pairs, broken wherever the value is missing."""
        d, pen_down = [], False
        for t, v in series:
            if v is None:
                pen_down = False
                continue
            d.append(f"{'L' if pen_down else 'M'}{sx(t):.1f},{sy(v):.1f}")
            pen_down = True
        return " ".join(d)

    # The corridor: where the position sits inside the health factor band, so the agent does nothing.
    # Each band is drawn for the stretch its own settings were in force; stretches with no band on
    # record are greyed.
    corridor_svg = ""
    for c in corridors:
        if len(c) < 2:
            continue
        upper = [(t, hi) for t, _, hi in c]
        lower = [(t, lo) for t, lo, _ in c]
        ring = " ".join(f"{sx(t):.1f},{sy(v):.1f}" for t, v in upper + lower[::-1])
        corridor_svg += (f'<polygon class="rb-band" points="{ring}"/>'
                         f'<path class="rb-band-edge" d="{poly_path(upper)}"/><path class="rb-band-edge" d="{poly_path(lower)}"/>')
    phase_svg = ""
    for ga, gz in uncovered(spans, pts[0]["t"], x1):
        xa, xz = sx(ga), sx(gz)
        mid = (xa + xz) / 2
        phase_svg += (f'<rect class="rb-phase" x="{xa:.1f}" y="{pad_t}" width="{xz - xa:.1f}" height="{plot_h}"/>'
                      f'<text x="{mid:.1f}" y="{pad_t + plot_h - 8}" class="chart-marklabel" '
                      f'transform="rotate(-90 {mid:.1f} {pad_t + plot_h - 8})">no band in force</text>')

    liq_path = poly_path([(p["t"], p["liq"]) for p in pts])
    px_path = poly_path([(p["t"], p["px"]) for p in pts])

    # Collateral added or removed: a dashed line. Borrow and Repay: a triangle on the price line,
    # up for a borrow (re-lever), down for a repay. The opening Borrow: a diamond.
    flow_svg = mark_svg = ""
    for a in actions:
        if not x0 <= a["t"] <= x1:
            continue
        x, y = sx(a["t"]), sy(price_at(all_pts, a["t"]))
        if a["kind"] in ("deposit", "withdraw"):
            flow_svg += f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{pad_t + plot_h}" class="chart-mark"/>'
        elif a["kind"] == "relever":
            mark_svg += f'<path class="rb-mk relever" d="M{x:.1f},{y - 7:.1f} L{x + 6.5:.1f},{y + 5:.1f} L{x - 6.5:.1f},{y + 5:.1f} Z"/>'
        elif a["kind"] == "derisk":
            mark_svg += f'<path class="rb-mk derisk" d="M{x:.1f},{y + 7:.1f} L{x + 6.5:.1f},{y - 5:.1f} L{x - 6.5:.1f},{y - 5:.1f} Z"/>'
        else:
            mark_svg += f'<path class="rb-mk open" d="M{x:.1f},{y - 7:.1f} L{x + 7:.1f},{y:.1f} L{x:.1f},{y + 7:.1f} L{x - 7:.1f},{y:.1f} Z"/>'

    last = pts[-1]
    end_svg = (f'<text x="{sx(last["t"]) + 9:.1f}" y="{sy(last["px"]):.1f}" dominant-baseline="middle" class="chart-endlabel" '
               f'style="fill:var(--c-line);font-weight:600">${last["px"]:,.0f}</text>')
    if last["liq"] is not None:
        end_svg += (f'<text x="{sx(last["t"]) + 9:.1f}" y="{sy(last["liq"]):.1f}" dominant-baseline="middle" class="chart-endlabel" '
                    f'style="fill:var(--c-liq);font-weight:600">liq. ${last["liq"]:,.0f}</text>')

    svg = f"""
    <svg viewBox="0 0 {W} {H}" id="rbChart" class="chart-svg" role="img" aria-label="LsETH oracle price in USD since the position opened, against the liquidation price, the no-action corridor and the agent's borrows and repays">
      <g>{grid}</g>
      <g>{xticks}</g>
      {phase_svg}{corridor_svg}{flow_svg}
      <path d="{liq_path}" fill="none" stroke="var(--c-liq)" stroke-width="1.5" stroke-dasharray="6 4"/>
      <path d="{px_path}" fill="none" stroke="var(--c-line)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      {mark_svg}{end_svg}
      <rect id="rbHover" x="{pad_l}" y="{pad_t}" width="{plot_w}" height="{plot_h}" fill="transparent"/>
      <line id="rbCrosshair" x1="0" y1="{pad_t}" x2="0" y2="{pad_t + plot_h}" class="chart-crosshair" visibility="hidden"/>
    </svg>
    """
    rebalances = [a for a in actions if a["kind"] in ("relever", "derisk")]
    relevers = [a for a in rebalances if a["kind"] == "relever"]
    repays = [a for a in rebalances if a["kind"] == "derisk"]
    return {
        "svg": svg,
        "pts_js": json.dumps([[p["t"], round(p["px"], 1), None if p["liq"] is None else round(p["liq"], 1)] for p in pts],
                             separators=(",", ":")),
        "acts_js": json.dumps([[a["t"], a["kind"], round(a.get("usdc") or a.get("lseth"), 2)] for a in actions
                               if x0 <= a["t"] <= x1], separators=(",", ":")),
        "scale": {"x0": x0, "x1": x1, "pad_l": pad_l, "plot_w": plot_w, "w": W,
                  "bands": spans},
        "price": all_pts[-1]["px"], "price_open": all_pts[0]["px"],
        "n": len(rebalances), "n_relever": len(relevers), "n_repay": len(repays),
        "usdc_borrowed": sum(a["usdc"] for a in relevers), "usdc_repaid": sum(a["usdc"] for a in repays),
        "opened": utc(t_open),
    }


rebalance_chart = build_rebalance_chart(load_rebalance_history(), start)

# --- Design system: the BTC board's tokens, fonts, logo and protocol icons ---------------------

tokens_css = re.sub(r"/\*[\s\S]*?\*/", "", (ASSETS / "kpk-tokens.css").read_text())
fonts_css = re.sub(r"/\*[\s\S]*?\*/", "", (ASSETS / "kpk-fonts.css").read_text())
dark_block = re.search(r':root\[data-theme="dark"\],\s*\.dark\s*\{([\s\S]*?)\}', tokens_css).group(1)
logo_svg = (ASSETS / "kpk-logo.svg").read_text()
LOGOS = {p.stem: "data:image/webp;base64," + base64.b64encode(p.read_bytes()).decode()
         for p in sorted((ASSETS / "logos").glob("*.webp"))}
# Upshift's mark is a rounded square; cropping it to a circle would cut it.
SQUARE_LOGOS = {"upshift"}


def icon(name, size=22):
    src = LOGOS.get(name)
    if not src:
        return ""
    cls = "logo-icon sq" if name in SQUARE_LOGOS else "logo-icon"
    return f'<img class="{cls}" src="{src}" width="{size}" height="{size}" alt="">'


# --- Data ---------------------------------------------------------------------------------------

wallet = data["wallet"]
coll = data["collateral"]
borrow = data["borrow"]
dep = data["deposit"]
ltv = data["ltv_pct"]
lltv = data["lltv_pct"]
lseth_price = coll["price_usd"]
spreads = data["spreads_pp"]
legs_by_horizon = data["legs_by_horizon"]
# Health factor on Morpho is LLTV / LTV; liquidation when the collateral price
# falls by that same ratio.
hf_now = lltv / ltv if ltv > 0 else None
liq_px = lseth_price * ltv / lltv if ltv > 0 else None
per_unit_borrow = lseth_price * ltv / 100

# 10% performance fee on the carry, matching the Phase 1 backtest's `x 0.9`
# (Notion: "lsETH Phase 1 carry -- backtest methodology & results"). Stated
# explicitly on the page because a reader who recomputes spread x LTV without it
# gets a higher number and assumes an error.
PERF_FEE = float(CONFIG["performance_fee"])
net_fee_multiplier = 1 - PERF_FEE

# Live first in the picker. KPK is the sole supplier and sole borrower in this
# market, so the live borrow rate is the one its own automations govern -- that
# makes it the forward-looking basis, not a mere snapshot. The trailing averages
# are the reality check on it.
_SPREAD_LABELS = [
    ("spot", "Live"),
    ("7d", "7d avg"),
    ("30d", "30d avg"),
]
# Trailing spreads, kept only where the API returned a value (a young vault has
# no 30d realized APY yet, so these can legitimately be None).
bases = {
    k: {"label": lbl, "spread": spreads[k],
        "yield": legs_by_horizon[k]["yield_pct"], "borrow": legs_by_horizon[k]["borrow_pct"]}
    for k, lbl in _SPREAD_LABELS
    if spreads.get(k) is not None and legs_by_horizon.get(k)
}
# Default to a trailing basis for the headline: the instantaneous vault rate can
# swing close to a point within an hour (observed 6.41% -> 5.55% in 8 minutes on
# 2026-08-10), which makes the live print a poor first impression for an external
# reader. 7d over 30d (Giel's call, 2026-08-10): the position is days old and its
# market is weeks old, so a 30d window leans on rates from before the position
# existed. Live stays one click away and is still the operationally-governed rate.
_default_basis = "7d" if "7d" in bases else "spot"
B0 = bases[_default_basis]
carry_head = B0["spread"] * ltv / 100 * net_fee_multiplier


def basis_word(label):
    return label.lower()


# Deposit split across the two deploy vaults, from the tracker's per-vault detail.
# Falls back to 100/0 if the position ever consolidates back to one vault.
_vaults = dep.get("vaults", [])
_yield_v = next((v for v in _vaults if "RWA" not in v["vault"]), None)
_rwa_v = next((v for v in _vaults if "RWA" in v["vault"]), None)
yield_w = (_yield_v["weight"] * 100) if _yield_v else 100.0
rwa_w = (_rwa_v["weight"] * 100) if _rwa_v else 0.0

# --- Health factor manager settings (config.json), as LTV at the market's live LLTV ------------

HFM = CONFIG["health_factor_manager"]


def ltv_of(hf):
    return lltv / hf


BAND = {
    "lo": ltv_of(HFM["borrow_above"]),     # above this health factor (below this LTV) it borrows
    "borrow_to": ltv_of(HFM["borrow_to"]),
    "repay_to": ltv_of(HFM["repay_to"]),
    "hi": ltv_of(HFM["repay_below"]),      # below this health factor (above this LTV) it repays
}
TARGET_LTV = float(CONFIG["target_ltv"])

V = {
    "repay_below": f"{HFM['repay_below']:g}",
    "repay_to": f"{HFM['repay_to']:g}",
    "borrow_above": f"{HFM['borrow_above']:g}",
    "borrow_to": f"{HFM['borrow_to']:g}",
    "repay_below_ltv": f"{BAND['hi']:.1f}%",
    "repay_to_ltv": f"{BAND['repay_to']:.1f}%",
    "borrow_above_ltv": f"{BAND['lo']:.1f}%",
    "borrow_to_ltv": f"{BAND['borrow_to']:.1f}%",
    "min_change": f"{HFM['min_change_usdc']:,}",
    "split_yield": f"{HFM['split']['yield']:g}",
    "split_rwa": f"{HFM['split']['rwa']:g}",
    "lltv": f"{lltv:.0f}%",
    "target_ltv": f"{TARGET_LTV:g}%",
    "live_yield": f"{yield_w:.0f}",
    "live_rwa": f"{rwa_w:.0f}",
}


def T(s):
    """Config copy with its {placeholders} filled, HTML-escaped."""
    return esc(s.format(**V))


# --- Position age ------------------------------------------------------------

if start:
    _age_days = max(0, (datetime.now(timezone.utc) - utc(start["timestamp"])).days)
    _opened = utc(start["timestamp"]).strftime("%-d %b %Y at %H:%M UTC")
    position_age_note = (
        f"Position opened {_opened} "
        f"({'today' if _age_days == 0 else f'{_age_days} days ago'}), "
        f"borrowing {start['borrowed_usdc']:,.0f} USDC."
    )
else:
    position_age_note = "No borrow found for this wallet on the LsETH/USDC market."

DOCS = CONFIG["docs"]
CONTRACTS = CONFIG["contracts"]
MAIN_RISKS = CONFIG["main_risks"]
risk_rows = "".join(
    f'<div class="risk-row"><div class="risk-n">{esc(n)}</div><div class="risk-d">{T(d)}</div><div class="risk-h">{T(h)}</div></div>'
    for n, d, h in MAIN_RISKS["rows"])

share_card = ""
if share_chart:
    share_card = f'''<div class="card">
    <div class="stat-label">Share price in LsETH since the position opened</div>
    <div class="legend">
      <span><i style="border-top-color:var(--kpk-ink)"></i>Upshift share price (recorded at each NAV update)</span>
      <span><i style="border-top-color:var(--c-good)"></i>KPK NAV Calculator (live)</span>
    </div>
    <div class="chart">
      {share_chart['svg']}
      <div id="shareTooltip" class="chart-tooltip" role="status">
        <div class="date" id="stDate"></div>
        <div class="row"><span class="key" style="background:var(--c-good)"></span>NAV Calculator<span class="val" id="stCalc"></span></div>
        <div class="row"><span class="key" style="background:var(--kpk-ink)"></span>Upshift recorded<span class="val" id="stRec"></span></div>
      </div>
    </div>
    <div class="stat-sub">The vault records its share price at each NAV update, twice a week, so the Upshift line moves in steps. KPK's NAV can be read onchain at any block, which gives the continuous line.</div>
  </div>'''

share_js = ""
if share_chart:
    share_js = f'''<script>
(function() {{
  var calc = {share_chart['calc_js']};
  var rec = {share_chart['rec_js']};
  var scale = {{ x0: {share_chart['scale']['x0']}, x1: {share_chart['scale']['x1']}, padL: {share_chart['scale']['pad_l']}, plotW: {share_chart['scale']['plot_w']} }};
  var svg = document.getElementById('shareChart');
  var rect = document.getElementById('shareHover');
  var crosshair = document.getElementById('shareCrosshair');
  var tooltip = document.getElementById('shareTooltip');
  if (!svg || !rect) return;

  function nearest(series, t) {{
    var best = series[0];
    for (var i = 1; i < series.length; i++) {{
      if (Math.abs(series[i][0] - t) < Math.abs(best[0] - t)) best = series[i];
    }}
    return best;
  }}
  function recordedAt(t) {{
    var v = rec[0][1];
    for (var i = 0; i < rec.length && rec[i][0] <= t; i++) v = rec[i][1];
    return v;
  }}
  function show(e) {{
    var box = svg.getBoundingClientRect();
    var px = e.clientX - box.left;
    var frac = Math.max(0, Math.min(1, (px * (840 / box.width) - scale.padL) / scale.plotW));
    var p = nearest(calc, scale.x0 + frac * (scale.x1 - scale.x0));
    var xv = (p[0] - scale.x0) / (scale.x1 - scale.x0) * scale.plotW + scale.padL;
    crosshair.setAttribute('x1', xv);
    crosshair.setAttribute('x2', xv);
    crosshair.setAttribute('visibility', 'visible');
    document.getElementById('stDate').textContent = new Date(p[0] * 1000).toLocaleString('en-GB', {{ day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }}) + ' UTC';
    document.getElementById('stCalc').textContent = p[1].toFixed(6);
    document.getElementById('stRec').textContent = recordedAt(p[0]).toFixed(6);
    var cx = xv / 840 * box.width, w = tooltip.offsetWidth || 160;
    tooltip.style.left = Math.max(w / 2, Math.min(box.width - w / 2, cx)) + 'px';
    tooltip.style.visibility = 'visible';
  }}
  rect.addEventListener('pointermove', show);
  rect.addEventListener('pointerdown', show);
  rect.addEventListener('pointerleave', function() {{
    crosshair.setAttribute('visibility', 'hidden');
    tooltip.style.visibility = 'hidden';
  }});
}})();
</script>'''

rebalance_card = ""
if rebalance_chart:
    rb = rebalance_chart
    rb_change = (rb["price"] / rb["price_open"] - 1) * 100
    # The labels are set in capitals by the stylesheet; LsETH keeps its case.
    keep = '<span class="keep-case">LsETH</span>'
    rebalance_card = f'''<div class="card">
    <div class="stat-label">{esc(REBALANCE_CFG["title"]).replace("LsETH", keep)}</div>
    <div class="statrow">
      <div><div class="c-label">{keep} oracle price</div><div class="c-value">${rb["price"]:,.0f}</div><div class="c-sub">{sgn(rb_change, 1)}% since the position opened</div></div>
      <div><div class="c-label">Agent rebalances</div><div class="c-value">{rb["n"]}</div><div class="c-sub">{rb["n_relever"]} re-lever · {rb["n_repay"]} repay, since {rb["opened"].strftime("%-d %b")}</div></div>
      <div><div class="c-label">Re-lever</div><div class="c-value">{rb["n_relever"]}</div><div class="c-sub">{rb["usdc_borrowed"]:,.0f} USDC borrowed, deposited into the KPK USDC vaults</div></div>
      <div><div class="c-label">Repay</div><div class="c-value">{rb["n_repay"]}</div><div class="c-sub">{rb["usdc_repaid"]:,.0f} USDC repaid</div></div>
    </div>
    <div class="legend">
      <span><i style="border-top-color:var(--c-line)"></i>LsETH oracle price</span>
      <span><i class="rb-band-key"></i>no-action corridor</span>
      <span><i class="dash" style="border-top-color:var(--c-liq)"></i>liquidation price</span>
      <span><span class="shape" style="color:var(--c-band)">&#9650;</span>re-lever (borrow)</span>
      <span><span class="shape" style="color:var(--c-liq)">&#9660;</span>repay</span>
      <span><span class="shape" style="color:var(--kpk-ink)">&#9670;</span>position opened</span>
      <span><span class="shape">&#9482;</span>LsETH added</span>
    </div>
    <div class="chart">
      {rb["svg"]}
      <div id="rbTooltip" class="chart-tooltip" role="status">
        <div class="date" id="rbDate"></div>
        <div class="row"><span class="key" style="background:var(--c-line)"></span>LsETH oracle price<span class="val" id="rbPx"></span></div>
        <div class="row" id="rbBandRow"><span class="key" style="background:var(--c-band)"></span>No-action corridor<span class="val" id="rbBand"></span></div>
        <div class="row" id="rbLiqRow"><span class="key" style="background:var(--c-liq)"></span>Liquidation price<span class="val" id="rbLiq"></span></div>
        <div id="rbActs"></div>
      </div>
    </div>
    <div class="stat-sub">{T(REBALANCE_CFG["caption"])}</div>
  </div>'''

rebalance_js = ""
if rebalance_chart:
    sc = rebalance_chart["scale"]
    rebalance_js = f'''<script>
(function() {{
  var pts = {rebalance_chart['pts_js']};
  var acts = {rebalance_chart['acts_js']};
  var S = {{ x0: {sc['x0']}, x1: {sc['x1']}, padL: {sc['pad_l']}, plotW: {sc['plot_w']}, w: {sc['w']}, bands: {json.dumps(sc['bands'])} }};
  var svg = document.getElementById('rbChart');
  var rect = document.getElementById('rbHover');
  var crosshair = document.getElementById('rbCrosshair');
  var tooltip = document.getElementById('rbTooltip');
  if (!svg || !rect) return;
  var LABELS = {{ relever: 'Re-lever: borrowed', derisk: 'Repay: repaid', open: 'Position opened: borrowed' }};

  function usd(v) {{ return '$' + Math.round(v).toLocaleString('en-US'); }}
  function num(v, dp) {{ return v.toLocaleString('en-US', {{ maximumFractionDigits: dp }}); }}
  function nearest(t) {{
    var best = pts[0];
    for (var i = 1; i < pts.length; i++) {{
      if (Math.abs(pts[i][0] - t) < Math.abs(best[0] - t)) best = pts[i];
    }}
    return best;
  }}
  function show(e) {{
    var box = svg.getBoundingClientRect();
    var px = e.clientX - box.left;
    var frac = Math.max(0, Math.min(1, (px * (S.w / box.width) - S.padL) / S.plotW));
    var t = S.x0 + frac * (S.x1 - S.x0);
    var p = nearest(t);
    var xv = (p[0] - S.x0) / (S.x1 - S.x0) * S.plotW + S.padL;
    crosshair.setAttribute('x1', xv);
    crosshair.setAttribute('x2', xv);
    crosshair.setAttribute('visibility', 'visible');
    document.getElementById('rbDate').textContent = new Date(p[0] * 1000).toLocaleString('en-GB', {{ day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }}) + ' UTC';
    document.getElementById('rbPx').textContent = usd(p[1]);
    var band = null;
    S.bands.forEach(function(b) {{ if (p[0] >= b[0] && p[0] <= b[1]) band = b; }});
    var hasLiq = p[2] !== null, hasBand = hasLiq && band !== null;
    document.getElementById('rbLiqRow').style.display = hasLiq ? '' : 'none';
    document.getElementById('rbBandRow').style.display = hasBand ? '' : 'none';
    if (hasLiq) document.getElementById('rbLiq').textContent = usd(p[2]);
    if (hasBand) document.getElementById('rbBand').textContent = usd(p[2] * band[2]) + ' to ' + usd(p[2] * band[3]);
    var box2 = document.getElementById('rbActs');
    while (box2.firstChild) box2.removeChild(box2.firstChild);
    var tol = 7 * (S.x1 - S.x0) / S.plotW;
    acts.forEach(function(a) {{
      if (Math.abs(a[0] - p[0]) > tol) return;
      var row = document.createElement('div');
      row.className = 'act';
      var when = new Date(a[0] * 1000).toLocaleString('en-GB', {{ day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }});
      row.textContent = when + ' UTC, ' + (a[1] === 'deposit' ? num(a[2], 1) + ' LsETH added' : a[1] === 'withdraw' ? num(a[2], 1) + ' LsETH removed' : LABELS[a[1]] + ' ' + num(a[2], 0) + ' USDC');
      box2.appendChild(row);
    }});
    var cx = xv / S.w * box.width, w = tooltip.offsetWidth || 160;
    tooltip.style.left = Math.max(w / 2, Math.min(box.width - w / 2, cx)) + 'px';
    tooltip.style.visibility = 'visible';
  }}
  rect.addEventListener('pointermove', show);
  rect.addEventListener('pointerdown', show);
  rect.addEventListener('pointerleave', function() {{
    crosshair.setAttribute('visibility', 'hidden');
    tooltip.style.visibility = 'hidden';
  }});
}})();
</script>'''

market_url = "https://app.morpho.org/ethereum/market/0xfb7d54e0ce71efc8fffd3f4e1db0afa9265882da5cc76604b62adfac64501e80/lseth-usdc"
debank_url = f"https://debank.com/profile/{wallet}"
# The visible stamp keeps the YYYY-MM-DD HH:MM UTC shape: refresh.yml masks
# exactly that pattern when deciding whether the data moved.
as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
built_epoch = int(datetime.now(timezone.utc).timestamp())
# Deep link to the workflow's own Run button. A real in-page trigger would need a
# GitHub token with `workflow` scope embedded in a PUBLIC page -- i.e. handing
# anyone write access to the repo -- so this hands off to GitHub's UI instead,
# where the click is authenticated as whoever is signed in.
actions_url = "https://github.com/kpk-labs/upshift-lseth-dashboard/actions/workflows/refresh.yml"


def ext(url, text):
    return f'<a href="{esc(url)}" target="_blank" rel="noopener">{text}</a>'


def addr(a, text):
    return ext(f"{ETHERSCAN}/address/{a}", text)


# --- Page pieces --------------------------------------------------------------------------------

def scope_cards():
    return "".join(
        f'<div class="scope-card"><div class="scope-head"><div class="stat-label">{esc(c["label"])}</div>{icon(c.get("icon", ""), 26)}</div>'
        f'<div class="scope-value">{esc(c["value"])}</div><div class="stat-sub">{T(c["detail"])}</div></div>'
        for c in CONFIG["scope"]["cards"])


deposit_chips = "".join(
    f'<span class="chip" style="text-transform:none">{icon(a.get("icon", ""), 16)}{esc(a["asset"])}</span>' for a in CONFIG["deposit_assets"])

basis_toggle = ('<span class="view-toggle" role="group" aria-label="Spread basis">' + "".join(
    f'<button type="button" data-basis="{k}" aria-pressed="{"true" if k == _default_basis else "false"}">{esc(b["label"])}</button>'
    for k, b in bases.items()) + "</span>")

spread_buttons = "".join(
    f'<button type="button" class="spread-btn" data-basis="{k}" aria-current="{"true" if k == _default_basis else "false"}">'
    f'<i>{esc(b["label"])}</i><b>{sgn(b["spread"])} pp</b></button>'
    for k, b in bases.items())


def mini(label, value, sub, cls=""):
    return f'<div class="mini{(' ' + cls) if cls else ''}"><span class="m-label">{label}<em>{sub}</em></span><span class="m-value">{value}</span></div>'


if lseth_apy is not None:
    total_row = mini("Total APY on ETH", f'<span id="totalApy">{lseth_apy + carry_head:.2f}%</span>',
                     f'{lseth_apy:.2f}% staking + <span id="carryPart">{carry_head:.2f}</span>% carry', cls="pop")
else:
    # The staking leg comes from DefiLlama (Morpho serves null for it); if that
    # fetch failed, show LLTV rather than invent a staking rate.
    total_row = mini("Liquidation LTV (LLTV)", f"{lltv:.1f}%", "on the LsETH/USDC market")
hero_side = "".join([
    total_row,
    mini("Spread", f'<span id="spreadTop">{sgn(B0["spread"])} pp</span>',
         f'vault yield minus borrow rate, <span id="spreadBasisTop">{esc(basis_word(B0["label"]))}</span>'),
    mini("Health factor · LTV", f"{hf_now:.3f} · {ltv:.1f}%" if hf_now else f"no debt · {ltv:.1f}%",
         f"today · band {V['repay_below']} to {V['borrow_above']}" + (f" · liquidation at ≈ {money(liq_px)}" if liq_px else "")),
    mini("Position", f"{coll['amount']:,.2f} LsETH",
         f"≈ {money(coll['usd'])} collateral · {borrow['amount']:,.0f} USDC borrowed"),
])

fee_term = f"{net_fee_multiplier:g}"
fee_sub = f"after {PERF_FEE * 100:.0f}% fee"
math_strip = (
    f'<div class="math">'
    f'<div class="chunk"><span class="term" id="mathYield">{B0["yield"]:.2f}%</span><span class="sub" id="mathYieldSub">vault yield, {esc(basis_word(B0["label"]))}</span></div><span class="op">-</span>'
    f'<div class="chunk"><span class="term" id="mathBorrow">{B0["borrow"]:.2f}%</span><span class="sub" id="mathBorrowSub">borrow, {esc(basis_word(B0["label"]))}</span></div><span class="op">=</span>'
    f'<div class="chunk"><span class="term" id="mathDelta">{sgn(B0["spread"])} pp</span><span class="sub">spread</span></div><span class="op">×</span>'
    f'<div class="chunk"><span class="term" id="mathLtv">{ltv:.1f}%</span><span class="sub">LTV, today</span></div><span class="op">×</span>'
    f'<div class="chunk"><span class="term">{fee_term}</span><span class="sub">{fee_sub}</span></div><span class="op">=</span>'
    f'<div class="chunk"><span class="term good" id="mathCarry">{sgn(carry_head)}%</span><span class="sub">carry on LsETH</span></div>'
    f'</div>')

SN = CONFIG.get("status_note") or {}
status_note = (f'<div class="status-note"><b>{T(SN["title"])}.</b> {T(SN["text"])}</div>'
               if SN.get("text") else "")
BT = CONFIG["backtest"]
backtest_fold = (f'<div class="card"><details class="fold"><summary>{esc(BT["summary"])}</summary>'
                 f'<p class="lede">{esc(BT["text"])} {ext(DOCS["backtest_query"], "Query ↗")}</p></details></div>')

vault_rows = "".join(
    f'<div class="kv"><span>{ext("https://app.morpho.org/ethereum/vault/" + v["address"], esc("Yield RWA" if "RWA" in v["vault"] else "Yield") + " ↗")} '
    f'<span class="dim">{v["weight"] * 100:.0f}%</span></span><span class="mono">{v["apy_pct"]:.2f}%</span></div>'
    for v in sorted(_vaults, key=lambda v: "RWA" in v["vault"]))

legend = (
    '<div class="legend">'
    '<span><i style="border-top:3px solid var(--kpk-ink)"></i><b>Blended vault yield</b></span>'
    '<span><i style="border-top-color:var(--c-borrow)"></i>Borrow APY · LsETH/USDC</span>'
    '<span><i style="border-top:1.5px solid var(--c-yield);opacity:.6"></i>KPK USDC Yield</span>'
    + ('<span><i style="border-top:1.5px solid var(--c-rwa);opacity:.6"></i>KPK USDC Yield RWA</span>' if hist.get("rwa_apy_pct") else "")
    + '</div>')

# Slider track: green up to the target LTV, orange by the repay trigger, red on to liquidation.
_g_good = TARGET_LTV / lltv * 100
_g_warn = BAND["hi"] / lltv * 100
_g_bad = (BAND["hi"] + (lltv - BAND["hi"]) / 2) / lltv * 100
slider_bg = (f"background: linear-gradient(to right, var(--c-good) 0%, var(--c-good) {_g_good:.1f}%, "
             f"var(--kpk-orange-500) {_g_warn:.1f}%, var(--c-bad) {_g_bad:.1f}%, var(--c-bad) 100%)")

sim = {
    "bases": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv) for kk, vv in b.items()} for k, b in bases.items()},
    "basis": _default_basis,
    "liveLtv": round(ltv, 4),
    "lltv": lltv,
    "fee": PERF_FEE,
    "staking": round(lseth_apy, 6) if lseth_apy is not None else None,
    "px": round(lseth_price, 4),
    "target": TARGET_LTV,
    "band": {k: round(v, 4) for k, v in BAND.items()},
}

# --- How we manage risk (all copy from config.json) ---------------------------------------------

RISK = CONFIG["risk"]
_SVG = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
PERM_ICONS = {
    "users": _SVG + '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c.5-3.6 3-5.6 6-5.6s5.5 2 6 5.6"/><circle cx="17" cy="9" r="2.6"/><path d="M16.2 14.4c2.6 0 4.5 1.8 4.9 5.1"/></svg>',
    "shield": _SVG + '<path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/></svg>',
    "sign": _SVG + '<path d="M4 20h16"/><path d="M15 4.5l4.5 4.5L9 19.5H4.5V15z"/></svg>',
}
LINK_LABELS = {"nav_calculator": "NAV Calculator ↗"}


def chips(items):
    return "".join(f'<span class="chip-s">{T(x)}</span>' for x in items)


def risk_section():
    flow = '<span class="rf-arrow" aria-hidden="true">→</span>'.join(
        f'<div class="rf-step"><div class="rf-n">{i}</div><div class="rf-t">{T(a)}</div><div class="rf-b">{T(b)}</div></div>'
        for i, (a, b) in enumerate(RISK["steps"], start=1))
    watch = "".join(
        f'<div class="card watch"><div class="watch-head">{icon(w["icon"], 24)}<div><div class="stat-label">{T(w["title"])}</div>'
        f'<div class="stat-sub">{T(w["sub"])}</div></div></div><div class="chips">{chips(w["items"])}</div>'
        + "".join(f'<div class="wakes"><div class="wakes-t">{T(lst["title"])}</div>'
                  + "".join(f'<div class="wake">{T(x)}</div>' for x in lst["items"]) + "</div>" for lst in w["lists"])
        + "</div>" for w in RISK["watch"])
    la, lh = RISK["layers"]["auto"], RISK["layers"]["human"]
    layers = (
        f'<div class="grid g2">'
        f'<div class="card layer"><div class="layer-tag auto">{T(la["tag"])}</div><div class="layer-t">{T(la["title"])}</div><p class="agent-b">{T(la["body"])}</p></div>'
        f'<div class="card layer"><div class="layer-tag human">{T(lh["tag"])}</div><div class="layer-t">{T(lh["title"])}</div><p class="agent-b">{T(lh["body"])}</p>'
        f'<div class="chips">{chips(lh.get("chips", []))}</div></div></div>')
    agents = "".join(
        f'<details class="card agent"><summary><span class="stat-label">{T(a["name"])}</span><span class="agent-s">{T(a["short"])}</span></summary>'
        f'<p class="agent-b">{T(a["body"])}'
        + (f' {addr(CONTRACTS[a["link"]], LINK_LABELS[a["link"]])}' if a.get("link") else "")
        + "</p></details>" for a in RISK["agents"])
    perms = "".join(
        f'<div class="card perm"><div class="perm-h">{PERM_ICONS[p["icon"]]}<span>{T(p["title"])}</span></div>'
        f'<p class="agent-b">{T(p["body"])}'
        + (f' Detail in the docs: {ext(DOCS["governance"], "Governance and controls ↗")}.' if p.get("docs_link") else "")
        + "</p></div>" for p in RISK["permissions"])
    return f"""
<section id="risk">
  <span class="label">How we manage risk</span>
  <h2>{T(RISK['title'])}</h2>
  <p class="lede">{T(RISK['lede'])}</p>
  <div class="doc-links">{ext(DOCS['automation'], 'How KPK automation works ↗')}{ext(DOCS['risk_framework'], 'KPK Risk Framework ↗')}{ext(DOCS['governance'], 'Governance and controls ↗')}</div>
  <div class="riskflow">{flow}</div>
  <div class="sub-h">What is watched</div>
  <div class="grid g3">{watch}</div>
  <div class="stat-sub" style="margin-top:-6px">Agent settings as set on {esc(RISK['as_of'])}.</div>
  <div class="sub-h">How a signal is handled</div>
  {layers}
  <div class="sub-h">The agents</div>
  <div class="grid g3">{agents}</div>
  <div class="sub-h">Onchain permissions</div>
  <div class="grid g3">{perms}</div>
</section>
"""


SCALING = CONFIG["scaling"]
SCALING_STATUS_CLASS = {"live": "positive", "under discussion": "info"}


def scaling_rows():
    """One table row per borrow venue; the status badge states the venue's real status."""
    out = []
    for s in SCALING["stages"]:
        cls = SCALING_STATUS_CLASS.get(s["status"], "generic")
        ics = "".join(icon(i, 20) for i in s.get("icons", []))
        links = "".join(f'<div class="stat-sub">{ext(u, esc(t))}</div>' for t, u in s.get("links", []))
        out.append(f'<tr><td class="nowrap"><b>{esc(s["stage"])}</b><div class="stage-icons">{ics}</div></td><td>{T(s["adds"])}</td>'
                   f'<td>{T(s["needs"])}{links}</td><td class="nowrap"><span class="sc-badge {cls}">{esc(s["status"])}</span></td></tr>')
    return "".join(out)


def scaling_section():
    """How the fund scales: the borrow venues beyond Morpho and where each stands."""
    return f"""
<section id="scaling">
  <span class="label">How the fund scales</span>
  <h2>{esc(SCALING['title'])}</h2>
  <p class="lede">{esc(SCALING['intro'])}</p>
  <div class="card"><div class="table-scroll"><table class="data">
    <thead><tr><th>Stage</th><th>What the strategy adds</th><th>What it needs</th><th>Status</th></tr></thead>
    <tbody>{scaling_rows()}</tbody></table></div>
    <div class="stat-sub" style="margin-top:12px">{esc(SCALING['note'].format(as_of=SCALING['as_of']))}</div>
  </div>
</section>
"""


TERMS = CONFIG["terms"]
terms_tiles = "".join(
    f'<div class="redeem-tile{" strong" if t.get("strong") else ""}"><div class="redeem-v">{esc(t["value"])}</div><div class="redeem-k">{esc(t["detail"])}</div></div>'
    for t in TERMS["tiles"])
terms_rows = "".join(f'<div class="kv"><span>{esc(k)}</span><span>{esc(v)}</span></div>' for k, v in TERMS["rows"])

# --- CSS and JS (plain strings: no em dashes, tokens only) -------------------------------------

CSS = """
:root {
  --c-line: #2E7FC1; --c-liq: #D2553A; --c-band: #2D8C6A;
  --c-band-fill: rgba(45, 140, 106, 0.12); --c-good: #2D8C6A; --c-bad: #D2553A; --c-info: #2E7FC1;
  --c-warn: #B8741A; --c-borrow: #2E7FC1; --c-yield: #DC5C39; --c-rwa: #22676D;
}
:root[data-theme="dark"] { --c-line: #3F93D4; --c-liq: #DD6447; --c-band: #35A578; --c-band-fill: rgba(53, 165, 120, 0.16); --c-good: #35A578; --c-bad: #DD6447; --c-info: #3F93D4; --c-warn: #E8973F; --c-borrow: #3F93D4; --c-yield: #E2724F; --c-rwa: #4DBEC8; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { DARK_TOKENS --c-line: #3F93D4; --c-liq: #DD6447; --c-band: #35A578; --c-band-fill: rgba(53, 165, 120, 0.16); --c-good: #35A578; --c-bad: #DD6447; --c-info: #3F93D4; --c-warn: #E8973F; --c-borrow: #3F93D4; --c-yield: #E2724F; --c-rwa: #4DBEC8; }
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { background: var(--kpk-surface); color: var(--kpk-ink); font-family: var(--kpk-font-sans); font-weight: 400; line-height: 1.5; -webkit-font-smoothing: antialiased; }
a { color: inherit; text-decoration-color: var(--kpk-ink-faint); text-underline-offset: 2px; }
.page { max-width: 1040px; margin: 0 auto; padding: var(--kpk-space-6) var(--kpk-space-4) var(--kpk-space-16); display: flex; flex-direction: column; gap: var(--kpk-space-8); }
header.top { display: flex; align-items: center; justify-content: space-between; gap: var(--kpk-space-4); flex-wrap: wrap; }
.logo { width: 84px; color: var(--kpk-ink); }
.logo svg { width: 100%; height: auto; display: block; }
.meta { display: flex; gap: var(--kpk-space-3); align-items: center; flex-wrap: wrap; font-size: var(--kpk-text-fs-meta); color: var(--kpk-ink-muted); }
.asof.stale { color: var(--c-bad); font-weight: 500; }
.theme-btn { font: inherit; font-size: var(--kpk-text-fs-meta); color: var(--kpk-ink-muted); background: none; border: var(--kpk-rule-hard) solid var(--kpk-line); border-radius: var(--kpk-radius-pill); padding: 2px 10px; cursor: pointer; }
.hero h1 { font-size: var(--kpk-text-display-h1); font-weight: 500; letter-spacing: -0.01em; margin: 0 0 var(--kpk-space-3); line-height: 1.1; }
.hero p { font-size: var(--kpk-text-body); max-width: 70ch; margin: 0 0 var(--kpk-space-4); }
.hero .row { display: flex; gap: var(--kpk-space-3); flex-wrap: wrap; align-items: center; }
@media (max-width: 640px) { .hero h1 { font-size: 34px; } .hero p { font-size: 15px; } }
.cta { display: inline-flex; align-items: center; gap: 8px; background: var(--kpk-ink); color: var(--kpk-surface); text-decoration: none; padding: 10px 18px; border-radius: var(--kpk-radius-pill); font-weight: 500; font-size: var(--kpk-text-body-small); }
.cta:hover { opacity: 0.9; }
.cta.ghost { background: transparent; color: var(--kpk-ink); border: var(--kpk-rule-hard) solid var(--kpk-line-strong); }
.chip { display: inline-flex; align-items: center; gap: 6px; font-size: var(--kpk-text-fs-tag); letter-spacing: 0.04em; text-transform: uppercase; padding: 3px 9px; border-radius: var(--kpk-radius-tag); border: var(--kpk-rule-hard) solid var(--kpk-line-strong); }
.pill { display: inline-block; font-size: var(--kpk-text-fs-tag); letter-spacing: 0.04em; text-transform: uppercase; padding: 3px 9px; border-radius: var(--kpk-radius-tag); background: var(--kpk-pill); color: var(--kpk-ink); vertical-align: middle; }
section { display: flex; flex-direction: column; gap: var(--kpk-space-4); scroll-margin-top: var(--kpk-space-4); }
.label { align-self: flex-start; background: var(--kpk-label-bg); color: var(--kpk-label-ink); font-size: var(--kpk-text-label); font-weight: 500; letter-spacing: 0.08em; text-transform: uppercase; padding: 3px 9px; border-radius: var(--kpk-radius-tag); }
h2 { font-size: var(--kpk-text-fs-section); font-weight: 500; margin: 0; letter-spacing: -0.01em; line-height: 1.2; }
@media (max-width: 640px) { h2 { font-size: 24px; } }
.lede { color: var(--kpk-ink-muted); font-size: var(--kpk-text-body-small); max-width: 80ch; margin: 0; }
.asof-note { font-size: var(--kpk-text-fs-meta); color: var(--kpk-ink-muted); }
.note { font-size: var(--kpk-text-body-small); border: var(--kpk-rule-hard) solid var(--c-bad); border-radius: var(--kpk-radius-label); padding: 10px 14px; }
.status-note { font-size: var(--kpk-text-body-small); background: var(--kpk-surface-card); border: var(--kpk-rule-hard) solid var(--kpk-line); border-left: 3px solid var(--c-good); border-radius: var(--kpk-radius-label); padding: 12px 16px; line-height: 1.55; }
.status-note b { font-weight: 500; }
.card { background: var(--kpk-surface-card); border: var(--kpk-rule-hard) solid var(--kpk-line); border-radius: var(--kpk-radius-card); padding: var(--kpk-space-6); min-width: 0; }
@media (max-width: 640px) { .card { padding: var(--kpk-space-4); } }
.grid { display: grid; gap: var(--kpk-space-4); }
.g3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.g2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (max-width: 760px) { .g3, .g2 { grid-template-columns: 1fr; } }
.stat-label { font-size: var(--kpk-text-label); letter-spacing: 0.08em; text-transform: uppercase; color: var(--kpk-ink-muted); font-weight: 500; }
.stat-value { font-size: var(--kpk-text-fs-stat); font-weight: 500; letter-spacing: -0.02em; line-height: 1.1; margin: 6px 0 4px; font-variant-numeric: tabular-nums; }
.stat-value.good, .term.good { color: var(--c-good); }
.stat-value.bad, .term.bad { color: var(--c-bad); }
.muted { color: var(--kpk-ink-muted); }
.stat-sub { font-size: var(--kpk-text-fs-meta); color: var(--kpk-ink-muted); margin-top: 4px; }
.kv { display: flex; justify-content: space-between; gap: 12px; font-size: var(--kpk-text-body-small); padding: 6px 0; border-top: var(--kpk-rule-soft) solid var(--kpk-line); flex-wrap: wrap; }
.kv:first-of-type { margin-top: 8px; }
.kv > span:last-child { margin-left: auto; text-align: right; }
.mono { font-family: var(--kpk-font-mono); font-size: var(--kpk-text-data-mono); }
.dim { color: var(--kpk-ink-muted); }
.nowrap { white-space: nowrap; }
.math { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; font-size: var(--kpk-text-body-small); }
.math .chunk { display: flex; flex-direction: column; align-items: center; }
.math .term { font-weight: 500; font-family: var(--kpk-font-mono); font-size: 16px; }
.math .sub { font-size: var(--kpk-text-fs-tag); color: var(--kpk-ink-muted); }
.math .op { color: var(--kpk-ink-muted); }
.flow { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr; gap: 12px; align-items: stretch; }
.flow .arrow { align-self: center; color: var(--kpk-ink-muted); font-size: 20px; }
@media (max-width: 760px) { .flow { grid-template-columns: 1fr; } .flow .arrow { transform: rotate(90deg); justify-self: center; } }
.leg { display: flex; flex-direction: column; }
.leg-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.leg-kind { font-size: var(--kpk-text-label); letter-spacing: 0.08em; text-transform: uppercase; font-weight: 500; color: var(--kpk-ink-muted); }
.leg-amount { font-size: 22px; font-weight: 500; margin: 4px 0 6px; font-variant-numeric: tabular-nums; }
.leg .kv:first-of-type { margin-top: 0; }
.logo-icon { border-radius: 50%; flex: none; display: inline-block; vertical-align: middle; }
.logo-icon.sq { border-radius: 22%; }
.scope { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: var(--kpk-space-6); }
@media (max-width: 760px) { .scope { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 440px) { .scope { grid-template-columns: 1fr; } }
.scope-card { background: var(--kpk-surface-card); border: var(--kpk-rule-hard) solid var(--kpk-line); border-radius: var(--kpk-radius-card); padding: 12px 14px; min-width: 0; }
.scope-value { font-size: 17px; font-weight: 500; margin: 4px 0 2px; }
.scope-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
.doc-links { display: flex; gap: 10px; flex-wrap: wrap; }
.doc-links a { font-size: var(--kpk-text-fs-meta); padding: 4px 10px; border: var(--kpk-rule-hard) solid var(--kpk-line); border-radius: var(--kpk-radius-pill); text-decoration: none; }
.doc-links a:hover { border-color: var(--kpk-line-strong); }
.sub-h { font-size: var(--kpk-text-label); letter-spacing: 0.08em; text-transform: uppercase; color: var(--kpk-ink-muted); font-weight: 500; margin: 6px 0 -6px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.chip-s { font-size: var(--kpk-text-fs-tag); padding: 3px 8px; border-radius: var(--kpk-radius-pill); background: var(--kpk-pill); color: var(--kpk-ink); line-height: 1.4; }
.pill.live-pill { background: var(--c-band-fill); color: var(--c-good); }
.view-toggle { display: inline-flex; gap: 4px; margin-left: 8px; vertical-align: middle; flex-wrap: wrap; }
.view-toggle button { font: inherit; font-size: var(--kpk-text-fs-tag); letter-spacing: 0.05em; text-transform: uppercase; padding: 3px 9px; border-radius: var(--kpk-radius-pill); border: var(--kpk-rule-hard) solid var(--kpk-line); background: transparent; color: var(--kpk-ink-muted); cursor: pointer; }
.view-toggle button[aria-pressed="true"] { background: var(--c-band-fill); color: var(--c-good); border-color: var(--c-good); }
.watch-head { display: flex; gap: 10px; align-items: flex-start; }
.wakes { margin-top: 12px; padding-top: 10px; border-top: var(--kpk-rule-soft) solid var(--kpk-line); }
.wakes-t { font-size: var(--kpk-text-fs-tag); color: var(--kpk-ink-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
.wake { font-size: var(--kpk-text-fs-meta); padding: 4px 0 4px 10px; border-left: 2px solid var(--c-band); margin-bottom: 4px; }
.layer-tag { display: inline-block; font-size: var(--kpk-text-fs-tag); letter-spacing: 0.05em; text-transform: uppercase; padding: 3px 9px; border-radius: var(--kpk-radius-tag); }
.layer-tag.auto { background: var(--c-band-fill); color: var(--c-good); border: var(--kpk-rule-hard) solid var(--c-good); }
.layer-tag.human { background: var(--kpk-pill); color: var(--kpk-ink); }
.layer-t { font-size: 18px; font-weight: 500; margin: 10px 0 0; }
details.agent > summary { cursor: pointer; list-style: none; display: flex; flex-direction: column; gap: 4px; }
details.agent > summary::-webkit-details-marker { display: none; }
details.agent > summary::after { content: "Show more"; font-size: var(--kpk-text-fs-tag); color: var(--kpk-ink-muted); text-decoration: underline; margin-top: 4px; }
details.agent[open] > summary::after { content: "Show less"; }
.agent-s { font-size: 15px; font-weight: 500; }
.agent-b { font-size: var(--kpk-text-body-small); margin: 8px 0 0; line-height: 1.55; }
.riskflow { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr auto 1fr; gap: 10px; align-items: stretch; }
.rf-step { background: var(--kpk-surface-card); border: var(--kpk-rule-hard) solid var(--kpk-line); border-radius: var(--kpk-radius-card); padding: 14px 16px; }
.rf-n { width: 22px; height: 22px; border-radius: 50%; background: var(--kpk-ink); color: var(--kpk-surface); font-size: 12px; font-weight: 500; display: flex; align-items: center; justify-content: center; }
.rf-t { font-weight: 500; margin: 8px 0 4px; }
.rf-b { font-size: var(--kpk-text-fs-meta); color: var(--kpk-ink-muted); line-height: 1.5; }
.rf-arrow { align-self: center; color: var(--kpk-ink-muted); font-size: 20px; }
@media (max-width: 860px) { .riskflow { grid-template-columns: 1fr; } .rf-arrow { transform: rotate(90deg); justify-self: center; } }
.perm-h { display: flex; gap: 10px; align-items: center; font-weight: 500; font-size: 16px; color: var(--kpk-ink); }
.perm-h svg { color: var(--c-good); flex: none; }
.stage-icons { display: flex; gap: 4px; margin-top: 6px; }
.sc-badge { display: inline-block; font-size: var(--kpk-text-fs-tag); padding: 2px 8px; border-radius: var(--kpk-radius-tag); border: var(--kpk-rule-hard) solid var(--kpk-line); white-space: nowrap; }
.sc-badge.positive { border-color: var(--c-good); color: var(--c-good); }
.sc-badge.info { border-color: var(--c-info); color: var(--c-info); }
.redeem { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }
@media (max-width: 480px) { .redeem { grid-template-columns: 1fr; } }
.redeem-tile { border: var(--kpk-rule-hard) solid var(--kpk-line); border-radius: var(--kpk-radius-label); padding: 12px 14px; }
.redeem-tile.strong { border-color: var(--c-good); background: var(--c-band-fill); }
.redeem-v { font-size: 22px; font-weight: 500; }
.redeem-tile.strong .redeem-v { color: var(--c-good); }
.redeem-k { font-size: var(--kpk-text-fs-meta); color: var(--kpk-ink-muted); margin-top: 4px; }
.headline { display: grid; grid-template-columns: 1.3fr 1fr; gap: var(--kpk-space-4); align-items: start; }
@media (max-width: 760px) { .headline { grid-template-columns: 1fr; } }
.hero-value { font-size: var(--kpk-text-slide-stat); margin: 10px 0 8px; }
@media (max-width: 640px) { .hero-value { font-size: 52px; } }
.stat-note { font-size: var(--kpk-text-body-small); color: var(--kpk-ink-muted); max-width: 52ch; }
.stat-note b { color: var(--kpk-ink); font-weight: 500; }
.side-stats { display: flex; flex-direction: column; background: var(--kpk-surface-card); border: var(--kpk-rule-hard) solid var(--kpk-line); border-radius: var(--kpk-radius-card); padding: 4px 18px; }
.mini { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; padding: 8px 0; }
.mini + .mini { border-top: var(--kpk-rule-soft) solid var(--kpk-line); }
/* The fund's headline return: KPK terracotta-600, the same token as the chart's Yield line. */
.mini.pop .m-value { color: var(--c-yield); }
.m-label { font-size: var(--kpk-text-body-small); color: var(--kpk-ink-muted); font-weight: 500; }
.m-label em { display: block; font-style: normal; font-weight: 400; font-size: var(--kpk-text-fs-tag); margin-top: 2px; }
.m-value { font-size: 20px; font-weight: 500; font-variant-numeric: tabular-nums; white-space: nowrap; }
details.fold > summary { cursor: pointer; font-size: var(--kpk-text-body-small); font-weight: 500; }
details.fold[open] > summary { margin-bottom: 10px; }
.legend { display: flex; gap: 18px; flex-wrap: wrap; font-size: var(--kpk-text-fs-meta); color: var(--kpk-ink-muted); align-items: center; margin-top: 10px; }
.legend i { display: inline-block; width: 16px; height: 0; border-top: 2px solid var(--c-line); vertical-align: middle; margin-right: 6px; }
.chart { position: relative; margin-top: 10px; }
.chart-svg { width: 100%; height: auto; display: block; overflow: visible; touch-action: pan-y; }
.chart-svg text { font-family: var(--kpk-font-sans); fill: var(--kpk-ink-muted); font-size: 12px; }
.chart-grid { stroke: var(--kpk-line); stroke-width: 1; }
.chart-endlabel { font-size: 12px; font-weight: 500; }
.chart-crosshair { stroke: var(--kpk-ink-muted); stroke-width: 1; }
.risk-list { display: flex; flex-direction: column; }
.risk-row { display: grid; grid-template-columns: 160px 1fr 1.3fr; gap: 14px; padding: 10px 0; border-top: var(--kpk-rule-soft) solid var(--kpk-line); font-size: var(--kpk-text-body-small); }
.risk-row:first-child { border-top: 0; padding-top: 0; }
.risk-n { font-weight: 500; }
.risk-h { color: var(--kpk-ink-muted); padding-left: 10px; border-left: 2px solid var(--c-band); }
@media (max-width: 760px) { .risk-row { grid-template-columns: 1fr; gap: 4px; } }
.chart-mark { stroke: var(--kpk-ink-faint); stroke-width: 1.2; stroke-dasharray: 5 3; }
.statrow { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 10px; }
@media (max-width: 760px) { .statrow { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.c-label { font-size: var(--kpk-text-label); letter-spacing: 0.08em; text-transform: uppercase; color: var(--kpk-ink-muted); font-weight: 500; }
.c-value { font-size: 26px; font-weight: 500; margin: 4px 0 2px; font-variant-numeric: tabular-nums; }
.c-sub { font-size: var(--kpk-text-fs-tag); color: var(--kpk-ink-muted); line-height: 1.4; }

.keep-case { text-transform: none; }
.legend .shape { display: inline-block; margin-right: 6px; font-size: 13px; }
.legend i.rb-band-key { height: 10px; border: 1px dashed var(--c-band); background: var(--c-band-fill); }
.rb-band { fill: var(--c-band-fill); stroke: none; }
.rb-band-edge { fill: none; stroke: var(--c-band); stroke-width: 1; stroke-dasharray: 3 3; }
.rb-phase { fill: var(--kpk-ink); opacity: 0.05; }
.rb-mk { stroke: var(--kpk-surface-card); stroke-width: 2; }
.rb-mk.relever { fill: var(--c-band); }
.rb-mk.derisk { fill: var(--c-liq); }
.rb-mk.open { fill: var(--kpk-ink); }
.chart-tooltip .act { margin-top: 3px; padding-top: 3px; border-top: var(--kpk-rule-soft) solid var(--kpk-line); }
.chart-svg text.chart-marklabel { font-size: 11px; }
@media (max-width: 640px) {
  .chart { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  .chart-svg { min-width: 720px; }
  .chart-svg text { font-size: 14px; }
}
.chart-tooltip { position: absolute; top: 0; pointer-events: none; visibility: hidden; background: var(--kpk-sheet); border: var(--kpk-rule-hard) solid var(--kpk-line-strong); border-radius: var(--kpk-radius-label); padding: 8px 10px; font-size: 12px; white-space: nowrap; transform: translate(-50%, 12px); z-index: 3; min-width: 150px; }
.chart-tooltip .date { color: var(--kpk-ink-muted); margin-bottom: 3px; }
.chart-tooltip .row { display: flex; align-items: center; gap: 6px; }
.chart-tooltip .key { width: 10px; height: 2px; border-radius: 2px; display: inline-block; }
.chart-tooltip .val { font-weight: 500; font-variant-numeric: tabular-nums; margin-left: auto; padding-left: 10px; }
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; max-height: 420px; overflow-y: auto; }
table.data { border-collapse: collapse; width: 100%; font-size: var(--kpk-text-body-small); }
table.data th, table.data td { text-align: left; padding: 8px 10px; border-bottom: var(--kpk-rule-soft) solid var(--kpk-line); vertical-align: top; }
table.data th { font-size: var(--kpk-text-label); letter-spacing: 0.06em; text-transform: uppercase; color: var(--kpk-ink-muted); font-weight: 500; position: sticky; top: 0; background: var(--kpk-surface-card); }
table.data .num { text-align: right; font-variant-numeric: tabular-nums; }
.sim-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.sim-head > div:first-child { flex: 1 1 420px; }
.sim-head .lede { margin-top: 6px; }
.sim-out { display: flex; align-items: baseline; gap: 10px; }
.sim-out .big { font-size: 36px; font-weight: 500; color: var(--c-good); font-variant-numeric: tabular-nums; }
.sim-out .big.neg { color: var(--c-bad); }
.slider-wrap { margin-top: 18px; }
.spread-picker { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 16px; }
.picker-label { font-size: var(--kpk-text-fs-meta); color: var(--kpk-ink-muted); margin-right: 2px; }
.spread-btn { display: flex; flex-direction: column; align-items: flex-start; gap: 1px; padding: 6px 11px; border: var(--kpk-rule-hard) solid var(--kpk-line); border-radius: var(--kpk-radius-label); background: var(--kpk-surface); color: var(--kpk-ink-muted); font: inherit; cursor: pointer; text-align: left; }
.spread-btn i { font-style: normal; font-size: var(--kpk-text-fs-tag); letter-spacing: 0.03em; }
.spread-btn b { font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; }
.spread-btn[aria-current="true"] { border-color: var(--c-good); color: var(--kpk-ink); }
.spread-btn[aria-current="true"] b { color: var(--c-good); }
.picker-note { flex: 1 1 100%; font-size: var(--kpk-text-fs-tag); color: var(--kpk-ink-muted); }
.ends { display: flex; justify-content: space-between; font-size: var(--kpk-text-fs-tag); color: var(--kpk-ink-muted); margin-bottom: 6px; }
.ends .hi { color: var(--c-bad); }
input[type=range] { width: 100%; -webkit-appearance: none; appearance: none; height: 6px; border-radius: 4px; outline: none; margin: 0; }
input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; width: 22px; height: 22px; border-radius: 50%; background: var(--kpk-ink); border: 3px solid var(--kpk-surface-card); cursor: pointer; }
input[type=range]::-moz-range-thumb { width: 22px; height: 22px; border-radius: 50%; background: var(--kpk-ink); border: 3px solid var(--kpk-surface-card); cursor: pointer; }
.marks { position: relative; height: 88px; margin-top: 4px; }
.band-seg { position: absolute; top: 0; height: 8px; background: var(--c-band-fill); border: var(--kpk-rule-hard) dashed var(--c-band); border-radius: 4px; }
.band-seg span { position: absolute; top: 10px; left: 50%; transform: translateX(-50%); font-size: var(--kpk-text-fs-tag); color: var(--c-band); white-space: nowrap; }
.band-seg.end span { left: auto; right: 0; transform: none; }
.marks button { position: absolute; transform: translateX(-50%); background: none; border: 0; padding: 2px 4px; font: inherit; font-size: var(--kpk-text-fs-tag); color: var(--kpk-ink-muted); cursor: pointer; white-space: nowrap; }
.marks button::before { content: ""; display: block; width: 7px; height: 7px; border-radius: 50%; border: 1.5px solid var(--kpk-ink-muted); margin: 0 auto 3px; }
.marks button.end { transform: translateX(calc(-100% + 7.5px)); text-align: right; }
.marks button.end::before { margin: 0 0 3px auto; }
.marks button.on { color: var(--kpk-ink); font-weight: 500; }
.marks button.on::before { background: var(--kpk-ink); border-color: var(--kpk-ink); }
.sim-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }
.sim-grid.three { grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 12px; }
@media (max-width: 760px) { .sim-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .sim-grid.three { grid-template-columns: 1fr; } }
.sim-cell { border: var(--kpk-rule-hard) solid var(--kpk-line); border-radius: var(--kpk-radius-label); padding: 10px 12px; min-width: 0; }
.sim-cell .k { font-size: var(--kpk-text-fs-tag); color: var(--kpk-ink-muted); text-transform: uppercase; letter-spacing: 0.04em; }
.sim-cell .v { font-size: 17px; font-weight: 500; margin-top: 2px; font-variant-numeric: tabular-nums; }
.sim-cell.t-good { border-color: var(--c-good); background: var(--c-band-fill); }
.sim-cell.t-good .v { color: var(--c-good); }
.sim-cell.t-warn { border-color: var(--c-warn); }
.sim-cell.t-warn .v { color: var(--c-warn); }
.sim-cell.t-bad { border-color: var(--c-bad); }
.sim-cell.t-bad .v { color: var(--c-bad); }
.sim-cell.t-none .v { color: var(--kpk-ink-muted); }
footer { border-top: var(--kpk-rule-hard) solid var(--kpk-line-strong); padding-top: var(--kpk-space-4); font-size: var(--kpk-text-fs-footer); color: var(--kpk-ink-muted); display: flex; flex-direction: column; gap: 8px; }
footer p { margin: 0; max-width: 100ch; }
footer b { font-weight: 500; color: var(--kpk-ink); }
a:focus-visible, button:focus-visible, input:focus-visible { outline: 2px solid var(--c-info); outline-offset: 2px; }
""".replace("DARK_TOKENS", dark_block)

JS = """
(function () {
  var root = document.documentElement;
  try { var saved = localStorage.getItem('lseth-carry-theme'); if (saved) root.setAttribute('data-theme', saved); } catch (e) {}
  var btn = document.getElementById('themeBtn');
  function label() { var t = root.getAttribute('data-theme'); btn.textContent = t === 'dark' ? 'Dark' : t === 'light' ? 'Light' : 'Auto'; }
  if (btn) {
    label();
    btn.addEventListener('click', function () {
      var t = root.getAttribute('data-theme'), next = t === 'light' ? 'dark' : t === 'dark' ? null : 'light';
      if (next) root.setAttribute('data-theme', next); else root.removeAttribute('data-theme');
      try { if (next) localStorage.setItem('lseth-carry-theme', next); else localStorage.removeItem('lseth-carry-theme'); } catch (e) {}
      label();
    });
  }
  // The page is static: the build stamps when it ran; the browser adds how old that is now.
  var el = document.getElementById('asOf');
  if (el) {
    var built = parseInt(el.getAttribute('data-built'), 10), mins = Math.floor((Date.now() / 1000 - built) / 60);
    if (mins >= 0) {
      el.textContent += ' · ' + (mins < 60 ? mins + ' min ago' : mins < 1440 ? Math.floor(mins / 60) + ' h ago' : Math.floor(mins / 1440) + ' days ago');
      if (mins > 300) el.classList.add('stale');  // >5h: the 4-hourly rebuild slipped
    }
  }

  var S = window.SIM, slider = document.getElementById('ltvSlider');
  if (!S || !slider) return;
  var cur = S.basis;
  function $(id) { return document.getElementById(id); }
  function set(id, txt) { var e = $(id); if (e) e.textContent = txt; }
  function sg(x) { return (x >= 0 ? '+' : '') + x.toFixed(2); }
  function money(x) { return (x < 0 ? '-$' : '$') + Math.abs(x).toLocaleString('en-US', { maximumFractionDigits: 0 }); }
  // Carry = spread x LTV x (1 - fee), with the spread held at the chosen basis.
  function carryAt(l) { return S.bases[cur].spread * l / 100 * (1 - S.fee); }

  // Headline, side card and formula strip: the fund at its live LTV, on the chosen basis.
  function renderHead() {
    var B = S.bases[cur], c = carryAt(S.liveLtv), w = B.label.toLowerCase();
    set('heroCarry', sg(c) + '%');
    $('heroCarry').className = 'stat-value hero-value ' + (c > 0 ? 'good' : 'bad');
    set('heroBasis', w);
    set('heroUsd', '≈ ' + money(S.px * c / 100) + ' a year per 1 LsETH at today\\'s price.');
    set('spreadTop', sg(B.spread) + ' pp');
    set('spreadBasisTop', w);
    if (S.staking !== null) { set('totalApy', (S.staking + c).toFixed(2) + '%'); set('carryPart', c.toFixed(2)); }
    set('mathYield', B.yield.toFixed(2) + '%'); set('mathYieldSub', 'vault yield, ' + w);
    set('mathBorrow', B.borrow.toFixed(2) + '%'); set('mathBorrowSub', 'borrow, ' + w);
    set('mathDelta', sg(B.spread) + ' pp');
    set('mathCarry', sg(c) + '%');
    $('mathCarry').className = 'term ' + (c > 0 ? 'good' : 'bad');
  }

  // Marks on the LTV track: the health factor manager's band, the target, and today's LTV.
  var box = $('ltvMarks'), markEls = [];
  function frac(l) { return Math.max(0, Math.min(1, l / S.lltv)); }
  function pos(l) { var f = frac(l); return 'calc(' + (f * 100) + '% + ' + ((0.5 - f) * 22) + 'px)'; }
  var seg = document.createElement('div'); seg.className = 'band-seg';
  var fa = frac(S.band.lo), fb = frac(S.band.hi);
  seg.style.left = pos(S.band.lo);
  seg.style.width = 'calc(' + ((fb - fa) * 100) + '% - ' + ((fb - fa) * 22) + 'px)';
  seg.innerHTML = '<span>health factor band, ' + S.band.lo.toFixed(1) + '% to ' + S.band.hi.toFixed(1) + '% LTV</span>';
  if (fb > 0.7) seg.classList.add('end');
  box.appendChild(seg);
  var same = Math.abs(S.liveLtv - S.target) < 0.3;
  var marks = [{ ltv: S.target, label: same ? 'target ' + S.target + '% = live' : 'target ' + S.target + '%', row: 1 }];
  if (!same) marks.push({ ltv: S.liveLtv, label: 'live ' + S.liveLtv.toFixed(1) + '%', row: 2 });
  marks.forEach(function (m) {
    var b = document.createElement('button'); b.type = 'button'; b.textContent = m.label;
    b.title = 'Set LTV to ' + m.ltv.toFixed(1) + '%';
    b.style.left = pos(m.ltv);
    b.style.top = (8 + m.row * 26) + 'px';
    if (frac(m.ltv) > 0.75) b.classList.add('end');
    b.addEventListener('click', function () { slider.value = m.ltv; render(); });
    box.appendChild(b); markEls.push({ el: b, ltv: m.ltv });
  });

  function render() {
    var l = parseFloat(slider.value), c = carryAt(l);
    set('sliderCarry', sg(c) + '%');
    $('sliderCarry').className = 'big' + (c < 0 ? ' neg' : '');
    set('sliderLtv', l.toFixed(1) + '%');
    set('spreadBasis', S.bases[cur].label);
    set('simHf', l > 0 ? (S.lltv / l).toFixed(3) : 'no debt');
    set('simBorrow', Math.round(S.px * l / 100).toLocaleString('en-US') + ' USDC');
    set('simUsd', money(S.px * c / 100) + ' a year');
    set('simDrop', ((1 - l / S.lltv) * 100).toFixed(1) + '%');
    $('simWarn').hidden = l <= S.band.hi + 0.05;
    markEls.forEach(function (m) { m.el.classList.toggle('on', Math.abs(m.ltv - l) < 0.06); });
  }

  // LTV needed for a given carry: the inverse of carry = spread x LTV x (1 - fee). The tile's
  // colour is read off the rounded value it shows, so label and colour never disagree.
  function renderTargets() {
    var sp = S.bases[cur].spread;
    [['ltvFor15', 1.5], ['ltvFor20', 2.0], ['ltvFor25', 2.5]].forEach(function (t) {
      var need = sp > 0 ? t[1] / (sp * (1 - S.fee)) * 100 : Infinity, e = $(t[0]);
      e.textContent = !isFinite(need) ? 'n/a at a negative spread' : need > S.lltv ? 'needs a wider spread' : need.toFixed(0) + '% LTV';
      var r = Math.round(need);
      e.parentElement.className = 'sim-cell ' + (!isFinite(need) || need > S.lltv ? 't-none' : r <= S.target ? 't-good' : r <= S.band.hi ? 't-warn' : 't-bad');
    });
  }

  // One basis for the whole page: the headline toggle and the simulator's picker stay in step.
  var basisBtns = [].slice.call(document.querySelectorAll('[data-basis]'));
  function setBasis(k) {
    cur = k;
    basisBtns.forEach(function (b) {
      var on = b.getAttribute('data-basis') === k ? 'true' : 'false';
      b.setAttribute(b.classList.contains('spread-btn') ? 'aria-current' : 'aria-pressed', on);
    });
    renderHead(); renderTargets(); render();
  }
  basisBtns.forEach(function (b) { b.addEventListener('click', function () { setBasis(b.getAttribute('data-basis')); }); });
  slider.addEventListener('input', render);
  setBasis(cur);
})();
"""

page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Reachable by link, not discoverable by search. robots.txt asks crawlers not
     to fetch; this asks them not to index if they fetch anyway. -->
<meta name="robots" content="noindex, nofollow">
<title>LsETH Carry Fund · KPK</title>
<meta property="og:title" content="LsETH Carry Fund | KPK">
<meta property="og:type" content="website">
<meta property="og:site_name" content="KPK">
<meta property="og:url" content="https://external.kpk.io/staking-carry-fund/lsETH/">
<meta property="og:image" content="https://external.kpk.io/staking-carry-fund/lsETH/og.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="KPK">
<meta name="twitter:card" content="summary_large_image">
<meta name="description" content="KPK's LsETH Carry Fund: LsETH collateral on Morpho, USDC borrowed and deployed in KPK's USDC vaults. Live carry, position, risk controls and terms.">
<style>{fonts_css}{tokens_css}{CSS}</style>
</head>
<body>
<div class="page">

<header class="top">
  <div class="logo" aria-label="KPK">{logo_svg}</div>
  <div class="meta">
    <span>Ethereum mainnet</span><span aria-hidden="true">·</span>
    <span class="asof" id="asOf" data-built="{built_epoch}">As of {as_of}</span>
    <button type="button" class="theme-btn" id="themeBtn" title="Switch theme">Auto</button>
  </div>
</header>

<section class="hero" id="overview">
  <h1>LsETH Carry Fund <span class="pill">live since {esc(CONFIG['live_since'])}</span></h1>
  {''.join(f'<p>{T(p)}</p>' for p in CONFIG['intro'])}
  <div class="row">
    <a class="cta" href="{esc(CONFIG['app_url'])}" target="_blank" rel="noopener">View the fund in the KPK app ↗</a>
    <a class="cta ghost" href="{esc(DOCS['fund'])}" target="_blank" rel="noopener">Read the fund docs ↗</a>
    <span class="asof-note">Deposit asset:</span> {deposit_chips}
    <span class="asof-note">{ext(DOCS['dune'], 'Live metrics on Dune ↗')}</span>
  </div>
  <div class="scope" aria-label="Scope">{scope_cards()}</div>
</section>

<section id="numbers">
  <span class="label">Headline numbers</span>
  <h2>What the fund earns on LsETH</h2>
  <div class="headline">
    <div class="card hero-main">
      <div class="stat-label">Carry on LsETH {basis_toggle}</div>
      <div class="stat-value hero-value {'good' if carry_head > 0 else 'bad'}" id="heroCarry">{sgn(carry_head)}%</div>
      <div class="stat-note">a year on the LsETH held, on top of its staking yield, at today's <b>{ltv:.1f}%</b> LTV and the <b id="heroBasis">{esc(basis_word(B0['label']))}</b> spread, after the {PERF_FEE * 100:.0f}% performance fee. <em>Not a promise: rates move.</em></div>
      <div class="stat-sub" id="heroUsd">≈ {money(lseth_price * carry_head / 100)} a year per 1 LsETH at today's price.</div>
    </div>
    <div class="side-stats">{hero_side}</div>
  </div>
  {math_strip}
  {status_note}
  {backtest_fold}
</section>

<section id="structure">
  <span class="label">Position structure</span>
  <h2>How the position is built</h2>
  <div class="flow">
    <div class="card leg">
      <div class="leg-head"><span class="leg-kind">Collateral</span>{icon("lseth")}</div>
      <div class="leg-amount">{coll['amount']:,.4f} LsETH</div>
      <div class="kv"><span>value</span><span class="mono">≈ {money(coll['usd'])}</span></div>
      <div class="kv"><span>venue</span><span>{ext(market_url, 'Morpho LsETH/USDC ↗')}</span></div>
      <div class="kv"><span>LLTV</span><span class="mono">{lltv:.1f}%</span></div>
    </div>
    <div class="arrow" aria-hidden="true">→</div>
    <div class="card leg">
      <div class="leg-head"><span class="leg-kind">Borrow · Morpho LsETH/USDC</span>{icon("morpho")}</div>
      <div class="leg-amount">{borrow['amount']:,.0f} USDC</div>
      <div class="kv"><span>LTV today</span><span class="mono">{ltv:.1f}%</span></div>
      <div class="kv"><span>per 1 LsETH</span><span class="mono">{per_unit_borrow:,.0f} USDC</span></div>
      <div class="kv"><span><b>borrow rate, live</b></span><span class="mono"><b>{borrow['apy_pct']:.2f}%</b></span></div>
    </div>
    <div class="arrow" aria-hidden="true">→</div>
    <div class="card leg">
      <div class="leg-head"><span class="leg-kind">Liquid sleeve · Morpho</span>{icon("morpho")}</div>
      <div class="leg-amount">{dep['amount']:,.0f} USDC</div>
      {vault_rows}
      <div class="kv"><span><b>blended, live</b></span><span class="mono"><b>{dep['apy_pct']:.2f}%</b></span></div>
    </div>
  </div>
  <div class="stat-sub">{esc(position_age_note)} KPK curates both sides of the LsETH/USDC market, and holds the borrow rate competitive with comparable 86%-LLTV USDC markets such as wstETH/USDC and rETH/USDC. LsETH is marked at {money(lseth_price)} by the market's oracle; vault yields are net of the vaults' fees. Verify the collateral and borrow legs on {ext(debank_url, 'DeBank ↗')}, which shows both positions but not the underlying yield.</div>
  {rebalance_card}
  {share_card}
  <div class="card">
    <div class="stat-label">Borrow rate and blended vault yield since the position opened</div>
    {legend}
    <div class="chart">
      {chart['svg']}
      <div id="chartTooltip" class="chart-tooltip" role="status">
        <div class="date" id="ttDate"></div>
        <div class="row"><span class="key" style="background:var(--kpk-ink)"></span><b>Blended</b><span class="val" id="ttBlend"></span></div>
        <div class="row"><span class="key" style="background:var(--c-borrow)"></span>Borrow<span class="val" id="ttBorrow"></span></div>
        <div class="row"><span style="width:10px"></span>Spread<span class="val" id="ttDelta"></span></div>
        <div class="row"><span class="key" style="background:var(--c-yield)"></span>Yield<span class="val" id="ttYield"></span></div>
        <div class="row"><span class="key" style="background:var(--c-rwa)"></span>Yield RWA<span class="val" id="ttRwa"></span></div>
      </div>
    </div>
    <div class="stat-sub">Daily borrow rate on the LsETH/USDC market against the fund's blended vault yield: both KPK USDC vaults' realized rates as a 7-day average, net of fees and including Merkl rewards, weighted by the fund's balance in each (KPK USDC Yield RWA since 27 Aug). The thin lines are the two vaults on their own. {chart['cap_note']}</div>
  </div>
  <div class="card">
    <details class="fold">
      <summary>Daily data table</summary>
      <div class="table-scroll">
        <table class="data">
          <thead><tr><th>Date</th><th class="num">Borrow APY</th><th class="num">Yield APY</th><th class="num">RWA APY</th><th class="num">Blended APY</th><th class="num">Spread (Blended - Borrow)</th></tr></thead>
          <tbody>
          {chart['table_rows']}
          </tbody>
        </table>
      </div>
    </details>
  </div>
</section>

<section id="simulator">
  <span class="label">Carry simulator</span>
  <div class="card">
    <div class="sim-head">
      <div><h2>Carry per 1 LsETH at a chosen LTV</h2>
      <p class="lede">Carry = spread × LTV × {fee_term}. The health factor sets LTV ({V['lltv']} LLTV ÷ health factor); the shaded segment is the health factor manager's band.</p></div>
      <div class="sim-out"><span class="big" id="sliderCarry">{sgn(carry_head)}%</span><span class="muted">a year on LsETH<br>at <b id="sliderLtv">{ltv:.1f}%</b> LTV, <span id="spreadBasis">{esc(B0['label'])}</span> spread, after fee</span></div>
    </div>
    <div class="spread-picker" role="group" aria-label="Which spread to simulate with">
      <span class="picker-label">Spread</span>
      {spread_buttons}
      <span class="picker-note"><b>Live</b> is today's borrow rate against the vaults' current yield. The trailing options pair the vaults' realised yield with the borrow rate over the same window, as a check.</span>
    </div>
    <div class="slider-wrap">
      <div class="ends"><span>0% LTV, no carry</span><span class="hi">liquidation at {lltv:.0f}% LTV</span></div>
      <input type="range" id="ltvSlider" min="0" max="{lltv:g}" step="0.1" value="{ltv:.1f}" aria-label="Loan-to-value, percent" style="{slider_bg}">
      <div class="marks" id="ltvMarks"></div>
    </div>
    <div class="note" id="simWarn" hidden>Above {V['repay_below_ltv']} LTV, a health factor below {V['repay_below']}, the health factor manager repays back to {V['repay_to_ltv']}.</div>
    <div class="sim-grid">
      <div class="sim-cell"><div class="k">Health factor</div><div class="v" id="simHf"></div></div>
      <div class="sim-cell"><div class="k">Borrowed per 1 LsETH</div><div class="v" id="simBorrow"></div></div>
      <div class="sim-cell"><div class="k">Carry per 1 LsETH</div><div class="v" id="simUsd"></div></div>
      <div class="sim-cell"><div class="k">LsETH drop to liquidation</div><div class="v" id="simDrop"></div></div>
    </div>
    <div class="sim-grid three">
      <div class="sim-cell"><div class="k">LTV needed for +1.5% a year</div><div class="v" id="ltvFor15"></div></div>
      <div class="sim-cell"><div class="k">LTV needed for +2.0% a year</div><div class="v" id="ltvFor20"></div></div>
      <div class="sim-cell"><div class="k">LTV needed for +2.5% a year</div><div class="v" id="ltvFor25"></div></div>
    </div>
    <div class="stat-sub" style="margin-top:12px">At live rates, LsETH at {money(lseth_price)}. The spread is held fixed: borrowing more pushes the market's USDC borrow rate up, so the high-LTV end reads high.</div>
  </div>
</section>

{risk_section()}
{scaling_section()}
<section id="terms">
  <span class="label">Risks and terms</span>
  <h2>{esc(MAIN_RISKS['title'])}</h2>
  <div class="card">
    <div class="risk-list">{risk_rows}</div>
  </div>
  <div class="sub-h">Subscriptions, redemptions and fees</div>
  <div class="grid g2">
    <div class="card">
      <div class="stat-label">Subscriptions and redemptions</div>
      <div class="redeem">{terms_tiles}</div>
      <div class="stat-sub" style="margin-top:10px">{esc(TERMS['note'])}</div>
    </div>
    <div class="card">
      <div class="stat-label">Fees and limits</div>
      {terms_rows}
    </div>
  </div>
</section>

<footer id="method">
  <p><a class="cta" href="{esc(CONFIG['app_url'])}" target="_blank" rel="noopener">View the fund in the KPK app ↗</a></p>
  <p><b>Data.</b> Live figures are read at each build from the Morpho API (api.morpho.org): the LsETH/USDC market, the trading subaccount's position, and the two KPK USDC vaults' net APY and share-price history. LsETH's staking APY comes from DefiLlama's Liquid Collective pool. LsETH is marked at the market oracle's price.</p>
  <p><b>Reading the numbers.</b> ≈ marks a derived or rounded figure. Carry = LTV × (vault yield - borrow rate) × (1 - {PERF_FEE * 100:.0f}% performance fee), on the LsETH held and on top of its staking yield. Carry figures use live and trailing rates read at each build, not a backtest, and are not promises.</p>
  <p><b>Updates.</b> Rebuilt every 4 hours from public, keyless sources; the page does not refresh itself in the browser ({ext(actions_url, 'rebuild now ↗')}). Contracts: {addr(wallet, 'trading subaccount')} · {addr(CONTRACTS['vault'], 'Upshift vault')} · {addr(CONTRACTS['shares'], 'shares')} · {addr(CONTRACTS['nav_calculator'], 'NAV calculator')}.</p>
</footer>

</div>
<script>window.SIM = {json.dumps(sim)};</script>
<script>{JS}</script>
<script>
(function() {{
  var borrow = {chart['borrow_js']};
  var yieldp = {chart['yield_js']};
  var rwap = {chart['rwa_js']};
  var blendp = {chart['blend_js']};
  var scale = {{ x0: {chart['scale']['x0']}, x1: {chart['scale']['x1']}, padL: {chart['scale']['pad_l']}, plotW: {chart['scale']['plot_w']} }};
  var svg = document.getElementById('rateChart');
  var rect = document.getElementById('hoverRect');
  var crosshair = document.getElementById('crosshair');
  var tooltip = document.getElementById('chartTooltip');
  var elDate = document.getElementById('ttDate');
  var elYield = document.getElementById('ttYield');
  var elRwa = document.getElementById('ttRwa');
  var elBorrow = document.getElementById('ttBorrow');
  var elBlend = document.getElementById('ttBlend');
  var elDelta = document.getElementById('ttDelta');

  function nearest(series, t) {{
    var lo = 0, hi = series.length - 1;
    while (lo < hi) {{
      var mid = (lo + hi) >> 1;
      if (series[mid][0] < t) lo = mid + 1; else hi = mid;
    }}
    if (lo > 0 && Math.abs(series[lo - 1][0] - t) < Math.abs(series[lo][0] - t)) lo -= 1;
    return series[lo];
  }}

  function fmtDate(t) {{
    return new Date(t * 1000).toLocaleDateString('en-GB', {{ year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' }});
  }}

  function show(e) {{
    var box = svg.getBoundingClientRect();
    var px = e.clientX - box.left;
    var frac = Math.max(0, Math.min(1, (px * (840 / box.width) - scale.padL) / scale.plotW));
    var t = scale.x0 + frac * (scale.x1 - scale.x0);
    var b = nearest(borrow, t), y = nearest(yieldp, t);
    var xv = (b[0] - scale.x0) / (scale.x1 - scale.x0) * scale.plotW + scale.padL;
    crosshair.setAttribute('x1', xv);
    crosshair.setAttribute('x2', xv);
    crosshair.setAttribute('visibility', 'visible');

    elDate.textContent = fmtDate(b[0]);
    elYield.textContent = y[1].toFixed(2) + '%';
    // RWA only exists from its launch date; the nearest() lookup would
    // otherwise pin every earlier hover to its first point.
    var rshow = 'n/a';
    if (rwap.length) {{
      var r = nearest(rwap, t);
      if (Math.abs(r[0] - t) < 129600) rshow = r[1].toFixed(2) + '%';
    }}
    elRwa.textContent = rshow;
    elBorrow.textContent = b[1].toFixed(2) + '%';
    var bl = blendp.length ? nearest(blendp, t) : null;
    var blend = (bl && Math.abs(bl[0] - t) < 129600) ? bl[1] : y[1];
    elBlend.textContent = blend.toFixed(2) + '%';
    var d = blend - b[1];
    elDelta.textContent = (d >= 0 ? '+' : '') + d.toFixed(2) + ' pp';

    // Keep the tooltip inside the chart box at both ends.
    var cx = xv / 840 * box.width, w = tooltip.offsetWidth || 160;
    tooltip.style.left = Math.max(w / 2, Math.min(box.width - w / 2, cx)) + 'px';
    tooltip.style.visibility = 'visible';
  }}
  if (rect) {{
    rect.addEventListener('pointermove', show);
    rect.addEventListener('pointerdown', show);
    rect.addEventListener('pointerleave', function() {{
      crosshair.setAttribute('visibility', 'hidden');
      tooltip.style.visibility = 'hidden';
    }});
  }}
}})();
</script>
{share_js}
{rebalance_js}
</body>
</html>
"""

OUT.write_text(page)
print(f"wrote {OUT} ({len(page):,} chars)")
