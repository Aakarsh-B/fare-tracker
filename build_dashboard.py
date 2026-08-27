"""Render data/history.csv into docs/index.html, a static fare board.

Run straight after tracker.py. GitHub Pages serves the docs/ folder, so the
board is a URL you can keep open on your phone.
"""

import csv
import html
import json
import os
from collections import defaultdict
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
HISTORY_PATH = os.path.join(ROOT, "data", "history.csv")
OUTPUT_PATH = os.path.join(ROOT, "docs", "index.html")

MAX_POINTS = 40


def read_rows():
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, newline="", encoding="utf-8") as handle:
        rows = []
        for row in csv.DictReader(handle):
            try:
                row["price"] = float(row["price"])
            except (TypeError, ValueError):
                continue
            rows.append(row)
        return rows


def sparkline(prices, target):
    """Inline SVG trend strip with a dashed line marking the target price."""
    if len(prices) < 2:
        return '<div class="spark spark--empty">not enough readings yet</div>'

    width, height, pad = 300, 44, 4
    low = min(min(prices), target)
    high = max(max(prices), target)
    span = (high - low) or 1
    step = width / (len(prices) - 1)

    def y_for(value):
        return round(height - pad - ((value - low) / span) * (height - 2 * pad), 1)

    points = " ".join(
        f"{round(index * step, 1)},{y_for(value)}" for index, value in enumerate(prices)
    )
    target_y = y_for(target)
    last_x = round((len(prices) - 1) * step, 1)
    last_y = y_for(prices[-1])
    tone = "good" if prices[-1] <= target else "watch"

    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" role="img" '
        f'aria-label="Price trend over the last {len(prices)} checks">'
        f'<line x1="0" y1="{target_y}" x2="{width}" y2="{target_y}" '
        f'class="spark-target" />'
        f'<polyline points="{points}" class="spark-line" />'
        f'<circle cx="{last_x}" cy="{last_y}" r="3.5" class="dot dot--{tone}" />'
        f"</svg>"
    )


def build_card(route, rows, currency):
    history = sorted(rows, key=lambda item: item["checked_at"])[-MAX_POINTS:]
    prices = [row["price"] for row in history]
    target = route["target_price"]

    if not history:
        return (
            f'<article class="route route--empty">'
            f'<h2>{html.escape(route["label"])}</h2>'
            f'<p class="note">No readings yet. The first run fills this in.</p>'
            f"</article>"
        )

    best_row = min(rows, key=lambda item: item["price"])
    latest = history[-1]
    delta = latest["price"] - prices[-2] if len(prices) > 1 else 0

    if best_row["price"] <= target:
        state_label, tone = "Below target", "good"
    elif best_row["price"] <= target * 1.15:
        state_label, tone = "Close", "watch"
    else:
        state_label, tone = "Holding high", "high"

    latest_price = f"{currency} {round(latest['price']):,}"
    if delta < 0:
        move = (
            f'<span class="move move--down">latest {latest_price} '
            f"&#9660;{abs(round(delta)):,}</span>"
        )
    elif delta > 0:
        move = (
            f'<span class="move move--up">latest {latest_price} '
            f"&#9650;{round(delta):,}</span>"
        )
    else:
        move = f'<span class="move move--flat">latest {latest_price}, flat</span>'

    return f"""<article class="route">
  <header class="route-head">
    <h2>{html.escape(route["label"])}</h2>
    <span class="flag flag--{tone}">{state_label}</span>
  </header>
  <p class="note">{html.escape(route.get("note", ""))}</p>
  <div class="figure">
    <span class="currency">{currency}</span>
    <span class="price">{round(best_row["price"]):,}</span>
    {move}
  </div>
  <dl class="facts">
    <div><dt>Cheapest date</dt><dd>{html.escape(best_row["outbound_date"])}</dd></div>
    <div><dt>Carrier</dt><dd>{html.escape(best_row["airlines"] or "—")}</dd></div>
    <div><dt>Stops</dt><dd>{html.escape(str(best_row["stops"]))}</dd></div>
    <div><dt>Your target</dt><dd>{currency} {target:,}</dd></div>
  </dl>
  {sparkline(prices, target)}
  <p class="meta">{len(rows)} readings &middot; last checked {html.escape(latest["checked_at"][:10])}</p>
</article>"""


