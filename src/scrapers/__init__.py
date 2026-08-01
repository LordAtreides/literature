import concurrent.futures
from src.core.config import logger
from .crossref import collect_from_crossref
from .arxiv import collect_from_arxiv
from .eartharxiv import collect_from_eartharxiv
from .reddit import collect_from_reddit
from .perplexity import collect_from_perplexity
from .rss_scraper import collect_from_rss

def run_parallel_collection(config):
    """6 kaynagi ThreadPool ile ayni anda tarar."""
    all_items = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(collect_from_crossref, config),
            executor.submit(collect_from_arxiv, config),
            executor.submit(collect_from_eartharxiv, config),
            executor.submit(collect_from_reddit, config),
            executor.submit(collect_from_perplexity, config),
            executor.submit(collect_from_rss, config)
        ]
        
        for future in concurrent.futures.as_completed(futures):
            try:
                all_items.extend(future.result())
            except Exception as e:
                logger.error(f"Collector hatasi: {e}")
    return all_items
