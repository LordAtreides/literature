import os
import json
import logging
import logging.handlers
from pathlib import Path

# Base Paths (Assuming this is run from main.py at the project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
SEEN_URLS_PATH = PROJECT_ROOT / "seen_urls.json"
ERROR_LOG_PATH = PROJECT_ROOT / "error.log"

# Setup Logging
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("GeologyTracker")

_fh = logging.handlers.RotatingFileHandler(
    ERROR_LOG_PATH, maxBytes=1_048_576, backupCount=3, encoding="utf-8"
)
_fh.setLevel(logging.WARNING)
_fh.setFormatter(logging.Formatter(LOG_FORMAT))
logger.addHandler(_fh)

# Environment Variables
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY")

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_seen_urls():
    if not SEEN_URLS_PATH.exists(): return {}
    try:
        with open(SEEN_URLS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Hafiza (seen_urls) okunamadi: %s", e)
        return {}

def save_seen_urls(seen_urls):
    try:
        with open(SEEN_URLS_PATH, "w", encoding="utf-8") as f:
            json.dump(seen_urls, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Hafiza kaydedilemedi: %s", e)
