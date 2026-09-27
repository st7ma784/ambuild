"""Jinja filters for showing numbers, sizes, times and statuses."""
import datetime

STATUS_LABELS = {
    "finished": "Finished",
    "running": "Running",
    "failed": "Failed",
    "incomplete": "Incomplete",
}
STATUS_STATES = {"finished": "ok", "running": "run", "failed": "fail", "incomplete": "warn",
                 # submissions (the queue)
                 "queued": "warn", "claimed": "run", "submitted": "run", "cancelling": "warn", "cancelled": "warn"}


def number(value, digits=2):
    """A number with a sensible precision; an en dash for none"""
    if value is None or value == "":
        return "–"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return "{0:,}".format(value)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    if value != 0 and (abs(value) >= 1e6 or abs(value) < 10 ** -digits):
        return "{0:.{1}e}".format(value, max(1, digits))
    return "{0:,.{1}f}".format(value, digits)


def duration(seconds):
    if seconds is None:
        return "–"
    seconds = int(round(seconds))
    if seconds < 60:
        return "{0} s".format(seconds)
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return "{0} min {1:02d} s".format(minutes, seconds)
    hours, minutes = divmod(minutes, 60)
    return "{0} h {1:02d} min".format(hours, minutes)


def filesize(n):
    if n is None:
        return "–"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "{0} {1}".format(int(n), unit) if unit == "B" else "{0:.1f} {1}".format(n, unit)
        n /= 1024.0


def when(value):
    """A timestamp as UTC, to the minute"""
    if value is None:
        return "–"
    if isinstance(value, str):
        try:
            value = datetime.datetime.fromisoformat(value)
        except ValueError:
            return value
    if value.tzinfo is not None:
        value = value.astimezone(datetime.timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M") + " UTC"


def statusLabel(status):
    return STATUS_LABELS.get(status, (status or "unknown").capitalize())


def statusState(status):
    return STATUS_STATES.get(status, "warn")


def install(env):
    env.filters.update(number=number, duration=duration, filesize=filesize, when=when,
                       status_label=statusLabel, status_state=statusState)
