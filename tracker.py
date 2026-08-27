"""Daily fare tracker for BLR -> Milan, March/April 2027.

Runs once a day from GitHub Actions. For each configured route it makes two
SerpApi calls:

  1. a rotating probe date, so the whole Mar-Apr window gets covered over time
  2. a recheck of the cheapest date found so far, so a drop on the known-best
     date is caught immediately

Four routes x two calls = 8 calls a day, roughly 240 a month, which fits inside
SerpApi's free tier of 250.
"""

import csv
import json
import os
import smtplib
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
HISTORY_PATH = os.path.join(ROOT, "data", "history.csv")
STATE_PATH = os.path.join(ROOT, "data", "state.json")

HISTORY_FIELDS = [
    "checked_at",
    "route_id",
    "probe_type",
    "outbound_date",
    "return_date",
    "price",
    "airlines",
    "stops",
    "duration_minutes",
    "price_level",
    "typical_low",
    "typical_high",
]


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def window_dates(start_iso, end_iso):
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    span = (end - start).days
    return [(start + timedelta(days=offset)).isoformat() for offset in range(span + 1)]


def call_serpapi(api_key, params):
    query = dict(params)
    query["engine"] = "google_flights"
    query["api_key"] = api_key
    url = "https://serpapi.com/search?" + urllib.parse.urlencode(query)
    with urllib.request.urlopen(url, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def cheapest_itinerary(payload, travel_class=None):
    """Pick the lowest-priced itinerary across both result buckets.

    For a business search, itineraries with any leg in a lower cabin are thrown
    out. Google will happily return an economy feeder stitched to a business
    long-haul and label the whole thing business; logging that as a business
    fare would poison the price history.
    """
    wanted = {1: "Economy", 2: "Premium economy", 3: "Business", 4: "First"}.get(
        travel_class
    )

    candidates = []
    for bucket in ("best_flights", "other_flights"):
        for itinerary in payload.get(bucket, []) or []:
            if not isinstance(itinerary.get("price"), (int, float)):
                continue
            if wanted:
                cabins = [leg.get("travel_class") for leg in itinerary.get("flights", [])]
                if any(cabin != wanted for cabin in cabins):
                    continue
            candidates.append(itinerary)

    if not candidates:
        return None
    return min(candidates, key=lambda item: item["price"])


def summarise(itinerary):
    legs = itinerary.get("flights", []) or []
    airlines = sorted({leg.get("airline", "?") for leg in legs})
    return {
        "price": itinerary["price"],
        "airlines": " / ".join(airlines),
        "stops": max(len(legs) - 1, 0),
        "duration_minutes": itinerary.get("total_duration", ""),
    }


def probe(api_key, config, route, outbound_date, probe_type):
    params = {
        "departure_id": route["departure_id"],
        "arrival_id": route["arrival_id"],
        "outbound_date": outbound_date,
        "travel_class": route["travel_class"],
        "currency": config["currency"],
        "hl": config["hl"],
        "gl": config["gl"],
        "deep_search": "true",
        "show_hidden": "true",
    }

    return_date = ""
    if route.get("trip") == "round_trip":
        return_date = (
            date.fromisoformat(outbound_date)
            + timedelta(days=config["return_after_days"])
        ).isoformat()
        params["type"] = 1
        params["return_date"] = return_date
    else:
        params["type"] = 2

    if route.get("include_airlines"):
        params["include_airlines"] = route["include_airlines"]

    try:
        payload = call_serpapi(api_key, params)
    except Exception as error:  # noqa: BLE001 - never let one route kill the run
        print(f"  {route['id']} {outbound_date}: request failed ({error})")
        return None

    if payload.get("error"):
        print(f"  {route['id']} {outbound_date}: {payload['error']}")
        return None

    itinerary = cheapest_itinerary(payload, route["travel_class"])
    if itinerary is None:
        print(f"  {route['id']} {outbound_date}: no all-cabin-matching itineraries")
        return None

    insights = payload.get("price_insights") or {}
    typical = insights.get("typical_price_range") or ["", ""]

    row = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "route_id": route["id"],
        "probe_type": probe_type,
        "outbound_date": outbound_date,
        "return_date": return_date,
        "price_level": insights.get("price_level", ""),
        "typical_low": typical[0] if len(typical) > 0 else "",
        "typical_high": typical[1] if len(typical) > 1 else "",
    }
    row.update(summarise(itinerary))
    print(
        f"  {route['id']} {outbound_date} ({probe_type}): "
        f"{config['currency']} {row['price']:,} via {row['airlines']}"
    )
    return row


