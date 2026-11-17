"""Hourly scheduling shared by the API and background worker."""
from datetime import datetime


def next_hour(now):
    """Find the next local hour boundary on the timeline, including DST folds."""
    candidate = (int(now) // 60 + 1) * 60
    while datetime.fromtimestamp(candidate).minute != 0:
        candidate += 60
    return candidate


def scheduled_scan(due, now):
    """Estimate the hourly scan that first sees a subscription due at `due`."""
    moment = max(int(due), int(now))
    try:
        return next_hour(moment - 1)  # A boundary itself is a scan time.
    except (OverflowError, OSError, ValueError):  # Beyond datetime's range.
        return moment


