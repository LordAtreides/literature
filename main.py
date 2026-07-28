"""
Jeoloji Takip Botu (Nihai Sürüm)
================================
- Crossref API ile akademik makale arar.
- Google ListModels API ile çalışan Gemini modelini OTOMATİK keşfeder.
- Sadece Telegram'a başarıyla gönderilen makaleleri hafızaya kaşlar.
- Google Sheets arşivleme butonunu destekler.
"""

import json
import gspread
import os
import sys
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
import requests

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

ERROR_LOG_PATH = Path(__file__).parent / "error.log"
_fh = logging.handlers.RotatingFileHandler(ERROR_LOG_PATH, maxBytes=1_048_576, backupCount=3, encoding="utf-8")
_fh.setLevel(logging.WARNING)
_fh.setFormatter(logging.Formatter(LOG_FORMAT))
logger.addHandler(_fh)

# ─── Ortam Değişkenleri ─────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

CONFIG_PATH = Path(__file__).parent / "config.json"
SEEN_URLS_PATH = Path(__file__).parent / "seen_urls.json"
UPDATE_OFFSET_PATH = Path(__file__).parent / "update_offset.txt"

# ─── Yardımcı Fonksiyonlar ──────────────────────────────────────────────────
def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_seen_urls():
    if not SEEN_URLS_PATH.exists():
        return {}
    try:
        with open(SEEN_URLS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_seen_urls(seen_urls):
    with open(SEEN_URLS_PATH, "w", encoding="utf-8") as f:
        json.dump(seen_urls, f, ensure_ascii=False, indent=2)

# ─── Google Sheets ──────────────────────────────────────────────────────────
def get_gsheets_client():
    if not GOOGLE_CREDENTIALS:
        return None
    try:
        return gspread.service_account_from_dict(json.loads(GOOGLE_CREDENTIALS))
    except Exception as e:
        logger.error("Sheets oturum hatası: %s", e)
        return None

def archive_to_sheet(entry):
    if not GOOGLE_SHEET_ID:
        return
    client = get_gsheets_client()
    if not client:
        return
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        sh.sheet1.append_row([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "Jeoloji", entry.get("title", ""), entry.get("link", ""), entry.get("summary", "")
        ], value_input_option="RAW")
    except Exception as e:
        logger.error("Sheets arşiv hatası: %s", e)

# ─── Telegram Callback ─────────────────────────────────────────────────────
def handle_callbacks(entries):
    if not UPDATE_OFFSET_PATH.exists():
        offset = 0
    else:
        try:
            offset = int(UPDATE_OFFSET_PATH.read_text().strip())
        except Exception:
            offset = 0
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": offset + 1, "timeout": 10}, timeout=15
        )
        data = resp.json()
        if data.get("ok") and data.get("result"):
            for update in data["result"]:
                offset = update["update_id"]
                if "callback_query" in update:
                    cb = update["callback_query"]
                    d = cb.get("data", "")
                    if d.startswith("archive_"):
                        idx = int(d.split("_")[1])
                        if 0 <= idx < len(entries):
                            archive_to_sheet(entries[idx])
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                json={"callback_query_id": cb["id"], "text": "Arşivlendi! ✅"}
                            )
            UPDATE_OFFSET_PATH.write_text(str(offset))
    except Exception:
        pass

# ─── Gemini: Model Otomatik Keşif ──────────────────────────────────────────
def discover_gemini_model():
    """Google'ın ListModels API'si ile çalışan ilk text modelini otomatik bulur."""
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY tanımlanmamış!")
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    try:
        resp = requests.get(url, timeout=10)
        logger.info("ListModels API yanıt kodu: %s", resp.status_code)
        if resp.status_code != 200:
            logger.error("ListModels başarısız: %s", resp.text[:500])
            return None
        models = resp.json().get("models", [])
        for m in models:
            methods = m.get("supportedGenerationMethods", [])
            name = m.get("name", "")
            if "generateContent" in methods:
                logger.info("✅ Kullanılacak model bulundu: %s", name)
                return name
        logger.error("generateContent destekleyen model bulunamadı!")
    except Exception as e:
        logger.error("Model keşif hatası: %s", e)
    return None

