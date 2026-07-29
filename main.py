"""
Jeoloji Takip Botu V3 (Funneling Architecture)
==============================================
Katman 1: Coklu Is Parcacigi ile 5 Kaynaktan Paralel Tarama
Katman 2: Fuzzy Deduplication (Baslik + URL tekillestirme)
Katman 3: Gemini Embeddings ile Vektorel Ilgi Suzgeci (Cosine Sim > 0.65)
Katman 4: Gemini Flash ile Structured JSON Puanlama (1-10)
Katman 5: Claude 3.5 Sonnet ile Derin Analiz (8+ icin)
Cikti: 8+ puanlilar Telegram'a, 4-7 puanlilar Web/Google Sheets'e, 1-3 Cöp.
"""

import json
import re
import gspread
import os
import sys
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from html import escape as html_escape_builtin
import concurrent.futures

import requests
import feedparser
import numpy as np
from thefuzz import fuzz

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

ERROR_LOG_PATH = Path(__file__).parent / "error.log"
_fh = logging.handlers.RotatingFileHandler(
    ERROR_LOG_PATH, maxBytes=1_048_576, backupCount=3, encoding="utf-8"
)
_fh.setLevel(logging.WARNING)
_fh.setFormatter(logging.Formatter(LOG_FORMAT))
logger.addHandler(_fh)

# ─── Ortam Degiskenleri ─────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

CONFIG_PATH = Path(__file__).parent / "config.json"
SEEN_URLS_PATH = Path(__file__).parent / "seen_urls.json"
UPDATE_OFFSET_PATH = Path(__file__).parent / "update_offset.txt"

# ─── Yardimci Fonksiyonlar ──────────────────────────────────────────────────

def safe_html(text):
    if not text: return ""
    return html_escape_builtin(str(text))

