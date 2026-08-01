import requests
from time import sleep
from datetime import datetime, timezone, timedelta
from tenacity import retry, wait_exponential, stop_after_attempt
from src.core.config import logger
from src.core.utils import strip_html_tags

@retry(wait=wait_exponential(multiplier=1, min=4, max=10), stop=stop_after_attempt(3))
def _fetch_crossref(url, params, headers):
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()

def collect_from_crossref(config):
    entries = []
    headers = {"User-Agent": "JeolojiBot/3.0 (mailto:jeolojibot@example.com)"}
    kws = config.get("anahtar_kelimeler", {})
    limit = config.get("ayarlar", {}).get("crossref_sonuc_limiti", 2)
    all_keywords = kws.get("en_academic_and_news", []) + kws.get("de_german_research", []) + kws.get("fr_french_research", [])

    for kw in all_keywords:
        try:
            data = _fetch_crossref(
                "https://api.crossref.org/works",
                params={
                    "query": kw, "select": "title,URL,abstract,published",
                    "sort": "published", "order": "desc", "rows": limit
                },
                headers=headers
            )
            for paper in data.get("message", {}).get("items", []):
                link = paper.get("URL", "")
                title_list = paper.get("title", [])
                title = title_list[0] if title_list else ""
                if link and title:
                    entries.append({
                        "title": title, "link": link, "abstract": strip_html_tags(paper.get("abstract", "")),
                        "source": "Crossref", "category": "makale"
                    })
        except Exception as e:
            logger.error(f"Crossref error for {kw}: {e}")
        sleep(0.3)
    logger.info("Crossref: %d", len(entries))
    return entries
