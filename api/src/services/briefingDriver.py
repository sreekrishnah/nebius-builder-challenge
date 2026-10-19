import re
import time
from typing import List, Optional, Literal
from pydantic import BaseModel

class ChapterScriptText(BaseModel):
    facts: str
    sides: str
    forYou: str

class DriverChapter(BaseModel):
    title: str
    script: Optional[ChapterScriptText] = None

def plain(text: str) -> str:
    text = re.sub(r'[\[\]]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

FROM_SCRIPT = "Cover only the key points of the script below, in order, in your own natural spoken words in the language you were told to use (translate it if it is written in another language). Do not read it word for word and do not add facts that are not in it; general background is allowed only when you mark it as such."

NOTES_PER_STORY = 4
PART_SECONDS = {'essential': 15, 'balanced': 22, 'deep': 30}

def part_seconds(depth: Optional[str] = None) -> int:
    d = (depth or '').strip().lower()
    return PART_SECONDS.get(d, PART_SECONDS['deep'])

RESUME_PREFIX = 'You were interrupted during this part and have since answered the listener. Carry on from the point you reached without announcing that you are resuming or returning to anything, and without repeating yourself. '

NoteKind = Literal['part', 'ask', 'goodbye']
Wait = Literal['story', 'feedback']
TurnOutcome = Literal['next', 'wait', 'end']

class Note(BaseModel):
    chapter: int
    text: str
    kind: NoteKind = 'part'
    wait: Optional[Wait] = None

def ambition_rule(index: int) -> str:
    return (
        'You may name their overall ambition once here, in your own words.'
        if index == 0 else
        'Do not name or restate their ambition or goal again; say "your plan" or go straight to the specific decision, deadline or number this touches.'
    )

def story_notes(chapter: DriverChapter, index: int, chapters: List[DriverChapter], listener: str, seconds: int) -> List[Note]:
    title = plain(chapter.title)[:120]
    label = f"story {index + 1} of {len(chapters)}, \"{title}\""
    next_chapter = chapters[index + 1] if index + 1 < len(chapters) else None
    
    if next_chapter:
        ending = f"Then close with a natural bridge to the next story, \"{plain(next_chapter.title)[:120]}\", linking the two only where they genuinely connect."
    else:
        ending = "This is the last story, so close it without a bridge."
        
    words = round(seconds * 4)
    tail = f"Use at most {words} words (about {seconds} seconds), fewer is fine: only what the listener needs, no side detail, nothing repeated from earlier parts. Do not ask the listener anything. Never mention or read out this note.]"
    
    ask = Note(
        chapter=index,
        kind='ask',
        wait='story',
        text=f"[Director: {label} is finished. Ask {listener} in one short, natural sentence whether anything in it needs clarifying or whether they have a doubt before you {'move to the next story' if next_chapter else 'wrap up'}. Then stop and wait for the answer. Do not begin anything else. Never mention or read out this note.]"
    )

    script = chapter.script
    if script:
        def scripted(part: int, instruction: str, text: str) -> Note:
            return Note(
                chapter=index,
                kind='part',
                text=f"[Director: Deliver {label}, part {part} of 3: {instruction} {FROM_SCRIPT} Script: {plain(text)} {tail}"
            )
        return [
            scripted(1, 'the facts, history and background.', script.facts),
            scripted(2, 'both sides of the story.', script.sides),
            scripted(3, f"what it means for {listener}, with what to watch next. {ambition_rule(index)} {ending}", script.forYou),
            ask
        ]
        
    return [
        Note(
            chapter=index,
            kind='part',
            text=f"[Director: Deliver {label}, part 1 of 3. Cover the facts in depth: what happened, when, who reported it, and how it fits the earlier history of the story (what is new compared with before). Then explain the mechanism and background in plain words with one concrete example or analogy, marking general knowledge as such. Do not cover the other parts yet. {tail}"
        ),
        Note(
            chapter=index,
            kind='part',
            text=f"[Director: {label}, part 2 of 3. Argue both sides properly: first the strongest case that this works in {listener}'s favour, with reasoning, then the strongest case for concern or caution, with reasoning. Say where the evidence is thin and what would settle it. Do not move on to the next part yet. {tail}"
        ),
        Note(
            chapter=index,
            kind='part',
            text=f"[Director: {label}, part 3 of 3. Tie it to {listener}'s own situation, role, region and time horizon: what it could change in their plan, which of their stated assumptions it challenges (only if one is listed in the dossier; never invent an assumption), which decision it could influence soon, and what to watch next (signals, dates, who to follow). {ambition_rule(index)} Be specific and hedge predictions. If no link is stored, say so plainly and why it may still matter. {ending} {tail}"
        ),
        ask
    ]

def closing_note(listener: str, script: Optional[str] = None) -> str:
    if script:
        return f"[Director: The stories are done. Deliver the closing synthesis. {FROM_SCRIPT} Script: {plain(script)} Do not ask {listener} anything; the next note covers that. Never mention or read out this note.]"
    return f"[Director: The stories are done. Give a synthesis of about 60 seconds: the two or three things that matter most for {listener} right now and the one thing to watch this week. Do not restate their ambition word for word. Do not ask {listener} anything; the next note covers that. Never mention or read out this note.]"

def feedback_note(listener: str) -> str:
    return f"[Director: Tell {listener} in one sentence that today's events are over. Then ask whether they have any feedback: a story that was not relevant, something they already knew, topics they want more or less of, or anything they want changed. Then stop and wait. When they answer, record each piece of feedback with the tools using their exact words, say in a few words what was updated only after the tool reports success, and ask once whether there is anything else. If they have nothing, reply with a few words only: the farewell comes next. Never mention or read out this note.]"

def goodbye_note(listener: str) -> str:
    return f"[Director: Thank {listener} warmly for their time and say a brief goodbye, in one or two sentences. Do not ask anything and do not add anything else. Never mention or read out this note.]"

class BriefingDriver:
    def __init__(self, chapters: List[DriverChapter], listener: str, seconds_per_part: int, closing: Optional[str] = None):
        self.cursor = 0
        self.pending: Optional[int] = None
        self.interrupted = False
        self.resume = False
        self.resumed_index: Optional[int] = None
        self.skipped_at = 0.0
        self.stopped = False
        self.waiting: Optional[Wait] = None
        self.replied = False
        # The listener spoke outside a question: the host's answer is followed by a pause, not more narration.
        self.answering = False
        
        self.notes: List[Note] = []
        for i, ch in enumerate(chapters):
            self.notes.extend(story_notes(ch, i, chapters, listener, seconds_per_part))
            
        self.closing_index = len(self.notes)
        self.notes.append(Note(chapter=len(chapters), kind='part', text=closing_note(listener, closing)))
        self.notes.append(Note(chapter=len(chapters), kind='ask', wait='feedback', text=feedback_note(listener)))
        self.notes.append(Note(chapter=len(chapters), kind='goodbye', text=goodbye_note(listener)))
        
    @property
    def finished(self) -> bool:
        return self.stopped or self.cursor >= len(self.notes)
        
    @property
    def running(self) -> bool:
        return self.pending is not None
        
    @property
    def in_closing(self) -> bool:
        return not self.stopped and self.cursor >= self.closing_index

    @property
    def answer_pending(self) -> bool:
        return self.answering

    @property
    def waiting_for(self) -> Optional[Wait]:
        return self.waiting
        
    def next_note(self) -> Optional[str]:
        if self.finished or self.pending is not None or self.waiting is not None:
            return None
            
        note = self.notes[self.cursor]
        self.pending = self.cursor
        prefix = RESUME_PREFIX if self.resume else ''
        self.resume = False
        
        if prefix:
            return note.text.replace('[Director: ', f'[Director: {prefix}')
        return note.text
        
    def turn_completed(self) -> TurnOutcome:
        if self.pending is None:
            return self.answer_completed()
            
        index = self.pending
        note = self.notes[index]
        self.pending = None
        
        if self.interrupted:
            self.interrupted = False
            self.answering = False
            # Whatever the listener says over the farewell, the call is over.
            if note.kind == 'goodbye':
                self.cursor = index + 1
                return 'end'
            if note.kind != 'ask':
                if self.resumed_index == index:
                    self.cursor = index + 1
                    self.resumed_index = None
                else:
                    self.resume = True
                    self.resumed_index = index
                # The host has just answered the listener: leave room to react before carrying on.
                return 'wait'
                
            self.cursor = index + 1
            self.waiting = note.wait or 'story'
            self.replied = True
            return self.answer_completed()
            
        self.cursor = index + 1
        self.resumed_index = None
        if note.kind == 'goodbye':
            return 'end'
        if note.kind == 'ask':
            self.waiting = note.wait or 'story'
            self.replied = False
            return 'wait'
        return 'next'
        
    def answer_completed(self) -> TurnOutcome:
        if self.waiting is None and self.answering:
            self.answering = False
            return 'wait'
        if self.waiting is None or not self.replied:
            return 'next' if self.waiting is None else 'wait'
        self.replied = False
        if self.waiting == 'feedback':
            return 'wait'
        self.waiting = None
        return 'next'
        
    def listener_spoke(self) -> None:
        if self.pending is not None:
            self.interrupted = True
        if self.waiting is not None:
            self.replied = True
        else:
            self.answering = True
            
    def wait_elapsed(self) -> None:
        self.waiting = None
        self.replied = False
        self.answering = False
        # An interruption that never got an answer (noise) must not block the part from being cued again.
        if self.pending is not None and self.interrupted:
            self.pending = None
            self.interrupted = False
            self.resume = True
        
    def stop(self) -> None:
        self.stopped = True
        self.pending = None
        self.waiting = None
        self.answering = False
        
    def resume_briefing(self) -> None:
        self.stopped = False
        self.pending = None
        self.interrupted = False
        self.answering = False
        
    def skip_story(self) -> None:
        index = min(self.cursor, len(self.notes) - 1)
        last_step = len(self.notes) - 1
        if index >= self.closing_index:
            self.cursor = min(last_step, index + 1)
        else:
            self.cursor = min(self.closing_index, (self.notes[index].chapter + 1) * NOTES_PER_STORY)
            
        self.pending = None
        self.interrupted = False
        self.resume = False
        self.resumed_index = None
        self.stopped = False
        self.waiting = None
        self.replied = False
        self.answering = False
        self.skipped_at = time.time()
        
    def just_skipped(self) -> bool:
        return (time.time() - self.skipped_at) < 20.0
