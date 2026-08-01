import re
from html import escape as html_escape_builtin
from thefuzz import fuzz
from .config import logger
from .memory import semantic_memory

def safe_html(text):
    if not text: return ""
    return html_escape_builtin(str(text))

def strip_html_tags(text):
    if not text: return ""
    return re.sub(r"<[^>]+>", "", str(text)).strip()

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
                
        # 3. Vektorel (Semantic) Memory Kontrolu (Sadece baslik gecenleri detayli incele)
        if not is_duplicate:
            abstract = item.get("abstract", "")
            text_for_memory = f"{title}. {abstract}"
            is_sem_dup, matched_url = semantic_memory.is_duplicate(text_for_memory, threshold=0.85)
            if is_sem_dup:
                logger.info(f"Semantik Kopya Yakalandi: {title} (Eslesen: {matched_url})")
                is_duplicate = True
                
        if not is_duplicate:
            seen_links.add(link)
            accepted_titles.append(title)
            unique.append(item)
            
    logger.info("Deduplication: %d ham -> %d tekil", len(items), len(unique))
    return unique
