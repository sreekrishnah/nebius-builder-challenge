import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import socketio

for noisy in [
    'websockets',
    'websockets.protocol',
    'websockets.client',
    'websockets.server',
    'engineio',
    'engineio.server',
    'engineio.socket',
    'socketio',
    'socketio.server',
    'google_genai',
]:
    logging.getLogger(noisy).setLevel(logging.WARNING)

from src.config.env import env
from src.http.middleware import ErrorHandlerMiddleware
from src.lib.logger import create_logger
from src.routes.index import router as api_router
from src.services.bidiAgent import end_all_sessions
from src.lib.gemini import check_configured_models
from src.services.batch import run_daily_batch
from src.services.schedule import is_catch_up_window, is_scheduled_minute
from src.services.pipeline import fail_orphaned_jobs
from src.bidi_socket import register_live_socket

log = create_logger('server')

batch_task: asyncio.Task = None

async def run_batch_timer():
    while True:
        if is_scheduled_minute():
            log.info('starting scheduled batch')
            asyncio.create_task(run_daily_batch())
        await asyncio.sleep(20)

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info('api starting', {"port": env.PORT, "frontend": env.FRONTEND_URL, "liveModel": env.GEMINI_LIVE_MODEL})
    
    try:
        await fail_orphaned_jobs()
    except Exception as err:
        log.error('orphan cleanup failed', {"err": str(err)})
        
    if is_catch_up_window():
        asyncio.create_task(run_daily_batch())
        
    await check_configured_models()
    
    global batch_task
    batch_task = asyncio.create_task(run_batch_timer())
    
    yield
    
    log.info('shutting down')
    if batch_task:
        batch_task.cancel()
    await end_all_sessions('server_shutdown')

app = FastAPI(lifespan=lifespan)

allowed_origins = [env.FRONTEND_URL]
if env.NODE_ENV != 'production':
    allowed_origins.extend(['http://localhost:3000', 'http://127.0.0.1:3000'])

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(ErrorHandlerMiddleware)

app.include_router(api_router, prefix='/api')

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins=allowed_origins, max_http_buffer_size=256*1024, ping_interval=25, ping_timeout=60, logger=False, engineio_logger=False)
register_live_socket(sio)

socket_app = socketio.ASGIApp(sio, other_asgi_app=app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(socket_app, host="0.0.0.0", port=int(env.PORT))
