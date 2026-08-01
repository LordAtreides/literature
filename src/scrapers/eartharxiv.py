import requests
from tenacity import retry, wait_exponential, stop_after_attempt
from src.core.config import logger
from src.core.utils import strip_html_tags

@retry(wait=wait_exponential(multiplier=1, min=4, max=10), stop=stop_after_attempt(3))
def _fetch_eartharxiv(url, params, headers):
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()

def collect_from_eartharxiv(config):
    entries = []
    limit = config.get("ayarlar", {}).get("eartharxiv_sonuc_limiti", 5)
    try:
        data = _fetch_eartharxiv(
            "https://api.osf.io/v2/preprints/",
            params={"filter[provider]": "eartharxiv", "sort": "-date_created", "page[size]": limit},
            headers={"User-Agent": "JeolojiBot/3.0"}
        )
        for item in data.get("data", []):
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
    except Exception as e:
        logger.error(f"EarthArXiv error: {e}")
    logger.info("EarthArXiv: %d", len(entries))
    return entries
