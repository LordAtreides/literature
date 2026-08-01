import requests
import feedparser
from time import sleep
from tenacity import retry, wait_exponential, stop_after_attempt
from src.core.config import logger
from src.core.utils import strip_html_tags

@retry(wait=wait_exponential(multiplier=1, min=4, max=10), stop=stop_after_attempt(3))
def _fetch_reddit(url, headers):
    resp = requests.get(url, timeout=10, headers=headers)
    resp.raise_for_status()
    return resp.text

def collect_from_reddit(config):
    entries = []
    for sub in config.get("reddit_subreddits", []):
        try:
            text = _fetch_reddit(f"https://www.reddit.com/r/{sub}/new/.rss", headers={"User-Agent": "JeolojiBot/3.0 (educational)"})
            for entry in feedparser.parse(text).entries[:10]:
                link, title = entry.get("link", ""), entry.get("title", "")
                if link and title:
                    entries.append({
                        "title": strip_html_tags(title), "link": link,
                        "abstract": strip_html_tags(entry.get("summary", ""))[:300],
                        "source": f"Reddit (r/{sub})", "category": "forum"
                    })
        except Exception as e:
            logger.error(f"Reddit error for {sub}: {e}")
        sleep(1)
    logger.info("Reddit: %d", len(entries))
    return entries
