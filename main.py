"""
Jeoloji Takip Botu (Yıkılmaz Sürüm - 2026 Modelleri)
===================================================
- config.json'dan anahtar kelimeleri okur.
- Crossref Akademik API üzerinden makaleleri arar (GitHub IP engeli yok, limitsiz).
- En güncel makaleleri çeker (sort=published).
- Gemini 3.6 Flash ve yeni nesil modelleri dener. Modellerin hiçbiri çalışmazsa orijinal özeti atar.
- Telegram üzerinden bildirim gönderir ve Google Sheets arşivlemeyi destekler.
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
    ERROR_LOG_PATH, maxBytes=1_048_576, backupCount=3, encoding="utf-8"
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
        return None
    try:
        return gspread.service_account_from_dict(json.loads(GOOGLE_CREDENTIALS))
    except Exception as exc:
        logger.error("Google Sheets oturum açma hatası: %s", exc)
        return None

def archive_to_sheet(entry: dict[str, str]):
    if not GOOGLE_SHEET_ID:
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
    except Exception as exc:
        logger.error("Google Sheets arşivleme hatası: %s", exc)

UPDATE_OFFSET_PATH = Path(__file__).parent / "update_offset.txt"

def load_update_offset() -> int:
    if not UPDATE_OFFSET_PATH.exists(): return 0
    try: return int(UPDATE_OFFSET_PATH.read_text().strip())
    except: return 0

def save_update_offset(offset: int) -> None:
    try: UPDATE_OFFSET_PATH.write_text(str(offset))
    except: pass

def handle_callbacks(entries: list[dict[str, str]]):
    offset = load_update_offset()
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        resp = requests.get(url, params={"offset": offset + 1, "timeout": 10}, timeout=15)
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
                                          json={"callback_query_id": cb["id"], "text": "Arşivlendi! ✅"})
            save_update_offset(offset)
    except Exception:
        pass

CONFIG_PATH = Path(__file__).parent / "config.json"
SEEN_URLS_PATH = Path(__file__).parent / "seen_urls.json"

def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_seen_urls() -> dict[str, str]:
    if not SEEN_URLS_PATH.exists(): return {}
    with open(SEEN_URLS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_seen_urls(seen_urls: dict[str, str]) -> None:
    with open(SEEN_URLS_PATH, "w", encoding="utf-8") as f:
        json.dump(seen_urls, f, ensure_ascii=False, indent=2)

def summarize_text(title: str, abstract: str) -> str:
    """Gemini API ile özetler. Modeller çalışmazsa orijinal metni döndürür."""
    fallback_text = abstract[:500] + "..." if len(abstract) > 500 else abstract
    if not GEMINI_API_KEY:
        return fallback_text
        
    prompt = f"Sen uzman bir jeologsun. Aşağıdaki akademik makaleyi incele ve Türkçe 3 maddelik kısa bir özet çıkar.\n\nBaşlık: {title}\nÖzet: {abstract}"
    
    # 2026 Yeni Nesil Gemini Modelleri
    models = ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-2.5-pro"]
    
    for model in models:
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            resp = requests.post(gemini_url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if "candidates" in data and data["candidates"]:
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            elif resp.status_code == 404:
                continue # Model bulunamadıysa sonrakini dene
        except Exception:
            pass # Hata olursa sonrakini dene
            
    # Hiçbir model çalışmazsa (veya şifre tamamen yanlışsa) orjinal özeti at!
    return fallback_text

def fetch_semantic_entries(anahtar_kelimeler: list[str], seen_urls: dict[str, str]) -> list[dict[str, str]]:
    entries = []
    for kelime in anahtar_kelimeler:
        url = "https://api.crossref.org/works"
        params = {"query": kelime, "select": "title,URL,abstract", "sort": "published", "order": "desc", "rows": 2}
        
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200: continue
            
            items = resp.json().get("message", {}).get("items", [])
            for paper in items:
                link = paper.get("URL")
                if not link: continue
                    
                title_list = paper.get("title", [])
                title = title_list[0] if title_list else "Başlıksız"
                abstract = paper.get("abstract", "Özet sunucu tarafından sağlanmadı.")
                
                summary = summarize_text(title, abstract)
                entries.append({"title": title, "link": link, "summary": summary})
        except Exception:
            pass
    return entries

def send_telegram(message: str, reply_markup: dict | None = None) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
    if reply_markup: payload["reply_markup"] = json.dumps(reply_markup)
    
    for _ in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 429:
                sleep(2)
                continue
            break
        except Exception:
            sleep(2)

def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info("Jeoloji Takip Botu başlatıldı")

    try:
        config = load_config()
        seen_urls = load_seen_urls()
        entries = fetch_semantic_entries(config["anahtar_kelimeler"], seen_urls)

        if entries:
            header = f"🌍 *Jeoloji Takip Botu*\n📅 {now}\n📊 {len(entries)} yeni içerik\n{'─'*30}\n"
            send_telegram(header)

            for idx, entry in enumerate(entries):
                title = entry.get("title", "Başlıksız").replace("*", "\\*")
                summary = entry.get("summary", "")
                
                # Madde imlerini düzenle
                lines = [ln.strip() for ln in summary.splitlines() if ln.strip()][:5]
                bullets = "\n".join(f"{ln}" if ln.startswith("-") or ln.startswith("*") else f"- {ln}" for ln in lines)
                
                msg = f"**{title}**\n{bullets}\n\n[Detaylı Oku]({entry.get('link')})"
                send_telegram(msg, reply_markup={"inline_keyboard": [[{"text": "Arşive Kaydet 📁", "callback_data": f"archive_{idx}"}]]})

            handle_callbacks(entries)
        else:
            logger.info("Yeni makale bulunamadı.")

        now_iso = datetime.now(timezone.utc).isoformat()
        new_count = 0
        for entry in entries:
            if entry.get("link") not in seen_urls:
                seen_urls[entry.get("link")] = now_iso
                new_count += 1
                
        if new_count > 0:
            save_seen_urls(seen_urls)

    except Exception as exc:
        sys.exit(1)

if __name__ == "__main__":
    main()
