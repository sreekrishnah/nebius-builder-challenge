from datetime import datetime, timedelta, timezone

IST_OFFSET = timedelta(hours=5, minutes=30)
SCHEDULED_HOUR_IST = 6
CATCH_UP_UNTIL_HOUR_IST = 9

def ist(now: datetime) -> datetime:
    if now.tzinfo:
        now_utc = now.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        now_utc = now
    return now_utc + IST_OFFSET

def is_scheduled_minute(now: datetime = None) -> bool:
    if now is None:
        now = datetime.utcnow()
    t = ist(now)
    return t.hour == SCHEDULED_HOUR_IST and t.minute == 0

def is_catch_up_window(now: datetime = None) -> bool:
    if now is None:
        now = datetime.utcnow()
    t = ist(now)
    return SCHEDULED_HOUR_IST <= t.hour < CATCH_UP_UNTIL_HOUR_IST

def batch_slot(now: datetime = None) -> datetime:
    if now is None:
        now = datetime.utcnow()
    t = ist(now)
    dt_ist_6am = datetime(t.year, t.month, t.day, SCHEDULED_HOUR_IST, tzinfo=timezone.utc)
    return dt_ist_6am - IST_OFFSET
