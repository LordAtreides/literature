import requests
from time import sleep
from tenacity import retry, wait_exponential, stop_after_attempt
from src.core.config import logger, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from src.core.utils import safe_html

CATEGORY_HEADERS = {
    "makale": "📚 AKADEMIK MAKALELER",
    "on_baski": "📄 ON BASKILAR",
    "haber": "📢 HABERLER",
    "duyuru": "📣 DUYURULAR",
    "firsat": "🎓 BURS & FIRSATLAR",
    "forum": "💬 FORUM & TARTISMALAR",
}

def build_bulletin_message(items, now_str):
    categorized = {}
    for item in items:
        cat = item.get("category", "haber")
        if cat not in categorized: categorized[cat] = []
        categorized[cat].append(item)

    parts = [f"📰 <b>JEOLOJI V3 BULTENI</b>", f"📅 {now_str} | 🔬 {len(items)} secit", ""]
    counter = 1

    for cat in ["makale", "on_baski", "haber", "duyuru", "firsat", "forum"]:
        if cat not in categorized: continue
        parts.extend([f"━━━ {CATEGORY_HEADERS.get(cat, cat.upper())} ━━━", ""])
        for item in categorized[cat]:
            title = safe_html(item.get("title", "Basliksiz"))
            summary = safe_html(item.get("summary", ""))
            
            lines = [ln.strip() for ln in summary.splitlines() if ln.strip()][:2]
            summary_text = "\n".join(f"   ↳ {ln}" for ln in lines) if lines else f"   ↳ {summary[:120]}"

            link = safe_html(item.get("link", ""))
            parts.extend([
                f"{counter}️⃣ <b>{title}</b>",
                summary_text,
                f"   🔗 <a href=\"{link}\">Oku</a> | 📊 {item.get('score', '?')}/10",
                ""
            ])
            counter += 1
    return "\n".join(parts)

@retry(wait=wait_exponential(multiplier=1, min=4, max=10), stop=stop_after_attempt(3))
def _post_telegram(url, payload):
    resp = requests.post(url, json=payload, timeout=20)
    if resp.status_code == 429:
        raise Exception("Rate limited")
    if resp.status_code == 400 and "too long" in resp.text.lower():
        # Handle chunking manually in outer func
        return resp
    resp.raise_for_status()
    return resp

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    
    try:
        resp = _post_telegram(url, payload)
        if resp.status_code == 400 and "too long" in resp.text.lower():
            max_len = 4000
            current = ""
            for line in message.split("\n"):
                if len(current) + len(line) + 1 > max_len:
                    _post_telegram(url, {"chat_id": TELEGRAM_CHAT_ID, "text": current, "parse_mode": "HTML"})
                    current = line
                else:
                    current += "\n" + line if current else line
            if current: 
                _post_telegram(url, {"chat_id": TELEGRAM_CHAT_ID, "text": current, "parse_mode": "HTML"})
        return True
    except Exception as e:
        logger.error(f"Telegram istek hatasi: {e}")
        return False
