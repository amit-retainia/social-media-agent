"""Settings, read from environment variables or a .env file.

Every outside service switches ON when its key is in .env.
When a key is missing, that service uses a simulated version.
Run `python main.py --check` to see which services are real.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    # Ignore the sample values from .env.example.
    return "" if value.endswith("-here") else value


def _flag(name: str, default: bool) -> bool:
    value = _env(name, "")
    return default if value == "" else value.lower() in {"1", "true", "yes", "on"}


# ---- Claude (text for all agents, and trends with web search) ----
API_KEY = _env("ANTHROPIC_API_KEY")
MODEL = _env("CLAUDE_MODEL", "claude-sonnet-5-5")
MAX_TOKENS = int(_env("CLAUDE_MAX_TOKENS", "4096"))
TRENDS_WEB_SEARCH = _flag("TRENDS_WEB_SEARCH", True)

# Mock mode: no Claude calls. Agents use simple fixed logic.
# It is on when there is no API key, or when you run with --mock.
MOCK = (not API_KEY) or _env("MOCK") == "1"

# ---- MachGen: images and video clips from open models (the Creator's default) ----
MACHGEN_API_KEY = _env("MACHGEN_API_KEY")
MACHGEN_API_URL = _env("MACHGEN_API_URL", "https://api.machgen.ai")
MACHGEN_IMAGE_MODEL = _env("MACHGEN_IMAGE_MODEL", "FLUX.2-dev")
MACHGEN_VIDEO_MODEL = _env("MACHGEN_VIDEO_MODEL", "Wan2.2-A14B")
MACHGEN_VIDEO_HEIGHT = int(_env("MACHGEN_VIDEO_HEIGHT", "720"))
# Which service makes images / video clips. auto = MachGen if its key is set, else the other one.
IMAGE_PROVIDER = _env("IMAGE_PROVIDER", "auto").lower()   # auto, machgen, openai
VIDEO_PROVIDER = _env("VIDEO_PROVIDER", "auto").lower()   # auto, machgen, runway
# How each clip is made: "text" (text to video) or "image" (make an AI image of the scene first,
# then animate it: same look as your photo posts).
VIDEO_MODE = _env("VIDEO_MODE", _env("RUNWAY_MODE", "text")).lower()

# ---- Images (OpenAI GPT Image). gpt-image-1 was shut down on 2026-10-23, so the default is gpt-image-2. ----
OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_IMAGE_MODEL = _env("OPENAI_IMAGE_MODEL", "gpt-image-2")
OPENAI_IMAGE_QUALITY = _env("OPENAI_IMAGE_QUALITY", "medium")

# ---- Video clips, other option (Runway). Used when VIDEO_PROVIDER=runway, or when there is no MachGen key. ----
RUNWAYML_API_SECRET = _env("RUNWAYML_API_SECRET")
RUNWAY_VIDEO_MODEL = _env("RUNWAY_VIDEO_MODEL", "gen4.5")
AI_VIDEO_ENABLED = _flag("AI_VIDEO_ENABLED", True)  # Set 0 to save money: no AI video clips.
# No AI video clips (no key, or AI_VIDEO_ENABLED=0): 1 = make a slideshow video from AI images
# (one image per scene with a slow zoom). Cheap, real and postable. 0 = simulated clips.
SLIDESHOW_FALLBACK = _flag("SLIDESHOW_FALLBACK", True)
# Burn the captions into the video (an .srt file is always saved too).
BURN_IN_CAPTIONS = _flag("BURN_IN_CAPTIONS", True)
# Optional: a .ttf font file for the captions (your brand font). Empty = a bold system font.
CAPTION_FONT = _env("CAPTION_FONT")
VIDEO_TIMEOUT_SECONDS = int(_env("VIDEO_TIMEOUT_SECONDS", "900"))

# ---- Voiceover (ElevenLabs) ----
ELEVENLABS_API_KEY = _env("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = _env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL = _env("ELEVENLABS_MODEL", "eleven_multilingual_v2")

# ---- Social platforms (Ayrshare: Instagram, Facebook, X, LinkedIn, TikTok, YouTube) ----
AYRSHARE_API_KEY = _env("AYRSHARE_API_KEY")
AYRSHARE_PROFILE_KEY = _env("AYRSHARE_PROFILE_KEY")  # Optional: one Ayrshare profile per business.

# ---- Media storage (optional; Amazon S3). Without it, media goes to Ayrshare's media storage. ----
AWS_ACCESS_KEY_ID = _env("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = _env("AWS_SECRET_ACCESS_KEY")
AWS_REGION = _env("AWS_REGION", "us-east-1")
S3_BUCKET = _env("S3_BUCKET")
S3_PUBLIC_BASE_URL = _env("S3_PUBLIC_BASE_URL")  # Optional, e.g. a CloudFront URL.

# ---- Alerts to the owner (optional): chat webhook and/or email ----
ALERT_WEBHOOK_URL = _env("ALERT_WEBHOOK_URL")  # Slack, Discord or Teams incoming webhook.
SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = int(_env("SMTP_PORT", "587"))
SMTP_USER = _env("SMTP_USER")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
ALERT_EMAIL_FROM = _env("ALERT_EMAIL_FROM")
ALERT_EMAIL_TO = _env("ALERT_EMAIL_TO")

# ---- Owner app on the phone (Telegram bot): preview, approve, ask for changes, alerts ----
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")  # The owner's chat. Per business: TELEGRAM_CHAT_ID_<BUSINESS>.
TELEGRAM_API_URL = _env("TELEGRAM_API_URL", "https://api.telegram.org")
# In the chat, how long "owner review" waits for your answers on the phone. The rest wait for `--bot`.
APPROVAL_WAIT_SECONDS = int(_env("APPROVAL_WAIT_SECONDS", "300"))
# Slow approvals (plan "Risks"): remind the owner this many hours before the post time.
# If the time passes with no approval, the post moves to the next free slot.
APPROVAL_REMINDER_HOURS = float(_env("APPROVAL_REMINDER_HOURS", "2"))
# `python main.py --bot`: minutes between Publisher runs (post what is due, reminders).
BOT_PUBLISH_EVERY_MINUTES = float(_env("BOT_PUBLISH_EVERY_MINUTES", "5"))

# ---- Planner ----
TIMEZONE = _env("TIMEZONE", "UTC")  # Default; the brand brief can set its own.
PLANNER_MIN_LEAD_HOURS = float(_env("PLANNER_MIN_LEAD_HOURS", "3"))  # Time for Creator + approval.

DATA_DIR = PROJECT_ROOT / "data" / "businesses"

PUBLISH_RETRIES = 3
PUBLISH_RETRY_WAIT = float(_env("PUBLISH_RETRY_WAIT", "2"))
SIMULATE_FAIL_RATE = float(_env("SIMULATE_FAIL_RATE", "0"))

# Analyst pulls metrics at these times after each post (hours).
METRIC_CHECKPOINTS = {"1h": 1, "24h": 24, "7d": 24 * 7}


def ayrshare_profile_key(tenant_id: str | None) -> str:
    """Per-business profile key: AYRSHARE_PROFILE_KEY_<BUSINESS> (e.g. _MY_BAKERY), else the default."""
    if tenant_id:
        own = _env("AYRSHARE_PROFILE_KEY_" + tenant_id.upper().replace("-", "_"))
        if own:
            return own
    return AYRSHARE_PROFILE_KEY


def telegram_chat_id(tenant_id: str | None) -> str:
    """Per-business owner chat: TELEGRAM_CHAT_ID_<BUSINESS> (e.g. _SWEET_CRUMBS), else TELEGRAM_CHAT_ID."""
    if tenant_id:
        own = _env("TELEGRAM_CHAT_ID_" + tenant_id.upper().replace("-", "_"))
        if own:
            return own
    return TELEGRAM_CHAT_ID


def _image_provider() -> str:
    """'MachGen', 'OpenAI' or '' (simulated)."""
    if IMAGE_PROVIDER in ("auto", "machgen") and MACHGEN_API_KEY:
        return "MachGen"
    if IMAGE_PROVIDER in ("auto", "openai") and OPENAI_API_KEY:
        return "OpenAI"
    return ""


def _video_provider() -> str:
    """'MachGen', 'Runway' or '' (no AI video clips)."""
    if not AI_VIDEO_ENABLED:
        return ""
    if VIDEO_PROVIDER in ("auto", "machgen") and MACHGEN_API_KEY:
        return "MachGen"
    if VIDEO_PROVIDER in ("auto", "runway") and RUNWAYML_API_SECRET:
        return "Runway"
    return ""


def services() -> list[tuple[str, bool, str]]:
    """(service, is_real, what to set) for every outside service."""
    s3 = all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET])
    return [
        ("Claude (all agents' text)", not MOCK, "ANTHROPIC_API_KEY"),
        ("Trends and web search (Planner)", not MOCK and TRENDS_WEB_SEARCH, "ANTHROPIC_API_KEY"),
        ("AI images (Creator)" + (f": {_image_provider()}" if _image_provider() else ""),
         bool(_image_provider()), "MACHGEN_API_KEY (or OPENAI_API_KEY)"),
        ("AI video clips (Creator)" + (f": {_video_provider()}" if _video_provider() else ""),
         bool(_video_provider()), "MACHGEN_API_KEY (or RUNWAYML_API_SECRET)"),
        ("Slideshow video from AI images (Creator, used when AI video is off)",
         bool(_image_provider()) and SLIDESHOW_FALLBACK, "MACHGEN_API_KEY"),
        ("Voiceover (Creator)", bool(ELEVENLABS_API_KEY), "ELEVENLABS_API_KEY"),
        ("Video editing, captions, sizes per platform (Creator)", True, "built in (ffmpeg + Pillow)"),
        ("Social platforms: connect, post, replies, metrics", bool(AYRSHARE_API_KEY), "AYRSHARE_API_KEY"),
        ("Media storage for posting", s3 or bool(AYRSHARE_API_KEY), "S3 keys, or AYRSHARE_API_KEY"),
        ("Owner app on the phone (Telegram): preview, approve, ask for changes, alerts",
         bool(TELEGRAM_BOT_TOKEN and (TELEGRAM_CHAT_ID or any(
             k.startswith("TELEGRAM_CHAT_ID_") and v.strip() for k, v in os.environ.items()))),
         "TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (run --telegram-setup); else the terminal"),
        ("Owner alerts by webhook", bool(ALERT_WEBHOOK_URL), "ALERT_WEBHOOK_URL"),
        ("Owner alerts by email", bool(SMTP_HOST and ALERT_EMAIL_TO), "SMTP_HOST + ALERT_EMAIL_TO"),
        ("Website reader (Interviewer)", True, "no key needed"),
    ]
