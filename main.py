"""
Jeoloji Takip Botu V4 (Moduler Yapi)
==============================================
"""

import sys
from datetime import datetime, timezone
from src.core.config import (
    logger, load_config, load_seen_urls, save_seen_urls,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
)
from src.core.utils import deduplicate_items
from src.core.memory import semantic_memory
from src.scrapers import run_parallel_collection
from src.scoring.claude import claude_batch_score, claude_deep_analysis
from src.notifiers import send_telegram, build_bulletin_message, batch_archive_to_sheet, create_and_send_podcast

def main():
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%d %B %Y, %H:%M UTC")
    logger.info("=== Jeoloji Takip Botu V4 (Moduler) baslatildi ===")

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

    # 3. CLAUDE HAIKU PUANLAMA
    logger.info("KATMAN 3: Claude Haiku Puanlama")
    scored_items = claude_batch_score(unique_items, config)

    # 4. BAYAT ICERIK SUZGECI (Mekanik Guvenlik Agi - Sadece Firsatlar Icin)
    current_year = now.year
    stale_years = {str(y) for y in range(2010, current_year - 1)}
    stale_count = 0
    for item in scored_items:
        title_lower = item.get("title", "").lower()
        link_lower = item.get("link", "").lower()
        cat = item.get("category", "")
        if cat in ("firsat", "duyuru", "opportunity"):
            for year in stale_years:
                if year in title_lower or year in link_lower:
                    item["score"] = 1
                    stale_count += 1
                    break
    if stale_count:
        logger.info("Bayat Suzgec: %d icerik dusuruldu.", stale_count)

    # 5. IKI ADIMLI DAGITIM (ROUTING) - DINAMIK ESIKLEME ILE
    telegram_items = []
    web_items = []
    trash_items = []
    
    # Niche (Spesifik) kelimeler - Eger bunlar varsa baraj 6'ya duser. Yoksa baraj 8'dir.
    niche_keywords = ["mars", "quaternary", "sedimentology", "bathymetry", "neotectonic", "paleoclimatology"]
    
    for item in scored_items:
        sc = item.get("score", 0)
        
        # Dinamik baraj hesabi
        threshold = 8
        title_lower = item.get("title", "").lower()
        abstract_lower = item.get("abstract", "").lower()
        
        if any(kw in title_lower or kw in abstract_lower for kw in niche_keywords):
            threshold = 6
            
        if sc >= threshold: 
            telegram_items.append(item)
            web_items.append(item)
        elif sc >= 4: 
            web_items.append(item)
        else: 
            trash_items.append(item)
        
    logger.info("ROUTING: %d Telegram (Dinamik Baraj), %d Web (4-10), %d Cöp (1-3)", len(telegram_items), len(web_items), len(trash_items))

    # WEB (Google Sheets)
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
            
        # HAFTASONU PODCASTI (Eger Cumartesi veya Pazar ise)
        if now.weekday() >= 5:
            create_and_send_podcast(telegram_items)

    # Hafizayi Guncelle
    now_iso = now.isoformat()
    for item in unique_items:
        link = item.get("link", "")
        seen_urls[link] = now_iso
        semantic_memory.add_to_memory(link, item.get("title", ""), item.get("abstract", ""))
    save_seen_urls(seen_urls)
    logger.info("Islem tamamlandi. %d yeni URL hafizaya alindi.", len(unique_items))

if __name__ == "__main__":
    main()
