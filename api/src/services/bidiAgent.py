import asyncio
import time
import base64
import re
from typing import Dict, Any, List, Optional, Literal

from google.genai import types
from src.ai.tasks import summarize_session
from src.ai.liveInstruction import build_live_instruction
from src.ai.liveSpeech import transcription_hints, TranscriptionHints
from src.lib.errors import errors, AppError
from src.lib.gemini import ai, MODELS
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_void
from src.services.bidi import SessionRefs, execute_tool, load_session_context, SessionContext
from src.services.briefingDriver import BriefingDriver, part_seconds, DriverChapter, ChapterScriptText
from src.services.script import get_latest_script
from src.services.voiceUsage import add_voice_tokens
from src.config.env import env

log = create_logger('live')

INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000

IDLE_TIMEOUT_MS = 5 * 60 * 1000
MAX_SESSION_MS = 40 * 60 * 1000
PROCESSING_DELAY_MS = 700
LEVEL_LOG_INTERVAL_MS = 5000
NEXT_PART_DELAY_MS = 150
ASK_WAIT_MS = 7000
FOLLOW_UP_WAIT_MS = 6000
FAREWELL_GRACE_MS = 2500
UNANSWERED_WAIT_MS = 25000

# A listener saying goodbye, or thanking the host once the stories are done, ends the call a moment after the reply.
GOODBYE = re.compile(r"(bye|goodbye|good bye|end (the )?(call|session)|hang up|that'?s all|that is all)", re.IGNORECASE)
THANKS = re.compile(r'(thank you|thanks|thankyou)', re.IGNORECASE)
END_AFTER_REPLY_MS = 3000
END_FALLBACK_MS = 10000
MOVE_ON = re.compile(r'\b(skip|move on|next (story|event|topic|briefing|news|one)|(move|go|jump) (on )?to (the )?next)\b', re.IGNORECASE)

LiveState = Literal['connecting', 'listening', 'user_speaking', 'processing', 'agent_speaking', 'held', 'ended', 'error']
Role = Literal['user', 'agent']

class TranscriptEntry:
    def __init__(self, role: Role, text: str):
        self.role = role
        self.text = text

class LiveEmitter:
    def state(self, state: LiveState) -> None: pass
    def audio(self, turn: int, data: bytes) -> None: pass
    def interrupted(self, turn: int) -> None: pass
    def transcript(self, role: Role, text: str, turn: int, final: bool) -> None: pass
    def tool(self, name: str, status: Literal['started', 'done', 'error']) -> None: pass
    def error(self, code: str, message: str) -> None: pass
    def ended(self, reason: str) -> None: pass

active: Dict[str, 'LiveSession'] = {}

async def end_session_for(user_id: str, reason: str) -> None:
    session = active.get(user_id)
    if session:
        await session.end(reason)

async def end_all_sessions(reason: str) -> None:
    for session in list(active.values()):
        await session.end(reason)

