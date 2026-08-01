import requests
import feedparser
from time import sleep
from tenacity import retry, wait_exponential, stop_after_attempt
from src.core.config import logger

@retry(wait=wait_exponential(multiplier=1, min=4, max=10), stop=stop_after_attempt(3))
def _fetch_arxiv(url, params, headers):
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.text

def collect_from_arxiv(config):
    entries = []
    queries = config.get("anahtar_kelimeler", {}).get("en_academic_and_news", [])
    limit = config.get("ayarlar", {}).get("arxiv_sonuc_limiti", 5)

    for query in queries:
        try:
            url = "http://export.arxiv.org/api/query"
            params = {"search_query": f"cat:physics.geo-ph AND all:({query})", "sortBy": "submittedDate", "sortOrder": "descending", "max_results": limit}
            text = _fetch_arxiv(url, params=params, headers={"User-Agent": "JeolojiBot/3.0"})
            for entry in feedparser.parse(text).entries:
                link, title = entry.get("link", ""), entry.get("title", "").replace("\n", " ").strip()
                if link and title:
                    entries.append({
                        "title": title, "link": link, "abstract": entry.get("summary", "").replace("\n", " ").strip(),
                        "source": "arXiv", "category": "on_baski"
                    })
        except Exception as e:
            logger.error(f"arXiv error for {query}: {e}")
        sleep(1)
    logger.info("arXiv: %d", len(entries))
    return entries
