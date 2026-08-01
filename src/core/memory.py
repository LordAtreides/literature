import json
import numpy as np
import requests
from time import sleep
from pathlib import Path
from src.core.config import logger, GEMINI_API_KEY

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MEMORY_PATH = PROJECT_ROOT / "semantic_memory.json"

class SemanticMemory:
    def __init__(self):
        self.memory = []
        self._load()
        
    def _load(self):
        if MEMORY_PATH.exists():
            try:
                with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                    self.memory = json.load(f)
            except Exception as e:
                logger.error(f"Semantic memory load error: {e}")
                self.memory = []

    def _save(self):
        try:
            with open(MEMORY_PATH, "w", encoding="utf-8") as f:
                json.dump(self.memory, f)
        except Exception as e:
            logger.error(f"Semantic memory save error: {e}")

    def get_embedding(self, text):
        if not GEMINI_API_KEY:
            return None
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={GEMINI_API_KEY}"
        payload = {
            "model": "models/text-embedding-004",
            "content": {"parts": [{"text": text[:2000]}]}
        }
        
        for attempt in range(3):
            try:
                resp = requests.post(url, json=payload, timeout=10)
                if resp.status_code == 200:
                    vals = resp.json().get("embedding", {}).get("values", [])
                    if vals:
                        # Normalize vector
                        vec = np.array(vals)
                        norm = np.linalg.norm(vec)
                        if norm > 0: vec = vec / norm
                        return vec.tolist()
                elif resp.status_code == 429:
                    sleep(2)
                else:
                    break
            except Exception:
                sleep(1)
        return None

    def is_duplicate(self, text, threshold=0.88):
        """Returns (is_duplicate, matched_url)"""
        if not self.memory: return False, None
        
        new_vec = self.get_embedding(text)
        if not new_vec: return False, None # API fails, fallback to passing
        
        new_arr = np.array(new_vec)
        
        # Simple dot product for cosine similarity since vectors are normalized
        for item in self.memory:
            past_vec = np.array(item["embedding"])
            similarity = np.dot(new_arr, past_vec)
            if similarity >= threshold:
                return True, item["link"]
                
        return False, None

    def add_to_memory(self, link, title, abstract):
        text = f"{title}. {abstract}"
        vec = self.get_embedding(text)
        if vec:
            self.memory.append({
                "link": link,
                "embedding": vec
            })
            # Keep memory bounded to last 2000 items to avoid giant files
            if len(self.memory) > 2000:
                self.memory = self.memory[-2000:]
            self._save()

semantic_memory = SemanticMemory()