def strip_html_tags(text):
    if not text: return ""
    return re.sub(r"<[^>]+>", "", str(text)).strip()

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_seen_urls():
    if not SEEN_URLS_PATH.exists(): return {}
    try:
        with open(SEEN_URLS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_seen_urls(seen_urls):
    with open(SEEN_URLS_PATH, "w", encoding="utf-8") as f:
        json.dump(seen_urls, f, ensure_ascii=False, indent=2)

# ─── Google Sheets (Web / 4-7 Puan Arastirmalari) ──────────────────────────

def get_gsheets_client():
    if not GOOGLE_CREDENTIALS: return None
    try:
        return gspread.service_account_from_dict(json.loads(GOOGLE_CREDENTIALS))
    except Exception as e:
        logger.error("Sheets oturum hatasi: %s", e)
        return None

def batch_archive_to_sheet(entries):
    """Gelen listeyi tek bir API istegiyle Google Sheets'e kaydeder."""
    if not GOOGLE_SHEET_ID or not entries: return
    client = get_gsheets_client()
    if not client: return
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        try:
            worksheet = sh.worksheet("Genel Bakış")
        except Exception:
            worksheet = sh.sheet1
            worksheet.update_title("Genel Bakış")
        
        rows = []
        for entry in entries:
            sheet_type = "telegram" if entry.get("score", 0) >= 7 else "web"
            rows.append([
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                entry.get("source", ""),
                entry.get("title", ""),
                entry.get("link", ""),
                entry.get("summary", "N/A"),
                str(entry.get("score", "")),
                sheet_type
            ])
            
        worksheet.append_rows(rows, value_input_option="RAW")
        logger.info("%d satir Google Sheets'e (toplu) kaydedildi.", len(rows))
    except Exception as e:
        logger.error("Sheets toplu arsiv hatasi: %s", e)

# ═══════════════════════════════════════════════════════════════════════════
# KATMAN 1: PARALEL TARAMA (COLLECTORS)
# ═══════════════════════════════════════════════════════════════════════════

def collect_from_crossref(config):
    entries = []
    headers = {"User-Agent": "JeolojiBot/3.0 (mailto:jeolojibot@example.com)"}
    kws = config.get("anahtar_kelimeler", {})
    limit = config.get("ayarlar", {}).get("crossref_sonuc_limiti", 2)
    all_keywords = kws.get("crossref_en", []) + kws.get("crossref_de", []) + kws.get("crossref_fr", [])

    for kw in all_keywords:
        try:
            resp = requests.get("https://api.crossref.org/works",
                params={"query": kw, "select": "title,URL,abstract", "sort": "published", "order": "desc", "rows": limit},
                headers=headers, timeout=10)
            if resp.status_code == 200:
                for paper in resp.json().get("message", {}).get("items", []):
                    link = paper.get("URL", "")
                    title_list = paper.get("title", [])
                    title = title_list[0] if title_list else ""
                    if link and title:
                        entries.append({
                            "title": title, "link": link, "abstract": strip_html_tags(paper.get("abstract", "")),
                            "source": "Crossref", "category": "makale"
                        })
        except Exception: pass
        sleep(0.3)
    logger.info("Crossref: %d", len(entries))
    return entries

def collect_from_arxiv(config):
    entries = []
    queries = config.get("anahtar_kelimeler", {}).get("arxiv_queries", [])
    limit = config.get("ayarlar", {}).get("arxiv_sonuc_limiti", 5)

    for query in queries:
        try:
            url = "http://export.arxiv.org/api/query"
            params = {"search_query": f"cat:physics.geo-ph AND all:({query})", "sortBy": "submittedDate", "sortOrder": "descending", "max_results": limit}
            resp = requests.get(url, params=params, timeout=10, headers={"User-Agent": "JeolojiBot/3.0"})
            if resp.status_code == 200:
                for entry in feedparser.parse(resp.text).entries:
                    link, title = entry.get("link", ""), entry.get("title", "").replace("\n", " ").strip()
                    if link and title:
                        entries.append({
                            "title": title, "link": link, "abstract": entry.get("summary", "").replace("\n", " ").strip(),
                            "source": "arXiv", "category": "on_baski"
                        })
        except Exception: pass
        sleep(1)
    logger.info("arXiv: %d", len(entries))
    return entries

def collect_from_eartharxiv(config):
    entries = []
    limit = config.get("ayarlar", {}).get("eartharxiv_sonuc_limiti", 5)
    try:
        resp = requests.get("https://api.osf.io/v2/preprints/",
            params={"filter[provider]": "eartharxiv", "sort": "-date_created", "page[size]": limit},
            timeout=10, headers={"User-Agent": "JeolojiBot/3.0"})
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                attrs = item.get("attributes", {})
                title = attrs.get("title", "")
                doi = attrs.get("doi", "")
                link = f"https://doi.org/{doi}" if doi else attrs.get("preprint_doi_created", "")
                if not link:
                    links = item.get("links", {})
                    link = links.get("html", links.get("preprint_doi", ""))
                if title and link:
                    entries.append({
                        "title": title, "link": link, "abstract": strip_html_tags(attrs.get("description", "")),
                        "source": "EarthArXiv", "category": "on_baski"
                    })
    except Exception: pass
    logger.info("EarthArXiv: %d", len(entries))
    return entries

def collect_from_google_news(config):
    entries = []
    for feed_info in config.get("google_news_rss", []):
        try:
            resp = requests.get(feed_info.get("url", ""), timeout=10, headers={"User-Agent": "JeolojiBot/3.0"})
            if resp.status_code == 200:
                for entry in feedparser.parse(resp.text).entries[:10]:
                    link, title = entry.get("link", ""), entry.get("title", "")
                    if link and title:
                        entries.append({
                            "title": strip_html_tags(title), "link": link,
                            "abstract": strip_html_tags(entry.get("summary", entry.get("description", ""))),
                            "source": f"Google News ({feed_info.get('name', '')})", "category": feed_info.get("category", "haber")
                        })
        except Exception: pass
        sleep(0.5)
    logger.info("Google News: %d", len(entries))
    return entries

def collect_from_reddit(config):
    entries = []
    for sub in config.get("reddit_subreddits", []):
        try:
            resp = requests.get(f"https://www.reddit.com/r/{sub}/new/.rss", timeout=10, headers={"User-Agent": "JeolojiBot/3.0 (educational)"})
            if resp.status_code == 200:
                for entry in feedparser.parse(resp.text).entries[:10]:
                    link, title = entry.get("link", ""), entry.get("title", "")
                    if link and title:
                        entries.append({
                            "title": strip_html_tags(title), "link": link,
                            "abstract": strip_html_tags(entry.get("summary", ""))[:300],
                            "source": f"Reddit (r/{sub})", "category": "forum"
                        })
        except Exception: pass
        sleep(1)
    logger.info("Reddit: %d", len(entries))
    return entries

def collect_from_tavily(config):
    """Ozel kaynaklari Tavily Search API kullanarak tarar."""
    entries = []
    if not TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY eksik, ozel kaynak aramasi atlandi.")
        return entries
        
    kaynaklar = config.get("ozel_kaynaklar", [])
    if not kaynaklar: return entries
        
    academic_kws = "Quaternary geology OR remote sensing OR sedimentology OR Mars"
    opportunity_kws = "PhD scholarship OR grant OR internship OR geosciences"
    
    url = "https://api.tavily.com/search"
    
    for group in kaynaklar:
        search_type = group.get("search_type", "academic")
        kws = opportunity_kws if search_type == "opportunity" else academic_kws
        category = "firsat" if search_type == "opportunity" else "makale"
        
        # Tavily include_domains parametresi alir
        urls = [res.get("url") for res in group.get("resources", []) if res.get("url")]
        
        # Tavily'ye ayni anda cok fazla domain atmak yerine 5'erli bolelim
        for i in range(0, len(urls), 5):
            batch_urls = urls[i:i+5]
            
            payload = {
                "api_key": TAVILY_API_KEY,
                "query": kws,
                "include_domains": batch_urls,
                "max_results": 5,
                "search_depth": "basic",
                "days": 7
            }
            
            try:
                resp = requests.post(url, json=payload, timeout=20)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("results", []):
                        entries.append({
                            "title": item.get("title", ""),
                            "link": item.get("url", ""),
                            "abstract": item.get("content", ""),
                            "source": "Tavily Custom",
                            "category": category
                        })
                else:
                    logger.error("Tavily API Hatasi [%d]: %s", resp.status_code, resp.text)
            except Exception as e:
                logger.error("Tavily istek hatasi: %s", e)
                
            sleep(1.5) # Limit korumasi
                
    logger.info("Tavily Search: %d icerik toplandi", len(entries))
    return entries

def run_parallel_collection(config):
    """6 kaynagi ThreadPool ile ayni anda tarar."""
    all_items = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        f1 = executor.submit(collect_from_crossref, config)
        f2 = executor.submit(collect_from_arxiv, config)
        f3 = executor.submit(collect_from_eartharxiv, config)
        f4 = executor.submit(collect_from_google_news, config)
        f5 = executor.submit(collect_from_reddit, config)
        f6 = executor.submit(collect_from_tavily, config)
        
        for future in concurrent.futures.as_completed([f1, f2, f3, f4, f5, f6]):
            try:
                all_items.extend(future.result())
            except Exception as e:
                logger.error("Collector hatasi: %s", e)
    return all_items

# ═══════════════════════════════════════════════════════════════════════════
# KATMAN 2: GELISMIS TEKILLESTIRME (FUZZY + CACHE)
# ═══════════════════════════════════════════════════════════════════════════

def deduplicate_items(items, seen_urls):
    unique = []
    seen_links = set()
    accepted_titles = []

    for item in items:
        link = item.get("link", "")
        title = item.get("title", "")
        
        # 1. Tam link eslesmesi ve gecmis kontrolu
        if not link or link in seen_urls or link in seen_links:
            continue
            
        # 2. Fuzzy Title Matching (Ayni makale farkli platformda ise)
        is_duplicate = False
        for accepted in accepted_titles:
            # Benzerlik orani %85 uzeriyse ayni makale say
            if fuzz.ratio(title.lower(), accepted.lower()) > 85:
                is_duplicate = True
                break
                
        if not is_duplicate:
            seen_links.add(link)
            accepted_titles.append(title)
            unique.append(item)
            
    logger.info("Deduplication: %d ham -> %d tekil", len(items), len(unique))
    return unique

# ═══════════════════════════════════════════════════════════════════════════
# KATMAN 3: EMBEDDING VEKTOREL FILTRE
# ═══════════════════════════════════════════════════════════════════════════

def get_gemini_embeddings(texts):
    """Gemini API kullanarak metinlerin vektörlerini (embeddings) alır."""
    if not GEMINI_API_KEY: return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:batchEmbedContents?key={GEMINI_API_KEY}"
    requests_data = [{"model": "models/text-embedding-004", "content": {"parts": [{"text": t[:1000]}]}} for t in texts]
    try:
        resp = requests.post(url, json={"requests": requests_data}, timeout=30)
        if resp.status_code == 200:
            return [embed["values"] for embed in resp.json().get("embeddings", [])]
    except Exception as e:
        logger.error("Embedding hatasi: %s", e)
    return None

def cosine_similarity(v1, v2):
    vec1, vec2 = np.array(v1), np.array(v2)
    norm = np.linalg.norm(vec1) * np.linalg.norm(vec2)
    return np.dot(vec1, vec2) / norm if norm else 0.0

def embedding_filter(items, config):
    """Kosinus benzerligi > 0.65 olanlari secer."""
    if not items or not GEMINI_API_KEY: return items
    
    # Referans vektoru (Kullanici profilinden)
    profile = config.get("kullanici_profili", {})
    ref_text = f"Jeoloji Muhendisligi {', '.join(profile.get('uzmanlik', []))} akademik makale bilimsel arastirma"
    
    # API limiti geregi max 100'luk parcalarla embedding aliyoruz
    all_embeddings = []
    texts_to_embed = [ref_text] + [f"{i.get('title','')} {i.get('abstract','')}" for i in items]
    
    for i in range(0, len(texts_to_embed), 100):
        batch_texts = texts_to_embed[i:i+100]
        embs = get_gemini_embeddings(batch_texts)
        if embs:
            all_embeddings.extend(embs)
        else:
            # Hata durumunda kalanlari bos doldur
            all_embeddings.extend([None]*len(batch_texts))
            
    if not all_embeddings or all_embeddings[0] is None:
        logger.warning("Embedding alinamadi, filtre atlandi.")
        return items

    ref_vec = all_embeddings[0]
    passed_items = []
    
    for idx, item in enumerate(items):
        vec = all_embeddings[idx + 1]
        if vec:
            sim = cosine_similarity(ref_vec, vec)
            if sim > 0.65:
                item["cosine_sim"] = round(sim, 3)
                passed_items.append(item)
        else:
            passed_items.append(item) # Vektor alinamayanlari gecir
            
    logger.info("Embedding Filtresi: %d -> %d gecti (>0.65)", len(items), len(passed_items))
    return passed_items

# ═══════════════════════════════════════════════════════════════════════════
# KATMAN 4: STRUCTURED JSON LLM PUANLAMA
# ═══════════════════════════════════════════════════════════════════════════

def find_working_gemini_model():
    if not GEMINI_API_KEY: return None
    try:
        resp = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}", timeout=10)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
            for c in ["models/gemini-1.5-flash", "models/gemini-1.5-pro", "models/gemini-pro"]:
                if c in models: return c
    except Exception: pass
    return "models/gemini-1.5-flash"

