import os
import sys

# Ensure api directory is on sys.path
api_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if api_dir not in sys.path:
    sys.path.insert(0, api_dir)

# Load local .env if present
env_path = os.path.join(api_dir, ".env")
if os.path.exists(env_path):
    from dotenv import load_dotenv
    load_dotenv(env_path)

# Ensure required fallback env vars exist if not set in .env
os.environ.setdefault("NODE_ENV", "test")
os.environ.setdefault("PORT", "5000")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("GEMINI_API_KEY", "mock-gemini-key")
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "mock-service-role-key-at-least-thirty-two-chars-long")
os.environ.setdefault("AUTH_SECRET", "super-secret-auth-key-must-be-at-least-32-chars-long")
