import requests
from time import sleep
from tenacity import retry, wait_exponential, stop_after_attempt
from src.core.config import logger, TAVILY_API_KEY

@retry(wait=wait_exponential(multiplier=1, min=4, max=10), stop=stop_after_attempt(3))
def _fetch_tavily(url, payload):
    resp = requests.post(url, json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json()

def collect_from_tavily(config):
    """Ozel kaynaklari Tavily Search API kullanarak tarar."""
    entries = []
    if not TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY eksik, ozel kaynak aramasi atlandi.")
        return entries
        
    kaynaklar = config.get("ozel_kaynaklar", [])
    if not kaynaklar: return entries
        
    academic_kws = "Quaternary geology OR remote sensing OR sedimentology OR Mars geology"
    opportunity_kws = "2025 2026 PhD scholarship OR grant OR internship geosciences geology"
    haber_kws = "geology discovery OR earthquake OR volcano OR Mars OR satellite OR geoscience news"
    
    url = "https://api.tavily.com/search"
    
    for group in kaynaklar:
        search_type = group.get("search_type", "academic")
        if search_type == "opportunity":
            kws = opportunity_kws
            category = "firsat"
        elif search_type == "haber":
            kws = haber_kws
            category = "haber"
        else:
            kws = academic_kws
            category = "makale"
        
        urls = [res.get("url") for res in group.get("resources", []) if res.get("url")]
        
        for i in range(0, len(urls), 5):
            batch_urls = urls[i:i+5]
            payload = {
                "api_key": TAVILY_API_KEY,
                "query": kws,
                "include_domains": batch_urls,
                "max_results": 5,
                "search_depth": "basic",
                "days": 14
            }
            try:
                data = _fetch_tavily(url, payload)
                for item in data.get("results", []):
                    entries.append({
                        "title": item.get("title", ""),
                        "link": item.get("url", ""),
                        "abstract": item.get("content", ""),
                        "source": "Tavily Custom",
                        "category": category
                    })
            except Exception as e:
                logger.error(f"Tavily error for {batch_urls}: {e}")
            sleep(1.5)
                
    logger.info("Tavily Search: %d icerik toplandi", len(entries))
    return entries