def structured_batch_score(items, model_name, config):
    if not items or not model_name:
        for i in items: i["score"] = 5
        return items

    profile_text = config.get("kullanici_profili", {}).get("aciklama", "Jeoloji")
    batch_size = 15
    scored = []

    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        
        # JSON semasina uygun input hazirla
        input_data = [{"id": str(j), "title": item["title"], "abstract": strip_html_tags(item.get("abstract",""))[:150]} for j, item in enumerate(batch)]
        
        prompt = f"""Sen bir jeoloji akademisyenisin. Profil: {profile_text}
Aşağıdaki makalelerin bu profile ne kadar uygun olduğunu 1-10 arası puanla.
(9-10: Çok önemli, 7-8: İlgili, 4-6: Dolaylı, 1-3: Çöp).

Format: SADECE JSON dönmelisin.
Şema: {{"results": [{{"id": "string", "score": integer}}]}}

İçerikler:
{json.dumps(input_data, ensure_ascii=False)}"""

        url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }
        
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                result_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                if result_text.startswith("```json"): result_text = result_text[7:]
                elif result_text.startswith("```"): result_text = result_text[3:]
                if result_text.endswith("```"): result_text = result_text[:-3]
                
                parsed = json.loads(result_text.strip())
                
                # Sonuclari eslestir
                for res in parsed.get("results", []):
                    try:
                        idx = int(res["id"])
                        if idx < len(batch):
                            batch[idx]["score"] = res["score"]
                    except Exception: pass
        except Exception as e:
            logger.error("JSON LLM hatasi: %s", e)
            
        # Puan alamayanlara varsayilan ver
        for item in batch:
            if "score" not in item: item["score"] = 5
        scored.extend(batch)
        sleep(2) # Limit korumasi
        
    return scored

