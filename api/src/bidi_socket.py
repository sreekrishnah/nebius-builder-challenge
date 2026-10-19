import asyncio
import base64
from typing import Dict, Any, Callable, Optional
import socketio

from src.config.env import env
from src.lib.errors import AppError
from src.lib.logger import create_logger
from src.lib.tokens import verify_token
from src.ai.liveSpeech import VOICE_LANGUAGES
from src.services.bidiAgent import LiveSession, LiveEmitter, INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE
from src.services.voiceUsage import consume_voice_session, limit_message, refund_voice_session

log = create_logger('live-socket')

class SocketData:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.session: Optional[LiveSession] = None
        self.starting: bool = False

def emitter_for(sio: socketio.AsyncServer, sid: str) -> LiveEmitter:
    class Emitter(LiveEmitter):
        def state(self, state: str) -> None:
            asyncio.create_task(sio.emit('live:state', {"state": state}, to=sid, namespace='/live'))
        def audio(self, turn: int, data: bytes) -> None:
            asyncio.create_task(sio.emit('live:audio', {"turn": turn, "data": data}, to=sid, namespace='/live'))
        def interrupted(self, turn: int) -> None:
            asyncio.create_task(sio.emit('live:interrupted', {"turn": turn}, to=sid, namespace='/live'))
        def transcript(self, role: str, text: str, turn: int, final: bool) -> None:
            asyncio.create_task(sio.emit('live:transcript', {"role": role, "text": text, "turn": turn, "final": final}, to=sid, namespace='/live'))
        def tool(self, name: str, status: str) -> None:
            asyncio.create_task(sio.emit('live:tool', {"name": name, "status": status}, to=sid, namespace='/live'))
        def error(self, code: str, message: str) -> None:
            asyncio.create_task(sio.emit('live:error', {"code": code, "message": message}, to=sid, namespace='/live'))
        def ended(self, reason: str) -> None:
            asyncio.create_task(sio.emit('live:ended', {"reason": reason}, to=sid, namespace='/live'))
    return Emitter()

def register_live_socket(sio: socketio.AsyncServer):
    # This namespace handles /live
    class LiveNamespace(socketio.AsyncNamespace):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.socket_data = {}

        async def trigger_event(self, event, *args):
            handler_name = 'on_' + (event or '').replace(':', '_')
            if hasattr(self, handler_name):
                handler = getattr(self, handler_name)
                import inspect
                if inspect.iscoroutinefunction(handler):
                    return await handler(*args)
                return handler(*args)
            return await super().trigger_event(event, *args)

        async def on_connect(self, sid, environ, auth):
            token = auth.get('token') if auth else None
            payload = verify_token(token, 'ws', env.AUTH_SECRET) if token else None
            if not payload:
                log.warn('socket rejected', {"socketId": sid})
                raise socketio.exceptions.ConnectionRefusedError('UNAUTHENTICATED')
                
            self.socket_data[sid] = SocketData(user_id=payload['sub'])
            log.info('socket connected', {"socketId": sid, "userId": payload['sub']})

        async def on_disconnect(self, sid, *args):
            data = self.socket_data.get(sid)
            if data:
                log.info('socket disconnected', {"socketId": sid, "userId": data.user_id, "hadSession": bool(data.session)})
                if data.session:
                    await data.session.end('client_disconnected')
                del self.socket_data[sid]

        async def on_live_start(self, sid, payload=None):
            data = self.socket_data.get(sid)
            if not data: return
            
            payload = payload or {}
            lang = payload.get('language')
            if lang and lang not in VOICE_LANGUAGES:
                return {"ok": False, "code": 'VALIDATION_ERROR', "message": 'That language is not supported.'}
                
            if data.session or data.starting:
                return {"ok": False, "code": 'ALREADY_STARTED', "message": 'A voice session is already active.'}
                
            data.starting = True
            counted = False
            
            try:
                gate = await consume_voice_session(data.user_id)
                if gate != 'ok':
                    log.info('voice session refused by daily limit', {"userId": data.user_id, "gate": gate})
                    data.starting = False
                    return {"ok": False, "code": 'DAILY_LIMIT', "message": limit_message(gate)}
                    
                counted = True
                session = LiveSession(data.user_id, emitter_for(sio, sid))
                session_id = await session.start(lang)
                data.session = session
                
                return {"ok": True, "sessionId": session_id, "inputSampleRate": INPUT_SAMPLE_RATE, "outputSampleRate": OUTPUT_SAMPLE_RATE}
            except Exception as err:
                log.error('voice session start failed', {"userId": data.user_id, "err": str(err)})
                if counted:
                    try:
                        await refund_voice_session(data.user_id)
                    except Exception as refundErr:
                        log.warn('voice session refund failed', {"refundErr": str(refundErr)})
                        
                code = getattr(err, 'code', 'INTERNAL') if isinstance(err, AppError) else 'INTERNAL'
                message = getattr(err, 'message', 'Voice session could not be started.')
                return {"ok": False, "code": code, "message": message}
            finally:
                data.starting = False

        async def on_live_audio(self, sid, frame=None):
            data = self.socket_data.get(sid)
            if not data or not data.session or frame is None: return
            
            if isinstance(frame, bytes):
                await data.session.send_audio(frame)
            elif isinstance(frame, bytearray):
                await data.session.send_audio(bytes(frame))
                
        async def on_live_mic(self, sid, info=None):
            data = self.socket_data.get(sid)
            if not data: return
            log.info('mic device', {"socketId": sid, "userId": data.user_id, **(info or {})})

        async def on_live_mute(self, sid, payload=None):
            data = self.socket_data.get(sid)
            if data and data.session and payload and isinstance(payload.get('muted'), bool):
                data.session.set_muted(payload['muted'])

        async def on_live_hold(self, sid, payload=None):
            data = self.socket_data.get(sid)
            if data and data.session and payload and isinstance(payload.get('held'), bool):
                data.session.set_held(payload['held'])

        async def on_live_end(self, sid, *args):
            data = self.socket_data.get(sid)
            if data and data.session:
                await data.session.end('user_ended')
                data.session = None
            return {"ok": True}

    sio.register_namespace(LiveNamespace('/live'))