STYLES = """
:root {
  --ink: #0e1a1c;
  --panel: #142528;
  --rule: #24393c;
  --bone: #e6e2d6;
  --dim: #8a9a99;
  --good: #4fa87a;
  --watch: #d9a441;
  --high: #c0563f;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 28px 18px 64px;
  background: var(--ink);
  color: var(--bone);
  font-family: "IBM Plex Sans", system-ui, sans-serif;
  font-size: 15px;
  line-height: 1.5;
}
.wrap { max-width: 560px; margin: 0 auto; }
.masthead { border-bottom: 2px solid var(--rule); padding-bottom: 14px; margin-bottom: 8px; }
.masthead h1 {
  font-family: "Archivo Narrow", system-ui, sans-serif;
  font-weight: 700;
  font-size: 30px;
  letter-spacing: 0.02em;
  text-transform: uppercase;
  margin: 0;
}
.masthead p { margin: 6px 0 0; color: var(--dim); font-size: 13px; }
.countdown {
  font-family: "IBM Plex Mono", monospace;
  color: var(--watch);
}
.route {
  border-bottom: 1px solid var(--rule);
  padding: 22px 0;
}
.route-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.route h2 {
  font-family: "Archivo Narrow", system-ui, sans-serif;
  font-weight: 600;
  font-size: 19px;
  letter-spacing: 0.01em;
  margin: 0;
}
.flag {
  font-family: "IBM Plex Mono", monospace;
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  padding: 3px 7px;
  border: 1px solid currentColor;
  white-space: nowrap;
}
.flag--good { color: var(--good); }
.flag--watch { color: var(--watch); }
.flag--high { color: var(--high); }
.note { color: var(--dim); font-size: 13px; margin: 4px 0 0; }
.figure { display: flex; align-items: baseline; gap: 8px; margin: 14px 0 12px; flex-wrap: wrap; }
.figure .move { flex-basis: 100%; }
.currency { font-family: "IBM Plex Mono", monospace; font-size: 13px; color: var(--dim); }
.price {
  font-family: "Archivo Narrow", system-ui, sans-serif;
  font-weight: 700;
  font-size: 44px;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.move { font-family: "IBM Plex Mono", monospace; font-size: 12px; }
.move--down { color: var(--good); }
.move--up { color: var(--high); }
.move--flat { color: var(--dim); }
.facts { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 16px; margin: 0 0 16px; }
.facts div { border-left: 2px solid var(--rule); padding-left: 9px; }
.facts dt {
  font-family: "IBM Plex Mono", monospace;
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--dim);
}
.facts dd { margin: 2px 0 0; font-size: 14px; font-variant-numeric: tabular-nums; }
.spark { width: 100%; height: 44px; display: block; }
.spark--empty { color: var(--dim); font-size: 12px; height: auto; }
.spark-line { fill: none; stroke: var(--bone); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
.spark-target { stroke: var(--watch); stroke-width: 1; stroke-dasharray: 3 4; vector-effect: non-scaling-stroke; }
.dot--good { fill: var(--good); }
.dot--watch { fill: var(--watch); }
.meta {
  font-family: "IBM Plex Mono", monospace;
  font-size: 11px;
  color: var(--dim);
  margin: 10px 0 0;
}
footer { color: var(--dim); font-size: 12px; margin-top: 26px; }
@media (max-width: 380px) { .facts { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""


def main():
    with open(CONFIG_PATH, encoding="utf-8") as handle:
        config = json.load(handle)

    rows = read_rows()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["route_id"]].append(row)

    cards = "\n".join(
        build_card(route, grouped.get(route["id"], []), config["currency"])
        for route in config["routes"]
    )

    days_left = (date.fromisoformat(config["window_start"]) - date.today()).days
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fare board &middot; Bengaluru to Milan</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo+Narrow:wght@600;700&family=IBM+Plex+Mono:wght@400&family=IBM+Plex+Sans:wght@400;500&display=swap" rel="stylesheet">
<style>{STYLES}</style>
</head>
<body>
<div class="wrap">
  <header class="masthead">
    <h1>Bengaluru &rarr; Milan</h1>
    <p>Business class hunt &middot; travel window {config["window_start"]} to {config["window_end"]}
       &middot; <span class="countdown">{days_left} days out</span></p>
  </header>
  {cards}
  <footer>
    Built {stamp}. Prices in {config["currency"]}, cheapest seen per route.
    Dashed line marks your target. Always reconfirm on the airline's own site before booking.
  </footer>
</div>
</body>
</html>
"""

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        handle.write(document)
    print(f"Wrote {OUTPUT_PATH} from {len(rows)} readings.")


if __name__ == "__main__":
    main()