def summarize_with_gemini(model_name, title, abstract):
    """Keşfedilen Gemini modeli ile Türkçe özet üretir."""
    fallback = abstract[:500] + "..." if len(abstract) > 500 else abstract
    if not model_name:
        return fallback

    prompt = (
        "Sen uzman bir jeologsun. Aşağıdaki akademik makaleyi incele ve "
        "anlaşılır bir dille Türkçe 3 maddelik kısa bir özet çıkar.\n\n"
        f"Başlık: {title}\nÖzet: {abstract}"
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        resp = requests.post(url, json=payload, timeout=20)
        logger.info("Gemini yanıt kodu: %s (model: %s)", resp.status_code, model_name)
        if resp.status_code == 200:
            data = resp.json()
            if "candidates" in data and data["candidates"]:
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        else:
            logger.error("Gemini hata detayı: %s", resp.text[:300])
    except Exception as e:
        logger.error("Gemini istek hatası: %s", e)
    return fallback

# ─── Crossref API ile Makale Çekme ─────────────────────────────────────────
def fetch_articles(anahtar_kelimeler, seen_urls, gemini_model):
    entries = []
    headers = {"User-Agent": "JeolojiBot/1.0 (mailto:jeolojibot@example.com)"}

    for kelime in anahtar_kelimeler:
        logger.info("🔍 Aranıyor: %s", kelime)
        params = {
            "query": kelime,
            "select": "title,URL,abstract",
            "sort": "published",
            "order": "desc",
            "rows": 2
        }
        try:
            resp = requests.get("https://api.crossref.org/works", params=params, headers=headers, timeout=15)
            logger.info("Crossref yanıt kodu (%s): %s", kelime, resp.status_code)
            if resp.status_code != 200:
                logger.error("Crossref reddetti: %s", resp.text[:300])
                continue

            items = resp.json().get("message", {}).get("items", [])
            logger.info("Crossref sonuç sayısı (%s): %d", kelime, len(items))

            for paper in items:
                link = paper.get("URL")
                if not link or link in seen_urls:
                    continue

                title_list = paper.get("title", [])
                title = title_list[0] if title_list else "Başlıksız"
                abstract = paper.get("abstract", "Özet sunucu tarafından sağlanmadı.")

                summary = summarize_with_gemini(gemini_model, title, abstract)
                entries.append({"title": title, "link": link, "summary": summary})
                logger.info("📄 Yeni makale: %s", title[:80])
        except Exception as e:
            logger.error("Crossref istek hatası (%s): %s", kelime, e)

    return entries

# ─── Telegram Gönderimi ────────────────────────────────────────────────────
def send_telegram(message, reply_markup=None):
    """Telegram'a mesaj gönderir. Başarılıysa True, değilse False döner."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=20)
            logger.info("Telegram yanıt kodu: %s", resp.status_code)
            if resp.status_code == 200:
                return True
            elif resp.status_code == 429:
                sleep(2)
                continue
            else:
                logger.error("Telegram hata detayı: %s", resp.text[:300])
                return False
        except Exception as e:
            logger.error("Telegram gönderim hatası: %s", e)
            sleep(2)
    return False

# ─── Ana Program ───────────────────────────────────────────────────────────
def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info("═══ Jeoloji Takip Botu başlatıldı (%s) ═══", now)

    # Temel kontroller
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.critical("TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID tanımlanmamış!")
        sys.exit(1)

    logger.info("Telegram Token: %s...%s", TELEGRAM_BOT_TOKEN[:8], TELEGRAM_BOT_TOKEN[-4:])
    logger.info("Telegram Chat ID: %s", TELEGRAM_CHAT_ID)

    # 1. Config ve hafıza yükle
    config = load_config()
    anahtar_kelimeler = config["anahtar_kelimeler"]
    seen_urls = load_seen_urls()
    logger.info("Anahtar kelimeler: %s", anahtar_kelimeler)
    logger.info("Hafızadaki URL sayısı: %d", len(seen_urls))

    # 2. Gemini modelini otomatik keşfet
    gemini_model = discover_gemini_model()

    # 3. Makaleleri çek ve özetle
    entries = fetch_articles(anahtar_kelimeler, seen_urls, gemini_model)
    logger.info("Toplam yeni makale sayısı: %d", len(entries))

    # 4. Telegram'a gönder (sadece yeni içerik varsa)
    if entries:
        header = (
            f"🌍 *Jeoloji Takip Botu*\n"
            f"📅 {now}\n"
            f"📊 {len(entries)} yeni içerik\n"
            f"{'─' * 30}\n"
        )
        send_telegram(header)

        successfully_sent = []
        for idx, entry in enumerate(entries):
            title = entry.get("title", "Başlıksız").replace("*", "\\*")
            summary = entry.get("summary", "")

            lines = [ln.strip() for ln in summary.splitlines() if ln.strip()][:5]
            bullets = "\n".join(
                ln if ln.startswith("-") or ln.startswith("*") else f"- {ln}"
                for ln in lines
            )

            msg = f"*{title}*\n{bullets}\n\n[Detaylı Oku]({entry.get('link')})"
            keyboard = {"inline_keyboard": [[{"text": "Arşive Kaydet 📁", "callback_data": f"archive_{idx}"}]]}

            if send_telegram(msg, reply_markup=keyboard):
                successfully_sent.append(entry)
                logger.info("✅ Telegram'a gönderildi: %s", title[:60])
            else:
                logger.error("❌ Telegram'a gönderilemedi: %s", title[:60])

        # 5. SADECE başarıyla gönderilenleri hafızaya kaydet
        now_iso = datetime.now(timezone.utc).isoformat()
        for entry in successfully_sent:
            seen_urls[entry["link"]] = now_iso

        if successfully_sent:
            save_seen_urls(seen_urls)
            logger.info("%d URL hafızaya kaydedildi.", len(successfully_sent))

        handle_callbacks(entries)
    else:
        logger.info("Yeni makale bulunamadı. Telegram'a mesaj gönderilmeyecek.")

    logger.info("═══ İşlem tamamlandı ═══")

if __name__ == "__main__":
    main()
