from typing import List, Dict, Optional

LANGUAGES = [
    ('English', 'en-IN'),
    ('Hindi', 'hi-IN'),
    ('Tamil', 'ta-IN'),
    ('Telugu', 'te-IN'),
    ('Malayalam', 'ml-IN'),
    ('Kannada', 'kn-IN'),
    ('Marathi', 'mr-IN'),
    ('Bengali', 'bn-IN'),
    ('Gujarati', 'gu-IN'),
    ('Spanish', 'es-ES'),
    ('French', 'fr-FR'),
    ('German', 'de-DE'),
    ('Mandarin', 'zh-CN'),
]

VOICE_LANGUAGES = [name for name, code in LANGUAGES]
LANGUAGE_CODES = {name.lower(): code for name, code in LANGUAGES}

MAX_VOCABULARY = 20
MAX_PHRASE_LENGTH = 60

from pydantic import BaseModel

class TranscriptionHints(BaseModel):
    languageCodes: List[str] = ['en-IN']
    customVocabulary: List[str] = []

def transcription_hints(source) -> TranscriptionHints:
    code = LANGUAGE_CODES.get(source.language.strip().lower())
    language_codes = [code, 'en-IN'] if code and code != 'en-IN' else ['en-IN']
    
    first_name = source.name.strip().split()[0] if source.name else None
    phrases = [
        'Ambition Gazette',
        'Gazzy',
    ]
    if first_name:
        phrases.append(first_name)
    phrases.extend(a.title for a in source.ambitions)
    phrases.extend(i.topic for i in source.interests if i.topic)
    
    custom_vocabulary = []
    seen = set()
    for p in phrases:
        pt = p.strip()
        if 1 < len(pt) <= MAX_PHRASE_LENGTH and pt not in seen:
            seen.add(pt)
            custom_vocabulary.append(pt)
            if len(custom_vocabulary) >= MAX_VOCABULARY:
                break
                
    return TranscriptionHints(
        languageCodes=language_codes,
        customVocabulary=custom_vocabulary
    )
