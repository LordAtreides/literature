import requests
import json
import re
from time import sleep
from tenacity import retry, wait_exponential, stop_after_attempt
from src.core.config import logger, PERPLEXITY_API_KEY

@retry(wait=wait_exponential(multiplier=1, min=4, max=10), stop=stop_after_attempt(3))
def _fetch_perplexity(payload):
    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json"
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    if resp.status_code != 200:
        logger.error(f"Perplexity API HTTP Error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    return resp.json()

def collect_from_perplexity(config):
    """Ozel kaynaklari Perplexity Sonar modeli kullanarak tarar."""
    entries = []
    if not PERPLEXITY_API_KEY:
        logger.warning("PERPLEXITY_API_KEY eksik, ozel kaynak aramasi (Sonar) atlandi.")
        return entries
        
    kaynaklar = config.get("ozel_kaynaklar", [])
    if not kaynaklar: return entries
        
    academic_query = "Find the top 5 most recent and important articles about Quaternary geology, remote sensing, sedimentology, or Mars geology from the provided domains."
    opportunity_query = "Find the top 5 most recent 2025/2026 PhD scholarships, grants, or geoscience internships from the provided domains."
    haber_query = "Find the top 5 most recent news about geology discoveries, earthquakes, volcanos, or Mars exploration from the provided domains."
    
    system_prompt = (
        "You are an expert research assistant. "
        "Search the provided domains and return exactly 5 recent and relevant results. "
        "You MUST format your entire response as a single, valid JSON object and nothing else. "
        "Schema: {\"results\": [{\"title\": \"...\", \"link\": \"...\", \"abstract\": \"...\"}]} "
        "Do not include markdown tags like ```json. Just output the raw JSON."
    )
    
    for group in kaynaklar:
        search_type = group.get("search_type", "academic")
        if search_type == "opportunity":
            query = opportunity_query
            category = "firsat"
        elif search_type == "haber":
            query = haber_query
            category = "haber"
        else:
            query = academic_query
            category = "makale"
        
        urls = [res.get("url") for res in group.get("resources", []) if res.get("url")]
        
        # Perplexity limits search_domain_filter to 20 domains. We batch them in groups of 10.
        for i in range(0, len(urls), 10):
            batch_urls = urls[i:i+10]
            
            # Clean domains (remove http/https and trailing slash)
            clean_domains = [u.replace("https://", "").replace("http://", "").rstrip('/') for u in batch_urls]
            
            payload = {
                "model": "sonar-small-online",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ],
                "search_domain_filter": clean_domains,
                "max_tokens": 1024,
                "temperature": 0.1
            }
            try:
                data = _fetch_perplexity(payload)
                content = data["choices"][0]["message"]["content"]
                
                # Try to parse JSON from the response
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    content = json_match.group(0)
                    
                parsed = json.loads(content)
                for item in parsed.get("results", []):
                    title = item.get("title", "")
                    link = item.get("link", "")
                    if title and link:
                        entries.append({
                            "title": title,
                            "link": link,
                            "abstract": item.get("abstract", ""),
                            "source": "Perplexity Sonar",
                            "category": category
                        })
            except Exception as e:
                logger.error(f"Perplexity error for domains {clean_domains}: {e}")
            sleep(2)
                
    logger.info("Perplexity Search: %d icerik toplandi", len(entries))
    return entries
