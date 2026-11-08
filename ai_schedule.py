"""Daily request windows in local time or a fixed GMT offset."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re


LOCAL_TIME = "本机时间"
TIMEZONE_CHOICES = [LOCAL_TIME] + [
    f"GMT{hour:+03d}:00" for hour in range(-12, 15)
] + ["GMT+05:30", "GMT+05:45", "GMT+09:30", "GMT+12:45"]


def normalize_timezone(value: str) -> str:
    if value in ("local", LOCAL_TIME):
        return LOCAL_TIME
    match = re.fullmatch(r"(?:GMT|UTC)\s*([+-])(\d{1,2})(?::([0-5]\d))?", value.strip().upper())
    if not match or int(match[2]) > 23:
        raise ValueError("时区请选择‘本机时间’，或填写 GMT+8、GMT-5、GMT+05:30 等；偏移须小于 24 小时。")
    hours, minutes = int(match[2]), int(match[3] or 0)
    sign = "+" if hours == minutes == 0 else match[1]
    return f"GMT{sign}{hours:02d}:{minutes:02d}"


def schedule_time(value: str, now: datetime | None = None) -> datetime:
    """Return an aware instant on the chosen clock; naive inputs mean local time."""
    value = normalize_timezone(value)
    now = now if now is not None else datetime.now(timezone.utc)
    if value == LOCAL_TIME:
        return now.astimezone()
    minutes = (int(value[4:6]) * 60 + int(value[7:9])) * (-1 if value[3] == "-" else 1)
    return now.astimezone(timezone(timedelta(minutes=minutes), value))


def parse_clock(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("AI 提交时间请使用 24 小时制 HH:MM，例如 01:00、07:00。")
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def validate_window(start: str, end: str) -> tuple[int, int]:
    first, last = parse_clock(start), parse_clock(end)
    if first == last:
        raise ValueError("AI 提交时段的开始和结束不能相同；全天提交请关闭时间限制。")
    return first, last


def next_allowed(now: datetime, start: str, end: str, not_before: datetime | None = None) -> datetime:
    """Half-open [start, end), including windows that cross midnight."""
    first, last = validate_window(start, end)
    # Persisted delays are instants, even after the user changes the selected zone.
    if not_before is not None and now.tzinfo is not None:
        not_before = not_before.astimezone(now.tzinfo)
    candidate = max(now, not_before) if not_before else now
    opening = candidate.replace(hour=first // 60, minute=first % 60, second=0, microsecond=0)
    minute = candidate.hour * 60 + candidate.minute
    inside = first <= minute < last if first < last else minute >= first or minute < last
    if inside:
        return candidate
    return opening if candidate < opening else opening + timedelta(days=1)


def next_retry(now: datetime, start: str, retry_after: str | None = None) -> datetime:
    """Defer limits until a later opening, respecting a later Retry-After."""
    first = parse_clock(start)
    opening = now.replace(hour=first // 60, minute=first % 60, second=0, microsecond=0)
    if opening <= now:
        opening += timedelta(days=1)
    if retry_after:
        try:
            if retry_after.strip().isdigit():
                advised = now + timedelta(seconds=int(retry_after))
            else:
                advised = parsedate_to_datetime(retry_after).astimezone(now.tzinfo)
                if now.tzinfo is None:
                    advised = advised.replace(tzinfo=None)
            opening = max(opening, advised)
        except (ValueError, TypeError, OverflowError):
            pass
    return opening


def is_usage_limit(error: dict) -> bool:
    code = str(error.get("code", "")).lower()
    message = str(error.get("message", "")).lower()
    return code in {"rate_limit_exceeded", "insufficient_quota", "usage_limit_reached", "usage_limit_exceeded"} or any(
        marker in message for marker in (
            "subscription sharing usage limit", "usage limit resets", "rate limit reached",
            "exceeded your current quota", "insufficient_quota",
        )
    )
