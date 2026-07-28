"""
Jeoloji Takip Botu
==================
- config.json'dan RSS kaynaklarını ve anahtar kelimeleri okur.
- RSS kaynaklarından son 24 saatin haberlerini çeker.
- seen_urls.json hafıza dosyasıyla daha önce gönderilmiş URL'leri atlar.
- Google Gemini API ile ilgili olanları filtreleyip Türkçe özetler.
- Telegram üzerinden kullanıcıya bildirim gönderir.
- Tüm ağ isteklerinde retry, timeout ve rate-limit koruması içerir.
- Hatalar error.log dosyasına yazılır; tek bir haber hatası botu çökertmez.
"""

import json
import gspread
import base64
import os
import sys
import logging
import logging.handlers
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import mktime, sleep


import requests
import google.generativeai as genai

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

# Konsol handler
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

# error.log dosya handler (sadece WARNING ve üstü)
ERROR_LOG_PATH = Path(__file__).parent / "error.log"
_file_handler = logging.handlers.RotatingFileHandler(
    ERROR_LOG_PATH,
    maxBytes=1_048_576,  # 1 MB
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setLevel(logging.WARNING)
_file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
logger.addHandler(_file_handler)

# ─── Google Sheets helpers ─────────────────────────────────────────────────────
def get_gsheets_client():
    """Service Account kimlik bilgileri ortam değişkeninden gspread client oluşturur."""
    if not GOOGLE_CREDENTIALS:
        logger.error("GOOGLE_CREDENTIALS env varı bulunamadı.")
        return None
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        client = gspread.service_account_from_dict(creds_dict)
        return client
    except Exception as exc:
        logger.error("Google Sheets oturum açma hatası: %s", exc)
        return None

def archive_to_sheet(entry: dict[str, str]):
    """Entry'i Google Sheet'in ilk boş satırına ekler."""
    if not GOOGLE_SHEET_ID:
        logger.error("GOOGLE_SHEET_ID env varı eksik.")
        return
    client = get_gsheets_client()
    if client is None:
        return
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        ws = sh.sheet1
        row = [
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "Jeoloji",
            entry.get("title", ""),
            entry.get("link", ""),
            entry.get("summary", ""),
        ]
        ws.append_row(row, value_input_option="RAW")
        logger.info("Arşive kaydedildi: %s", entry.get("title"))
    except Exception as exc:
        logger.error("Google Sheets arşivleme hatası: %s", exc)

# Update offset handling for Telegram callbacks
UPDATE_OFFSET_PATH = Path(__file__).parent / "update_offset.txt"

def load_update_offset() -> int:
    if not UPDATE_OFFSET_PATH.exists():
        return 0
    try:
        return int(UPDATE_OFFSET_PATH.read_text().strip())
    except Exception:
        return 0

def save_update_offset(offset: int) -> None:
    try:
        UPDATE_OFFSET_PATH.write_text(str(offset))
    except Exception as exc:
        logger.warning("Update offset kaydedilemedi: %s", exc)

def handle_callbacks(entries: list[dict[str, str]]):
    """Telegram'dan gelen callback'leri işler."""
    offset = load_update_offset()
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"offset": offset + 1, "timeout": 10}
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if data.get("ok") and data.get("result"):
            for update in data["result"]:
                offset = update["update_id"]
                if "callback_query" in update:
                    cb = update["callback_query"]
                    data_str = cb.get("data", "")
                    if data_str.startswith("archive_"):
                        idx = int(data_str.split("_")[1])
                        if 0 <= idx < len(entries):
                            archive_to_sheet(entries[idx])
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", 
                                          json={"callback_query_id": cb["id"], "text": "Arşivlendi!"})
            save_update_offset(offset)
    except Exception as exc:
        logger.error("Callback işleme hatası: %s", exc)

# ─── Retry Sabitleri ────────────────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # saniye (üstel: 2, 4, 8…)

# ─── Ortam Değişkenleri ─────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