def read_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def append_history(rows):
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    is_new = not os.path.exists(HISTORY_PATH)
    with open(HISTORY_PATH, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def recent_prices(history, route_id, days=30):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    prices = []
    for row in history:
        if row["route_id"] != route_id:
            continue
        try:
            stamp = datetime.fromisoformat(row["checked_at"])
            price = float(row["price"])
        except (ValueError, KeyError):
            continue
        if stamp >= cutoff:
            prices.append(price)
    return prices


def evaluate(route, row, history, config, state):
    """Return an alert reason, or None if this fare isn't worth a notification."""
    price = row["price"]
    reasons = []

    if price <= route["target_price"]:
        reasons.append(f"at or below your target of {route['target_price']:,}")

    baseline = recent_prices(history, route["id"])
    if len(baseline) >= 8:
        median = statistics.median(baseline)
        if price <= median * config["drop_threshold"]:
            pct = round((1 - price / median) * 100)
            reasons.append(f"{pct}% under its own 30-day median of {round(median):,}")

    if not reasons:
        return None

    last_sent = state.get("last_alert", {}).get(route["id"])
    if last_sent:
        elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last_sent)
        if elapsed < timedelta(days=config["alert_cooldown_days"]):
            print(f"  {route['id']}: alert suppressed, still in cooldown")
            return None

    return " and ".join(reasons)


def send_email(subject, body):
    """Email via SMTP. Needs SMTP_USER, SMTP_PASS and SMTP_TO to be set."""
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    recipient = os.environ.get("SMTP_TO") or user
    if not user or not password:
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = user
    message["To"] = recipient
    message.set_content(body)

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, password)
            server.send_message(message)
        print(f"Emailed {recipient}.")
        return True
    except Exception as error:  # noqa: BLE001
        print(f"Email failed: {error}")
        return False


def open_issue(title, body):
    """Open a GitHub issue. GitHub emails you about issues in your own repo,
    so this works with no credentials beyond the token Actions already provides."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return False

    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=json.dumps({"title": title, "body": body, "labels": ["fare-drop"]}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "fare-tracker",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=30).read()
        print("Opened a GitHub issue.")
        return True
    except Exception as error:  # noqa: BLE001
        print(f"Issue creation failed: {error}")
        return False


def notify(subject, body):
    emailed = send_email(subject, body)
    issued = open_issue(subject, body)
    if not emailed and not issued:
        print("No delivery method worked. The alert is in this log only.")


def main():
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("SERPAPI_KEY is not set. Add it as a repository secret.")
        return 1

    config = load_json(CONFIG_PATH, None)
    state = load_json(STATE_PATH, {"cursor": {}, "best": {}, "last_alert": {}})
    history = read_history()
    dates = config.get("dates") or window_dates(
        config["window_start"], config["window_end"]
    )

    new_rows = []
    alerts = []

    for route in config["routes"]:
        print(route["label"])
        cursor = state["cursor"].get(route["id"], 0)
        probe_date = dates[cursor % len(dates)]
        state["cursor"][route["id"]] = cursor + config["rotate_step"]

        checks = [(probe_date, "rotating")]
        best = state["best"].get(route["id"])
        if best and best["outbound_date"] != probe_date:
            checks.append((best["outbound_date"], "recheck"))

        for outbound_date, probe_type in checks:
            row = probe(api_key, config, route, outbound_date, probe_type)
            if row is None:
                continue
            new_rows.append(row)

            if best is None or row["price"] < best["price"]:
                best = {"outbound_date": row["outbound_date"], "price": row["price"]}
                state["best"][route["id"]] = best

            reason = evaluate(route, row, history, config, state)
            if reason:
                alerts.append((route, row, reason))
                state.setdefault("last_alert", {})[route["id"]] = datetime.now(
                    timezone.utc
                ).isoformat()

    if new_rows:
        append_history(new_rows)
    save_json(STATE_PATH, state)

    if alerts:
        headline = alerts[0][0]["label"]
        subject = (
            f"Fare drop: {headline}"
            if len(alerts) == 1
            else f"Fare drops on {len(alerts)} routes"
        )
        lines = []
        for route, row, reason in alerts:
            lines.append(
                f"{route['label']}\n"
                f"{config['currency']} {row['price']:,} on {row['outbound_date']}"
                f" via {row['airlines']}, {row['stops']} stop(s)\n"
                f"Why: {reason}\n"
            )
        lines.append("Reconfirm on the airline's own site before booking.")
        body = "\n".join(lines)
        print(subject + "\n" + body)
        notify(subject, body)
    else:
        print("No alerts today.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
