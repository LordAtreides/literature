import requests
import feedparser
from time import sleep
from tenacity import retry, wait_exponential, stop_after_attempt
from src.core.config import logger
from src.core.utils import strip_html_tags

@retry(wait=wait_exponential(multiplier=1, min=4, max=10), stop=stop_after_attempt(3))
def _fetch_rss(url, headers):
    resp = requests.get(url, timeout=10, headers=headers)
    resp.raise_for_status()
    return resp.text

def collect_from_rss(config):
    entries = []
    rss_feeds = config.get("rss_feeds", [])
    
    for feed in rss_feeds:
        try:
            url = feed.get("url")
            source_name = feed.get("source", "RSS")
            category = feed.get("category", "makale")
            
            text = _fetch_rss(url, headers={"User-Agent": "JeolojiBot/4.0 (educational)"})
            parsed = feedparser.parse(text)
            
            for entry in parsed.entries[:10]: # Her kaynaktan en yeni 10
                link = entry.get("link", "")
                title = entry.get("title", "")
                if link and title:
                    entries.append({
                        "title": strip_html_tags(title),
                        "link": link,
                        "abstract": strip_html_tags(entry.get("summary", ""))[:300],
                        "source": source_name,
                        "category": category
                    })
        except Exception as e:
            logger.error(f"RSS error for {url}: {e}")
        sleep(1)
        
    logger.info("RSS Direct: %d", len(entries))
    return entries