class LiveSession:
    def __init__(self, user_id: str, out: LiveEmitter):
        self.user_id = user_id
        self.out = out
        self.session = None
        self._live_context = None
        self.refs = SessionRefs()
        self.state: LiveState = 'connecting'
        self.turn = 1
        self.muted = False
        self.held = False
        self.closing = False
        self.db_session_id: Optional[str] = None
        self.instruction = ''
        self.hints = TranscriptionHints(languageCodes=['en-IN'], customVocabulary=[])
        self.transcript: List[TranscriptEntry] = []
        self.open_role: Optional[Role] = None
        self.last_activity = time.time() * 1000
        self.started_at = time.time() * 1000
        self.watchdog: Optional[asyncio.TimerHandle] = None
        self.driver: Optional[BriefingDriver] = None
        self.continue_timer: Optional[asyncio.TimerHandle] = None
        self.end_timer: Optional[asyncio.TimerHandle] = None
        self.processing_timer: Optional[asyncio.TimerHandle] = None
        self.tools_in_flight = 0
        self.move_on_handled = False
        self.end_requested = False
        self.tokens_used = 0
        self.input_frames = 0
        self.input_sum_squares = 0
        self.input_peak = 0
        self.last_level_log = time.time() * 1000
        self._receive_task: Optional[asyncio.Task] = None

    @property
    def id(self) -> Optional[str]:
        return self.db_session_id

    async def start(self, language: Optional[str] = None) -> str:
        await end_session_for(self.user_id, 'replaced')
        active[self.user_id] = self
        self.set_state('connecting')

        try:
            ctx = await load_session_context(self.user_id)
            if language and language != ctx.language:
                ctx.language = language
                await self.save_default_language(language)

            self.instruction = build_live_instruction(ctx, self.refs)
            self.hints = transcription_hints(ctx)
            self.refs.heard = lambda: self.recent_user_speech()
            await self.set_up_driver(ctx)

            self._live_context = None
            insert_res = supabase.table('bidi_sessions').insert({
                "user_id": self.user_id,
                "language": ctx.language,
                "status": 'active'
            }).select('id').execute()
            rows = unwrap('bidi_sessions.insert', insert_res)
            rows = rows if isinstance(rows, list) else [rows]
            if not rows:
                raise Exception("Failed to insert bidi_session")
            self.db_session_id = rows[0]['id']
            
            await self.connect()
            
            loop = asyncio.get_running_loop()
            self.watchdog = loop.call_later(15.0, self.check_limits)
            
            log.info('session started', {"userId": self.user_id, "sessionId": self.db_session_id, "model": MODELS['live']})
            return self.db_session_id
        except Exception as err:
            if self.user_id in active:
                del active[self.user_id]
            await self.cleanup()
            log.error('session failed to start', {"userId": self.user_id, "err": str(err)})
            if isinstance(err, AppError):
                raise err
            raise AppError('MODEL_UNAVAILABLE', 502, 'Voice session could not be started.', str(err))

    async def save_default_language(self, language: str) -> None:
        try:
            res = supabase.table('user_intelligence_profiles').update({"prefered_language": language}).eq('user_id', self.user_id).execute()
            unwrap_void('profile.language.update', res)
        except Exception as err:
            log.warn('default language was not saved', {"userId": self.user_id, "err": str(err)})

    async def connect(self) -> None:
        from src.services.bidi import get_gemini_tools
        tools = get_gemini_tools()
        
        self._live_context = ai.aio.live.connect(
            model=MODELS['live'],
            config=types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='Aoede')
                    )
                ),
                input_audio_transcription=types.AudioTranscriptionConfig(
                    language_codes=self.hints.languageCodes,
                    custom_vocabulary=self.hints.customVocabulary
                ),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                system_instruction=types.Content(parts=[types.Part.from_text(text=self.instruction)]),
                tools=tools,
            )
        )
        self.session = await self._live_context.__aenter__()
        
        opening = (
            '[The session has just opened. Greet the listener in one or two sentences, say what is coming, then stop and wait for the producer\'s note. Do not begin the first story yet.]'
            if self.driver else
            '[The session has just opened. Greet the listener as instructed.]'
        )
        
        await self.session.send_client_content(
            turns=[types.Content(role='user', parts=[types.Part.from_text(text=opening)])],
            turn_complete=True
        )
        self.set_state('listening')
        
        self._receive_task = asyncio.create_task(self.receive_loop())

    async def receive_loop(self):
        try:
            while not self.closing and self.session:
                message = await self.session._receive()
                await self.handle_message(message)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if not self.closing:
                log.warn('live socket error or closed', {"sessionId": self.db_session_id, "message": str(e)})
                self.handle_close(1006, str(e))

    async def send_audio(self, data: bytes) -> None:
        if self.closing or not self.session or self.muted or self.held:
            return
        self.last_activity = time.time() * 1000
        self.track_input_level(data)
        
        try:
            await self.session.send_realtime_input(audio=types.Blob(data=data, mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}"))
        except Exception as exc:
            if not self.closing:
                self.handle_close(1011, str(exc))

    def track_input_level(self, data: bytes) -> float:
        import struct
        if not data:
            return 0.0
        count = len(data) // 2
        if count == 0:
            return 0.0
        try:
            shorts = struct.unpack(f"<{count}h", data[:count * 2])
            sum_sq = sum(s * s for s in shorts)
            rms = (sum_sq / count) ** 0.5
            self.input_frames += 1
            self.last_activity = time.time() * 1000
            return rms
        except Exception:
            return 0.0

    def set_muted(self, muted: bool) -> None:
        if self.closing or self.muted == muted:
            return
        self.muted = muted
        if muted and self.session:
            asyncio.create_task(self.session.send_realtime_input(audio_stream_end=True))
        log.info('mute changed', {"sessionId": self.db_session_id, "muted": muted})

    def set_held(self, held: bool) -> None:
        if self.closing or self.held == held:
            return
        self.held = held
        if held:
            if self.session:
                asyncio.create_task(self.session.send_realtime_input(audio_stream_end=True))
            self.set_state('held')
            self.clear_continue_timer()
        else:
            self.set_state('listening')
            if self.driver:
                self.schedule_next_part()
        log.info('hold changed', {"sessionId": self.db_session_id, "held": held})

    async def handle_message(self, message: Any) -> None:
        # 1. Usage metadata
        usage_metadata = getattr(message, 'usage_metadata', None)
        if usage_metadata:
            tokens = getattr(usage_metadata, 'total_token_count', 0)
            if tokens:
                self.add_tokens(tokens)

        # 2. Tool calls from model
        tool_call = getattr(message, 'tool_call', None)
        if tool_call:
            calls = getattr(tool_call, 'function_calls', []) or []
            if calls:
                self.set_state('processing')
                self.tools_in_flight += len(calls)
                responses = []
                for call in calls:
                    call_name = getattr(call, 'name', '') or 'unknown'
                    call_id = getattr(call, 'id', '')
                    call_args = getattr(call, 'args', {}) or {}
                    self.out.tool(call_name, 'started')
                    started_at = time.time() * 1000
                    try:
                        result = await execute_tool(self.user_id, self.refs, call_name, call_args)
                        self.out.tool(call_name, 'done')
                        log.info('tool done', {"sessionId": self.db_session_id, "name": call_name, "durationMs": int(time.time() * 1000 - started_at)})
                        responses.append(types.FunctionResponse(
                            name=call_name,
                            id=call_id,
                            response={"output": result}
                        ))
                    except Exception as e:
                        err_msg = getattr(e, 'safeMessage', None) or getattr(e, 'message', None) or 'The action failed.'
                        log.error('tool execution failed', {"call": call_name, "err": str(e)})
                        self.out.tool(call_name, 'error')
                        responses.append(types.FunctionResponse(
                            name=call_name,
                            id=call_id,
                            response={"error": err_msg}
                        ))
                    finally:
                        self.tools_in_flight = max(0, self.tools_in_flight - 1)
                
                if responses and self.session and not self.closing:
                    await self.session.send_tool_response(function_responses=responses)
                    if not self.held:
                        self.set_state('listening')

        # 3. Server content
        server_content = getattr(message, 'server_content', None)
        if server_content:
            if getattr(server_content, 'interrupted', False):
                self.listener_spoke()
                self.turn += 1
                self.finalize_open()
                self.out.interrupted(self.turn)
                self.set_state('user_speaking')
                return

            input_transcription = getattr(server_content, 'input_transcription', None)
            if input_transcription and getattr(input_transcription, 'text', None):
                self.last_activity = time.time() * 1000
                self.listener_spoke()
                self.append_transcript('user', input_transcription.text)
                self.move_on_if_asked()
                self.end_if_asked()
                if self.state in ('listening', 'processing'):
                    self.set_state('user_speaking')
                self.schedule_processing()

            output_transcription = getattr(server_content, 'output_transcription', None)
            if output_transcription and getattr(output_transcription, 'text', None):
                self.append_transcript('agent', output_transcription.text)

            model_turn = getattr(server_content, 'model_turn', None)
            if model_turn and getattr(model_turn, 'parts', None):
                for part in model_turn.parts:
                    inline_data = getattr(part, 'inline_data', None)
                    if inline_data and getattr(inline_data, 'data', None):
                        self.last_activity = time.time() * 1000
                        if self.processing_timer:
                            self.processing_timer.cancel()
                            self.processing_timer = None
                        audio_data = inline_data.data
                        if isinstance(audio_data, str):
                            audio_data = base64.b64decode(audio_data)
                        if self.state != 'agent_speaking' and not self.held:
                            self.set_state('agent_speaking')
                        self.out.audio(self.turn, audio_data)
                    if getattr(part, 'text', None):
                        self.append_transcript('agent', part.text)

            if getattr(server_content, 'turn_complete', False):
                self.move_on_handled = False
                if self.end_requested:
                    self.end_soon(END_AFTER_REPLY_MS)
                self.finalize_open()
                self.turn += 1
                if not self.held:
                    self.set_state('listening')
                self.after_turn()

    def add_tokens(self, count: int) -> None:
        self.tokens_used += count
        if self.tokens_used < env.VOICE_TOKENS_PER_SESSION or self.closing:
            return
        log.warn('voice session reached its token limit', {"sessionId": self.db_session_id, "tokens": self.tokens_used})
        self.out.error('TOKEN_LIMIT', 'This voice session reached its length limit. You can start another one if you have sessions left today.')
        asyncio.create_task(self.end('token_limit'))

    def recent_user_speech(self) -> str:
        entries = [e.text for e in self.transcript if e.role == 'user']
        return ' '.join(entries[-3:])

    async def set_up_driver(self, ctx: SessionContext) -> None:
        if not ctx.briefing:
            return
        listener = ctx.name.strip().split()[0] if ctx.name else 'the listener'
        
        try:
            stored = await get_latest_script(self.user_id)
        except Exception as err:
            log.warn('briefing script could not be loaded', {"userId": self.user_id, "err": str(err)})
            stored = None
            
        scripts = {}
        if stored and stored.script.chapters:
            for c in stored.script.chapters:
                scripts[c.developmentId] = c
                
        driver_chapters = []
        for item in ctx.briefing:
            written = scripts.get(item.developmentId)
            driver_chapters.append(DriverChapter(
                title=item.storyTitle,
                script=ChapterScriptText(facts=written.facts, sides=written.sides, forYou=written.forYou) if written else None
            ))
            
        depth = getattr(ctx.profile, 'depth', None) if not isinstance(ctx.profile, dict) else ctx.profile.get('depth')
        self.driver = BriefingDriver(
            chapters=driver_chapters,
            listener=listener,
            seconds_per_part=part_seconds(depth),
            closing=stored.script.closing if stored else None
        )
        
        class BriefingControls:
            def __init__(self, driver: BriefingDriver, cue_fn):
                self.driver = driver
                self.cue_fn = cue_fn
            def stop(self):
                self.driver.stop()
            def skip_story(self):
                if not self.driver.just_skipped():
                    self.driver.skip_story()
            def resume(self):
                self.driver.resume_briefing()
                self.cue_fn()
                
        self.refs.briefing = BriefingControls(self.driver, self.schedule_next_part)

    def listener_spoke(self) -> None:
        if self.driver:
            self.driver.listener_spoke()
        self.clear_continue_timer()
        if self.driver and (self.driver.waiting_for or self.driver.answer_pending):
            self.wait_for_listener(UNANSWERED_WAIT_MS)

    def move_on_if_asked(self) -> None:
        if self.move_on_handled or not self.driver or not self.transcript:
            return
        last = self.transcript[-1]
        if last.role != 'user' or not MOVE_ON.search(last.text):
            return
        self.move_on_handled = True
        self.driver.skip_story()
        log.info('listener asked to move on', {"sessionId": self.db_session_id})

    def end_if_asked(self) -> None:
        if self.end_requested or not self.driver or not self.transcript:
            return
        last = self.transcript[-1]
        if last.role != 'user':
            return
        if not GOODBYE.search(last.text) and not (THANKS.search(last.text) and self.driver.in_closing):
            return
        self.end_requested = True
        self.clear_continue_timer()
        # Normally the end follows the host's reply; this covers a reply that never comes.
        self.end_soon(END_FALLBACK_MS)

    def end_soon(self, ms: int) -> None:
        if self.end_timer:
            self.end_timer.cancel()
        loop = asyncio.get_running_loop()
        self.end_timer = loop.call_later(ms / 1000.0, lambda: asyncio.create_task(self.end('completed')))

    def clear_continue_timer(self) -> None:
        if self.continue_timer:
            self.continue_timer.cancel()
        self.continue_timer = None

    def after_turn(self) -> None:
        if not self.driver:
            return
        asked_just_now = self.driver.running
        outcome = self.driver.turn_completed()
        if outcome == 'end':
            self.clear_continue_timer()
            loop = asyncio.get_running_loop()
            self.end_timer = loop.call_later(FAREWELL_GRACE_MS / 1000.0, lambda: asyncio.create_task(self.end('completed')))
        elif outcome == 'wait':
            self.wait_for_listener(ASK_WAIT_MS if asked_just_now else FOLLOW_UP_WAIT_MS)
        else:
            self.schedule_next_part()

    def wait_for_listener(self, ms: int) -> None:
        if self.closing:
            return
        self.clear_continue_timer()
        def on_timeout():
            self.continue_timer = None
            if self.driver:
                self.driver.wait_elapsed()
            self.cue_next_part()
        loop = asyncio.get_running_loop()
        self.continue_timer = loop.call_later(ms / 1000.0, on_timeout)

    def schedule_next_part(self) -> None:
        if not self.driver or self.closing:
            return
        self.clear_continue_timer()
        loop = asyncio.get_running_loop()
        self.continue_timer = loop.call_later(NEXT_PART_DELAY_MS / 1000.0, self.cue_next_part)

    def cue_next_part(self) -> None:
        self.continue_timer = None
        if not self.driver or self.closing or self.held or not self.session or self.end_requested:
            return
            
        if self.tools_in_flight > 0 or self.state not in ('listening', 'processing'):
            return
            
        note = self.driver.next_note()
        if not note:
            return
            
        log.info('briefing part cued', {"sessionId": self.db_session_id, "cursor": self.driver.cursor})
        asyncio.create_task(self._send_briefing_note(note))

    async def _send_briefing_note(self, note: str) -> None:
        try:
            if self.session and not self.closing:
                await self.session.send_realtime_input(text=note)
        except Exception as exc:
            log.warn('failed to cue briefing part', {"sessionId": self.db_session_id, "err": str(exc)})
            self.handle_close(1011, str(exc))

    def schedule_processing(self) -> None:
        if self.processing_timer:
            self.processing_timer.cancel()
        def on_processing():
            if self.state == 'user_speaking':
                self.set_state('processing')
        loop = asyncio.get_running_loop()
        self.processing_timer = loop.call_later(PROCESSING_DELAY_MS / 1000.0, on_processing)

    def append_transcript(self, role: Role, text: str) -> None:
        last = self.transcript[-1] if self.transcript else None
        if last and last.role == role and self.open_role == role:
            last.text += text
        else:
            self.finalize_open()
            self.transcript.append(TranscriptEntry(role, text))
            self.open_role = role
        
        self.out.transcript(role, self.transcript[-1].text.strip(), self.turn, False)

    def finalize_open(self) -> None:
        if not self.open_role:
            return
        last = self.transcript[-1]
        if last:
            self.out.transcript(last.role, last.text.strip(), self.turn, True)
        self.open_role = None

    def set_state(self, state: LiveState) -> None:
        if self.state == state:
            return
        self.state = state
        self.out.state(state)

    def handle_close(self, code: int, reason: str) -> None:
        if self.closing:
            return
        self.closing = True
        log.warn('live connection closed', {"sessionId": self.db_session_id, "code": code, "reason": reason})
        self.out.error('MODEL_UNAVAILABLE', 'Voice session was interrupted.')
        asyncio.create_task(self.end('connection_lost'))

    def check_limits(self) -> None:
        now = time.time() * 1000
        if now - self.last_activity > IDLE_TIMEOUT_MS:
            asyncio.create_task(self.end('idle_timeout'))
            if self.watchdog:
                self.watchdog.cancel()
        elif now - self.started_at > MAX_SESSION_MS:
            asyncio.create_task(self.end('max_duration'))
            if self.watchdog:
                self.watchdog.cancel()
        else:
            if self.watchdog:
                loop = asyncio.get_running_loop()
                self.watchdog = loop.call_later(15.0, self.check_limits)

    async def cleanup(self) -> None:
        if self.watchdog:
            self.watchdog.cancel()
        self.watchdog = None
        if self.processing_timer:
            self.processing_timer.cancel()
        self.processing_timer = None
        self.clear_continue_timer()
        if self.end_timer:
            self.end_timer.cancel()
        self.end_timer = None
        
        if self._receive_task:
            self._receive_task.cancel()
            self._receive_task = None
            
        if getattr(self, '_live_context', None):
            try:
                await self._live_context.__aexit__(None, None, None)
            except Exception:
                pass
            self._live_context = None
        self.session = None

    async def end(self, reason: str) -> None:
        if self.closing and self.state == 'ended':
            return
        self.closing = True
        self.finalize_open()
        await self.cleanup()
        
        if active.get(self.user_id) == self:
            del active[self.user_id]
            
        self.set_state('ended')
        self.out.ended(reason)
        
        try:
            await add_voice_tokens(self.user_id, self.tokens_used)
        except Exception as err:
            log.warn('voice tokens were not recorded', {"sessionId": self.db_session_id, "err": str(err)})
            
        log.info('session ended', {"userId": self.user_id, "sessionId": self.db_session_id, "reason": reason, "entries": len(self.transcript)})
        await self.persist(reason)

    async def persist(self, reason: str) -> None:
        if not self.db_session_id:
            return
            
        entries = [{"role": t.role, "text": t.text.strip()} for t in self.transcript if len(t.text.strip()) > 0]
        summary = None
        
        try:
            if any(e['role'] == 'user' for e in entries):
                result = await summarize_session(entries)
                stories = list(self.refs.discussed_story_ids)
                sources = []
                if stories:
                    sources_res = supabase.table('story_sources').select('url, title, publisher').in_('story_id', stories).order('published_at', desc=True).limit(6).execute()
                    sources = unwrap('story_sources.session', sources_res)
                    sources = sources if isinstance(sources, list) else [sources]
                    
                summary = {
                    "summary": result.summary,
                    "keyPoints": result.key_points,
                    "nextSteps": result.next_steps,
                    "sources": [{"name": s.get('publisher') or 'Source', "url": s.get('url'), "title": s.get('title')} for s in sources]
                }
        except Exception as err:
            log.error('session summary failed', {"sessionId": self.db_session_id, "err": str(err)})
            
        try:
            res = supabase.table('bidi_sessions').update({
                "status": 'ended',
                "ended_at": (time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())),
                "end_reason": reason,
                "transcript": entries,
                "summary": summary,
                "discussed_story_ids": list(self.refs.discussed_story_ids)
            }).eq('id', self.db_session_id).execute()
            unwrap_void('bidi_sessions.end', res)
        except Exception as err:
            log.error('persisting session failed', {"sessionId": self.db_session_id, "err": str(err)})
