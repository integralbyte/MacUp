from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    value = dt or utc_now()
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def local_display(value: str | None) -> str:
    parsed = parse_iso(value)
    if parsed is None:
        return "Never"
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def relative_display(value: str | None, *, now: datetime | None = None) -> str:
    parsed = parse_iso(value)
    if parsed is None:
        return "never"
    current = (now or utc_now()).astimezone(timezone.utc)
    seconds = int((parsed - current).total_seconds())
    future = seconds > 0
    absolute = abs(seconds)

    def plural(value: int, unit: str) -> str:
        return f"{value} {unit}" + ("" if value == 1 else "s")

    def phrase(text: str) -> str:
        return f"in {text}" if future else f"{text} ago"

    if absolute < 60:
        return "in less than a minute" if future else "just now"
    if absolute < 3600:
        minutes = max(1, round(absolute / 60))
        return phrase(plural(minutes, "minute"))
    if absolute < 86400:
        hours = absolute // 3600
        minutes = (absolute % 3600) // 60
        text = plural(hours, "hour")
        if minutes:
            text += f" {plural(minutes, 'minute')}"
        return phrase(text)

    days = absolute // 86400
    if days == 1:
        return "tomorrow" if future else "yesterday"
    if days < 7:
        return phrase(plural(days, "day"))
    if days < 14:
        return "in 1 week" if future else "last week"
    if days < 30:
        return phrase(plural(round(days / 7), "week"))
    if days < 60:
        return "in 1 month" if future else "last month"
    if days < 365:
        return phrase(plural(round(days / 30), "month"))
    if days < 730:
        return "in 1 year" if future else "last year"
    return phrase(plural(round(days / 365), "year"))
