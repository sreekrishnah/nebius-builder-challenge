from typing import Literal
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone

Period = Literal['day', 'week', 'month', 'year']

class Bucket(BaseModel):
    period: Period
    # First calendar day of the bucket (YYYY-MM-DD): the day itself, the Monday, the 1st of the month, or 1 January.
    start: str

IST_OFFSET = timedelta(hours=5, minutes=30)
IST_TZ = timezone(IST_OFFSET)

def ist_day(at: datetime) -> str:
    """The IST calendar date (YYYY-MM-DD) of an instant."""
    return at.astimezone(IST_TZ).strftime('%Y-%m-%d')

def monday_of(day: str) -> str:
    # Parsed as UTC midnight
    date = datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    # weekday() is 0 for Monday, 6 for Sunday
    since_monday = date.weekday()
    monday = date - timedelta(days=since_monday)
    return monday.strftime('%Y-%m-%d')

def bucket_for(day: str, today: str) -> Bucket:
    """
    Where a day belongs in the cumulative history: days of the current week stay single days; earlier weeks of the
    current month are one entry per week; earlier months of the current year are one entry per month; earlier years
    are one entry per year. A week is never split, so it is placed by its Monday.
    """
    week_start = monday_of(day)
    today_monday = monday_of(today)
    
    if week_start >= today_monday:
        return Bucket(period='day', start=day)
    if week_start[:7] == today[:7]:
        return Bucket(period='week', start=week_start)
    if week_start[:4] == today[:4]:
        return Bucket(period='month', start=f"{week_start[:7]}-01")
    return Bucket(period='year', start=f"{week_start[:4]}-01-01")

def bucket_key(bucket: Bucket) -> str:
    return f"{bucket.period}:{bucket.start}"