# ═══════════════════════════════════════════════════════════════════════════
# KATMAN 5: DERIN ANALIZ (CLAUDE 3.5 SONNET)
# ═══════════════════════════════════════════════════════════════════════════

def claude_deep_analysis(item):
    """Claude Sonnet API kullanarak ve Prompt Caching ile özet cikarir."""
    if not HAS_ANTHROPIC or not CLAUDE_API_KEY:
        return item.get("abstract", "")[:200] + "..."
        
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    
    system_prompt = """Bir akademik jeoloji metnini Türkçe'ye çevirip özetleyeceksin.
KURALLAR:
- SADECE VE SADECE 2 SATIR yazacaksın.
- 1. Satır: Ana bulgu/keşif nedir?
- 2. Satır: Metodoloji veya çalışmanın bilimsel önemi nedir?
- "Özetle, sonucunda, incelenmiştir" gibi yapay zeka dili (AI cliches) kullanmak YASAK.
- Net, objektif ve doğrudan akademik bilgi ver."""

    user_prompt = f"Başlık: {item.get('title')}\nÖzet: {strip_html_tags(item.get('abstract'))[:1000]}"

    try:
        message = client.messages.create(
            model="claude-3-5-sonnet-latest", # Claude Sonnet guncel
            max_tokens=150,
            temperature=0.1,
            system=[
                {
                    "type": "text", 
                    "text": system_prompt, 
                    "cache_control": {"type": "ephemeral"} # Prompt Caching aktif (maliyeti dusurur)
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
        )
        return message.content[0].text.strip()
    except Exception as e:
        logger.error("Claude API hatasi: %s", e)
        return strip_html_tags(item.get("abstract", ""))[:200]

# ═══════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════

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

            parts.extend([
                f"{counter}️⃣ <b>{title}</b>",
                summary_text,
                f"   🔗 <a href=\"{item.get('link', '')}\">Oku</a> | 📊 {item.get('score', '?')}/10",
                ""
            ])
            counter += 1
    return "\n".join(parts)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    for _ in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 200: return True
            if resp.status_code == 429: sleep(2); continue
            
            # Too long ise parcala
            if resp.status_code == 400 and "too long" in resp.text.lower():
                max_len = 4000
                current = ""
                for line in message.split("\n"):
                    if len(current) + len(line) + 1 > max_len:
                        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": current, "parse_mode": "HTML"}, timeout=10)
                        current = line
                    else:
                        current += "\n" + line if current else line
                if current: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": current, "parse_mode": "HTML"}, timeout=10)
                return True
            return False
        except Exception: sleep(2)
    return False

# ═══════════════════════════════════════════════════════════════════════════
# ANA PROGRAM
# ═══════════════════════════════════════════════════════════════════════════

def main():
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%d %B %Y, %H:%M UTC")
    logger.info("=== Jeoloji Takip Botu V3 (Funnel) baslatildi ===")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.critical("Telegram token/id eksik!")
        sys.exit(1)

    config = load_config()
    seen_urls = load_seen_urls()
    
    # 1. PARALEL TARAMA
    logger.info("KATMAN 1: Paralel Tarama basladi")
    raw_items = run_parallel_collection(config)
    if not raw_items:
        logger.info("Yeni icerik bulunamadi. Cikis.")
        return

    # 2. GELISMIS TEKILLESTIRME
    logger.info("KATMAN 2: Fuzzy Deduplication")
    unique_items = deduplicate_items(raw_items, seen_urls)

    # 3. EMBEDDING FILTRE
    logger.info("KATMAN 3: Gemini Embedding Filtresi")
    filtered_items = embedding_filter(unique_items, config)

    # 4. STRUCTURED LLM PUANLAMA
    logger.info("KATMAN 4: Structured JSON Puanlama (Gemini)")
    gemini_model = find_working_gemini_model()
    scored_items = structured_batch_score(filtered_items, gemini_model, config)

    # 5. IKI ADIMLI DAGITIM (ROUTING)
    telegram_items = []  # 7-10 puan
    web_items = []       # 4-6 puan
    trash_items = []     # 1-3 puan
    
    for item in scored_items:
        sc = item.get("score", 0)
        if sc >= 7: 
            telegram_items.append(item)
            web_items.append(item) # 7+ olanlar web'e de kaydedilecek
        elif sc >= 4: 
            web_items.append(item)
        else: 
            trash_items.append(item)
        
    logger.info("ROUTING: %d Telegram (7+), %d Web (4-10), %d Cöp (1-3)", len(telegram_items), len(web_items), len(trash_items))

    # WEB (4-10 Puanlari Google Sheets'e kaydet)
    if web_items:
        batch_archive_to_sheet(web_items)

    # DERIN ANALIZ VE TELEGRAM
    if telegram_items:
        logger.info("KATMAN 5: Claude Derin Analiz")
        for item in telegram_items:
            item["summary"] = claude_deep_analysis(item)
            
        logger.info("Bulten Gonderiliyor...")
        if send_telegram(build_bulletin_message(telegram_items, now_str)):
            logger.info("Bulten basariyla gonderildi!")

    # Hafizayi Guncelle (Tum islenen unique_items linklerini kaydet)
    now_iso = now.isoformat()
    for item in unique_items:
        seen_urls[item.get("link", "")] = now_iso
    save_seen_urls(seen_urls)
    logger.info("Islem tamamlandi. %d yeni URL hafizaya alindi.", len(unique_items))

if __name__ == "__main__":
    main()