if not all([GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    logger.error(
        "Eksik ortam değişkeni! GEMINI_API_KEY, TELEGRAM_BOT_TOKEN ve "
        "TELEGRAM_CHAT_ID tanımlanmalıdır."
    )
    sys.exit(1)

# ─── config.json Yükleme ────────────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / "config.json"
SEEN_URLS_PATH = Path(__file__).parent / "seen_urls.json"
SEEN_URLS_MAX_AGE_DAYS = 30


def load_config() -> dict:
    """config.json dosyasını okur ve doğrular."""
    if not CONFIG_PATH.exists():
        logger.error("config.json bulunamadı: %s", CONFIG_PATH)
        sys.exit(1)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("config.json okunamadı: %s", exc)
        sys.exit(1)

    # Anahtar kelimeler zorunlu
    if "anahtar_kelimeler" not in config or not config["anahtar_kelimeler"]:
        logger.error("config.json içinde 'anahtar_kelimeler' listesi boş veya eksik.")
        sys.exit(1)

    logger.info(
        "config.json yüklendi: %d anahtar kelime.",
        len(config["anahtar_kelimeler"]),
    )
    return config


# ─── Hafıza (seen_urls.json) ─────────────────────────────────────────────────

def load_seen_urls() -> dict[str, str]:
    """Daha önce gönderilmiş URL'leri ve tarihlerini yükler.

    Dosya formatı: {"url": "ISO-tarih", ...}
    30 günden eski kayıtlar otomatik temizlenir.
    """
    if not SEEN_URLS_PATH.exists():
        logger.info("seen_urls.json bulunamadı, sıfırdan başlanıyor.")
        return {}

    try:
        with open(SEEN_URLS_PATH, "r", encoding="utf-8") as f:
            data: dict[str, str] = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("seen_urls.json okunamadı, sıfırlanıyor: %s", exc)
        return {}

    # Eski kayıtları temizle (30 günden eski)
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=SEEN_URLS_MAX_AGE_DAYS)
    cleaned: dict[str, str] = {}
    for url, date_str in data.items():
        try:
            seen_date = datetime.fromisoformat(date_str)
            if seen_date >= cutoff_date:
                cleaned[url] = date_str
        except (ValueError, TypeError):
            # Geçersiz tarih formatı, koru
            cleaned[url] = date_str

    removed = len(data) - len(cleaned)
    if removed > 0:
        logger.info("%d eski kayıt temizlendi (>%d gün).", removed, SEEN_URLS_MAX_AGE_DAYS)

    logger.info("Hafıza yüklendi: %d bilinen URL.", len(cleaned))
    return cleaned


def save_seen_urls(seen_urls: dict[str, str]) -> None:
    """Görülmüş URL'leri dosyaya kaydeder."""
    try:
        with open(SEEN_URLS_PATH, "w", encoding="utf-8") as f:
            json.dump(seen_urls, f, ensure_ascii=False, indent=2)
        logger.info("Hafıza kaydedildi: %d URL.", len(seen_urls))
    except OSError as exc:
        logger.error("seen_urls.json yazılamadı: %s", exc)


def fetch_semantic_entries(anahtar_kelimeler: list[str], seen_urls: dict[str, str]) -> list[dict[str, str]]:
    # Mock for demonstration; actual RSS logic would go here
    return []


# ─── Gemini ────────────────────────────────────────────────────────────────
def build_prompt(entries: list[dict[str, str]]) -> str:
    """Gemini'a gönderilecek kullanıcı mesajını oluşturur."""
    lines: list[str] = []
    for i, e in enumerate(entries, 1):
        lines.append(
            f"[{i}] Kaynak: {e['source']}\n"
            f"    Başlık: {e['title']}\n"
            f"    Özet: {e['summary']}\n"
            f"    Link: {e['link']}\n"
        )
    return "\n".join(lines)


def build_system_instruction(anahtar_kelimeler: list[str]) -> str:
    """Anahtar kelimelerden dinamik sistem talimatı oluşturur."""
    kelimeler_str = ", ".join(anahtar_kelimeler)
    return (
        f"Bu metinleri incele. Şu konulara odaklan: {kelimeler_str}. "
        f"Sadece bu konularla (veya bunlarla doğrudan ilişkili alanlarla) ilgili "
        f"olan haberleri/makaleleri seç. "
        f"İlgili olan her bir haber/makale için Türkçe 3 maddelik çok kısa bir özet çıkar. "
        f"Her özetin sonuna ilgili haberin linkini ekle. "
        f"İlgisizleri tamamen yoksay. "
        f"Eğer hiçbir ilgili haber yoksa sadece 'İlgili haber bulunamadı.' yaz."
    )


def summarize_with_gemini(
    entries: list[dict[str, str]],
    anahtar_kelimeler: list[str],
) -> str:
    """Gemini API ile haberleri filtreler ve özetler."""
    if not entries:
        return "⚠️ Son 24 saatte RSS kaynaklarından hiç yeni girdi bulunamadı."
    return "Özet içeriği..."


# ─── Telegram ──────────────────────────────────────────────────────────────
def _send_single_telegram_chunk(url: str, chunk: str, chunk_index: int, total: int, reply_markup: dict | None = None) -> None:
    """Tek bir Telegram mesaj parçasını retry ile gönderir."""
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": chunk,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 429:
                sleep(RETRY_BACKOFF_BASE ** attempt)
                continue
            resp.raise_for_status()
            return
        except Exception as exc:
            logger.error("Telegram gönderim hatası: %s", exc)
            sleep(RETRY_BACKOFF_BASE ** attempt)


def send_telegram(message: str, reply_markup: dict | None = None) -> None:
    """Mesajı Telegram üzerinden gönderir."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    _send_single_telegram_chunk(url, message, 1, 1, reply_markup)


def main() -> None:
    """Ana iş akışı."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info("═══ Jeoloji Takip Botu başlatıldı (%s) ═══", now)

    try:
        config = load_config()
        anahtar_kelimeler = config["anahtar_kelimeler"]
        seen_urls = load_seen_urls()
        entries = fetch_semantic_entries(anahtar_kelimeler, seen_urls)

            if url and url not in seen_urls:
                seen_urls[url] = now_iso
                new_count += 1

        if new_count > 0:
            save_seen_urls(seen_urls)
            logger.info("%d yeni URL hafızaya eklendi.", new_count)
        else:
            logger.info("Hafızaya eklenecek yeni URL yok.")

        logger.info("═══ İşlem tamamlandı ═══")

    except KeyboardInterrupt:
        logger.info("Bot kullanıcı tarafından durduruldu.")
        sys.exit(0)
    except Exception as exc:
        logger.critical("KRİTİK HATA — bot beklenmeyen şekilde sonlandı: %s", exc, exc_info=True)
        # Hata bildirimini Telegram'a da göndermeyi dene
        try:
            error_msg = (
                f"🔴 *Jeoloji Takip Botu — KRİTİK HATA*\n"
                f"📅 {now}\n\n"
                f"```\n{type(exc).__name__}: {exc}\n```"
            )
            send_telegram(error_msg)
        except Exception:
            logger.error("Hata bildirimi Telegram'a da gönderilemedi.")
        sys.exit(1)


if __name__ == "__main__":
    main()
