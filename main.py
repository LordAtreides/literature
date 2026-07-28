"""
Jeoloji Takip Botu
==================
- config.json'dan kaynakları ve anahtar kelimeleri okur.
- Crossref Akademik API üzerinden makaleleri arar (GitHub IP engellerine takılmaz).
- seen_urls.json hafıza dosyasıyla daha önce gönderilmiş URL'leri atlar.
- Gemini REST API ile makale özetlerini Türkçe 3 maddeye çevirir (Kütüphanesiz, %100 uyumlu).
- Telegram üzerinden kullanıcıya bildirim gönderir.
- Google Sheets arşivleme butonunu destekler.
"""

import json
import gspread
import os
import sys
import logging
import logging.handlers
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep
import requests

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

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

# ─── Ortam Değişkenleri ─────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

# ─── Google Sheets helpers ─────────────────────────────────────────────────────
def get_gsheets_client():
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
                                          json={"callback_query_id": cb["id"], "text": "Google Tablolara Arşivlendi! ✅"})
            save_update_offset(offset)
    except Exception as exc:
        logger.error("Callback işleme hatası: %s", exc)

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2
CONFIG_PATH = Path(__file__).parent / "config.json"
SEEN_URLS_PATH = Path(__file__).parent / "seen_urls.json"

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        logger.error("config.json bulunamadı: %s", CONFIG_PATH)
        sys.exit(1)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("config.json okunamadı: %s", exc)
        sys.exit(1)

def load_seen_urls() -> dict[str, str]:
    if not SEEN_URLS_PATH.exists():
        return {}
    try:
        with open(SEEN_URLS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_seen_urls(seen_urls: dict[str, str]) -> None:
    try:
        with open(SEEN_URLS_PATH, "w", encoding="utf-8") as f:
            json.dump(seen_urls, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.error("seen_urls.json yazılamadı: %s", exc)

def fetch_semantic_entries(anahtar_kelimeler: list[str], seen_urls: dict[str, str]) -> list[dict[str, str]]:
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY yok, özetleme atlanıyor.")
        return []

    entries = []
    for kelime in anahtar_kelimeler:
        url = "https://api.crossref.org/works"
        params = {
            "query": kelime,
            "select": "title,URL,abstract",
            "sort": "published",
            "order": "desc",
            "rows": 2 
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                continue
                
            items = resp.json().get("message", {}).get("items", [])
            for paper in items:
                link = paper.get("URL")
                if not link or link in seen_urls:
                    continue
                    
                title_list = paper.get("title", [])
                title = title_list[0] if title_list else "Başlıksız"
                abstract = paper.get("abstract", "Özet metni sunucu tarafından sağlanmadı.")
                
                # Gemini Doğrudan (Kütüphanesiz) REST API İstediği
                prompt = (
                    f"Sen uzman bir jeologsun. Aşağıdaki makale bilgilerini incele ve "
                    f"anlaşılır bir dille Türkçe 3 maddelik kısa bir özet çıkar.\n\n"
                    f"Başlık: {title}\nÖzet: {abstract}"
                )
                
                gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
                gemini_payload = {"contents": [{"parts": [{"text": prompt}]}]}
                
                try:
                    resp_gemini = requests.post(gemini_url, json=gemini_payload, timeout=20)
                    if resp_gemini.status_code == 200:
                        data_gemini = resp_gemini.json()
                        # Eğer bloklanırsa veya boş dönerse kontrol ediyoruz
                        if "candidates" in data_gemini and len(data_gemini["candidates"]) > 0:
                            summary = data_gemini["candidates"][0]["content"]["parts"][0]["text"].strip()
                            entries.append({"title": title, "link": link, "summary": summary})
                    else:
                        logger.error("Gemini API REST Hatası: %s %s", resp_gemini.status_code, resp_gemini.text)
                except Exception as e:
                    logger.error("Gemini İstek Hatası: %s", e)
                    
        except Exception as e:
            logger.error("Makale API Hatası (%s): %s", kelime, e)
            
    return entries

def _send_single_telegram_chunk(url: str, chunk: str, chunk_index: int, total: int, reply_markup: dict | None = None) -> None:
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
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    _send_single_telegram_chunk(url, message, 1, 1, reply_markup)

def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info("═══ Jeoloji Takip Botu başlatıldı (%s) ═══", now)

    try:
        config = load_config()
        anahtar_kelimeler = config["anahtar_kelimeler"]
        seen_urls = load_seen_urls()

        entries = fetch_semantic_entries(anahtar_kelimeler, seen_urls)

        if entries:
            header = (
                f"🌍 *Jeoloji Takip Botu*\n"
                f"📅 {now}\n"
                f"🔑 Filtre: {', '.join(anahtar_kelimeler[:5])}{'…' if len(anahtar_kelimeler) > 5 else ''}\n"
                f"📊 {len(entries)} yeni içerik\n"
                f"{'─' * 30}\n\n"
            )
            send_telegram(header)

            for idx, entry in enumerate(entries):
                title = entry.get("title", "Başlıksız").replace("*", "\\*")
                summary = entry.get("summary", "")
                
                lines = [ln.strip() for ln in summary.splitlines() if ln.strip()][:3]
                bullets = "\n".join(f"{ln}" if ln.startswith("-") or ln.startswith("*") else f"- {ln}" for ln in lines)
                
                msg = f"**{title}**\n{bullets}\n\n[Detaylı Oku]({entry.get('link')})"
                keyboard = {"inline_keyboard": [[{"text": "Arşive Kaydet 📁", "callback_data": f"archive_{idx}"}]]}
                send_telegram(msg, reply_markup=keyboard)

            handle_callbacks(entries)
        else:
            logger.info("Yeni makale/haber bulunamadı. Telegram'a mesaj gönderilmeyecek.")

        now_iso = datetime.now(timezone.utc).isoformat()
        new_count = 0
        for entry in entries:
            url = entry.get("link", "")
            if url and url not in seen_urls:
                seen_urls[url] = now_iso
                new_count += 1
                
        if new_count > 0:
            save_seen_urls(seen_urls)
            logger.info("%d yeni URL hafızaya eklendi.", new_count)
        else:
            logger.info("Hafızaya eklenecek yeni URL yok.")

        logger.info("═══ İşlem tamamlandı ═══")

    except Exception as exc:
        logger.critical("KRİTİK HATA: %s", exc, exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
