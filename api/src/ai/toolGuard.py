import re
import math
import unicodedata

WORD_PATTERN = re.compile(r'[\w]{2,}', flags=re.UNICODE)
MIN_QUOTE_WORDS = 2
REQUIRED_SHARE = 0.6

def get_words(text: str) -> list[str]:
    normalized = unicodedata.normalize('NFKC', text).lower()
    return WORD_PATTERN.findall(normalized)[:80]

def heard_request(recent_user_speech: str, quote: str) -> bool:
    if not quote:
        return False
    quoted = get_words(quote)
    if len(quoted) < MIN_QUOTE_WORDS:
        return False
    
    heard = set(get_words(recent_user_speech))
    matched = sum(1 for w in quoted if w in heard)
    
    return matched >= max(MIN_QUOTE_WORDS, math.ceil(len(quoted) * REQUIRED_SHARE))

CHANGING_TOOLS = ['update_preference', 'submit_feedback', 'track_event', 'untrack_event', 'control_briefing']

def is_changing_tool(name: str) -> bool:
    return name in CHANGING_TOOLS
