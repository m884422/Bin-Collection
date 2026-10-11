#!/usr/bin/env python3
"""Build a rolling, two-month iCalendar feed from Manchester City Council.

Set MCC_UPRN in the environment (use a GitHub Actions secret when deploying).
The feed includes the current and following full calendar month.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

CHECKER_PAGE = (
    "https://manchester.portal.uk.empro.verintcloudservices.com/"
    "site/myaccount/request/sr_bin_coll_day_checker"
)
API_ROOT = "https://manchester.form.uk.empro.verintcloudservices.com/api"
FORM_NAME = "sr_bin_coll_day_checker"
LOCAL_TZ = ZoneInfo("Europe/London")

# These are the live response fields used by Manchester's own checker. Labels
# are deliberately presentation-friendly and never expose the council's field names.
BIN_LABELS = {
    "ahtm_dates_black_bin": "Grey",
    "ahtm_dates_brown_commingled_bin": "brown",
    "ahtm_dates_green_organic_bin": "green",
    "ahtm_dates_blue_pulpable_bin": "Blue",
}

COUNCIL_CHECKER_URL = CHECKER_PAGE
EVENT_DESCRIPTION = (
    "Manchester City Council collection. Put the listed bin or bins out by "
    "07:00 on collection day. Check the council's live checker for changes."
)


def get_csrf_token(page_html: str) -> str:
    """Get the public form's CSRF token from the council checker page."""
    matches = re.findall(
        r'<meta\s+name=["\']_csrf_token["\']\s+content=["\']([^"\']+)',
        page_html,
        flags=re.IGNORECASE,
    )
    if not matches:
        raise RuntimeError("Could not read the council checker's CSRF token")
    return matches[0]


def council_dates(uprn: str, from_date: date, to_date: date) -> dict[str, str]:
    """Request live dates from the public Manchester City Council form API."""
    session = requests.Session()
    page = session.get(CHECKER_PAGE, timeout=30)
    page.raise_for_status()
    csrf = get_csrf_token(page.text)

    listing = session.get(
        f"{API_ROOT}/citizen",
        params={"archived": "Y", "preview": "false", "locale": "en"},
        timeout=30,
    )
    listing.raise_for_status()
    authorization = listing.headers.get("Authorization")
    if not authorization:
        raise RuntimeError("Council form API did not return an authorization header")

    request_body = {
        "name": FORM_NAME,
        "data": {
            "uprn": uprn,
            "nextCollectionFromDate": from_date.isoformat(),
            "nextCollectionToDate": to_date.isoformat(),
        },
        "email": "",
        "caseid": "",
        "xref": "",
        "xref1": "",
        "xref2": "",
    }
    response = session.post(
        f"{API_ROOT}/custom",
        params={
            "action": "bin_checker-get_bin_col_info",
            "actionedby": "_KDF_custom",
            "loadform": "true",
            "access": "citizen",
            "locale": "en",
        },
        json=request_body,
        headers={"Authorization": authorization, "X-CSRF-TOKEN": csrf},
        timeout=30,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Council form API returned non-JSON data") from exc

    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Council form API response did not contain bin date data")
    if data.get("txt_col_service_type") == "no_collections_at_this_time":
        raise RuntimeError(
            "Council checker reported no collection service for this property; "
            "keeping the previous feed rather than publishing an empty calendar"
        )
    return data


def parse_dates(value: str | None) -> list[date]:
    """Extract DD/MM/YYYY collection dates from a council response field."""
    text = html.unescape(value or "")
    found: set[date] = set()
    for match in re.finditer(r"\b(\d{2}/\d{2}/\d{4})\b", text):
        found.add(datetime.strptime(match.group(1), "%d/%m/%Y").date())
    return sorted(found)


def month_window(today: date, months: int = 2) -> tuple[date, date]:
    """Return the first day of this month through the end of the next month."""
    first = date(today.year, today.month, 1)
    after_index = today.year * 12 + (today.month - 1) + months
    after_first = date(after_index // 12, after_index % 12 + 1, 1)
    return first, after_first - timedelta(days=1)


def ical_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def fold_line(line: str, limit: int = 75) -> str:
    """Fold an iCalendar content line at 75 UTF-8 octets (RFC 5545)."""
    remaining = line.encode("utf-8")
    output: list[bytes] = []
    first = True
    while len(remaining) > (limit if first else limit - 1):
        max_payload = limit if first else limit - 1
        cut = max_payload
        while cut > 0 and cut < len(remaining) and (remaining[cut] & 0xC0) == 0x80:
            cut -= 1
        prefix = b"" if first else b" "
        output.append(prefix + remaining[:cut])
        remaining = remaining[cut:]
        first = False
    prefix = b"" if first else b" "
    output.append(prefix + remaining)
    return "\r\n".join(part.decode("utf-8") for part in output)


def absolute_alarm(day: date, local_time: time, description: str, now_local: datetime) -> list[str]:
    """Make an exact Europe/London alarm using an RFC 5545 UTC trigger."""
    alarm_local = datetime.combine(day, local_time, tzinfo=LOCAL_TZ)
    if alarm_local <= now_local:
        return []  # Don't publish reminders whose time has already passed.
    trigger_utc = alarm_local.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return [
        "BEGIN:VALARM",
        f"TRIGGER;VALUE=DATE-TIME:{trigger_utc}",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{ical_escape(description)}",
        "END:VALARM",
    ]


def collection_phrase(labels: list[str]) -> str:
    """Natural-language bin names for reminders; e.g. 'grey, brown and green bins'."""
    lower = [label.lower() for label in labels]
    if len(lower) == 1:
        return f"{lower[0]} bin"
    return f"{', '.join(lower[:-1])} and {lower[-1]} bins"


def make_calendar(
    data: dict[str, str], start: date, end: date, now_local: datetime
) -> tuple[str, int]:
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    dates_to_labels: dict[date, list[str]] = {}
    for field, label in BIN_LABELS.items():
        for collection_day in parse_dates(data.get(field)):
            if start <= collection_day <= end:
                labels = dates_to_labels.setdefault(collection_day, [])
                if label not in labels:
                    labels.append(label)

    if not dates_to_labels:
        raise RuntimeError(
            "Council checker returned no collection dates for the month window; "
            "keeping the previous feed rather than publishing an empty calendar"
        )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Arena//Manchester Council Bin Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Manchester Bin Collections",
        "X-WR-CALDESC:Current and following month from Manchester City Council",
        "X-WR-TIMEZONE:Europe/London",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
    ]

    # One all-day calendar entry per collection date; no property address or UPRN.
    for collection_day in sorted(dates_to_labels):
        labels = dates_to_labels[collection_day]
        label_text = ", ".join(labels)
        summary = f"Bin Collection - {label_text}"
        phrase = collection_phrase(labels)
        next_day = collection_day + timedelta(days=1)
        uid = f"mcc-bin-collection-{collection_day:%Y%m%d}@bin-calendar.invalid"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{stamp}",
                f"LAST-MODIFIED:{stamp}",
                "SEQUENCE:0",
                f"DTSTART;VALUE=DATE:{collection_day:%Y%m%d}",
                f"DTEND;VALUE=DATE:{next_day:%Y%m%d}",
                f"SUMMARY:{ical_escape(summary)}",
                f"DESCRIPTION:{ical_escape(EVENT_DESCRIPTION)}",
                f"URL:{COUNCIL_CHECKER_URL}",
                "CATEGORIES:Waste",
                "STATUS:CONFIRMED",
                "TRANSP:TRANSPARENT",
            ]
        )

        # The previous-evening alarm is 21:00 London; the day-of alarm is 07:00.
        # Absolute UTC trigger times retain these wall-clock times across GMT/BST.
        previous_evening = collection_day - timedelta(days=1)
        lines.extend(
            absolute_alarm(
                previous_evening,
                time(21, 0),
                f"Put out {phrase} tonight for tomorrow's collection.",
                now_local,
            )
        )
        lines.extend(
            absolute_alarm(
                collection_day,
                time(7, 0),
                f"Collection today: put out {phrase} by 07:00.",
                now_local,
            )
        )
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n", len(dates_to_labels)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="site/bin-collections.ics",
        help="where to write the calendar (default: site/bin-collections.ics)",
    )
    parser.add_argument(
        "--as-of",
        help="override today's London date (YYYY-MM-DD; useful for testing)",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=2,
        help="full calendar months to include, starting with this month (default: 2)",
    )
    args = parser.parse_args()

    uprn = os.environ.get("MCC_UPRN", "").strip()
    if not re.fullmatch(r"\d{6,12}", uprn):
        print(
            "Set MCC_UPRN to the property's UPRN (use a GitHub Actions secret "
            "when publishing).",
            file=sys.stderr,
        )
        return 2
    if args.months < 1 or args.months > 3:
        print("--months must be between 1 and 3", file=sys.stderr)
        return 2

    try:
        today = (
            date.fromisoformat(args.as_of)
            if args.as_of
            else datetime.now(LOCAL_TZ).date()
        )
        start, end = month_window(today, args.months)
        # A test date assumes the scheduled 00:10 local run. Normally use the
        # actual London time so a manual run never publishes already-past alarms.
        now_local = (
            datetime.combine(today, time(0, 10), tzinfo=LOCAL_TZ)
            if args.as_of
            else datetime.now(LOCAL_TZ)
        )
        data = council_dates(uprn, start, end)
        calendar_text, event_count = make_calendar(data, start, end, now_local)
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(calendar_text.encode("utf-8"))
        print(
            f"Wrote {event_count} collection-date entries for the complete window "
            f"{start.isoformat()} to {end.isoformat()} to {target}"
        )
        return 0
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"Calendar refresh failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
