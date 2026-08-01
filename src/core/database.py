import json
from datetime import datetime, timezone
from pathlib import Path
from src.core.config import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
DATA_PATH = DOCS_DIR / "data.json"

class WebDatabase:
    def __init__(self):
        DOCS_DIR.mkdir(exist_ok=True)
        self.items = self._load()
        self._cleanup_old_items()

    def _load(self):
        if DATA_PATH.exists():
            try:
                with open(DATA_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Web database okuma hatasi: {e}")
        return []

    def _save(self):
        try:
            with open(DATA_PATH, "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Web database kayit hatasi: {e}")

    def _cleanup_old_items(self):
        # 30 gunu gecenleri sil
        now = datetime.now(timezone.utc)
        valid_items = []
        for item in self.items:
            added_at = item.get("added_at")
            if added_at:
                try:
                    dt = datetime.fromisoformat(added_at)
                    if (now - dt).days <= 30:
                        valid_items.append(item)
                except Exception:
                    valid_items.append(item)
            else:
                valid_items.append(item)
                
        if len(valid_items) < len(self.items):
            self.items = valid_items
            self._save()

    def add_items(self, new_items):
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # Sadece eksik alanlari tamamla
        for item in new_items:
            item["added_at"] = now_iso
            
        self.items = new_items + self.items
        
        # Son 30 gun temizligi tekrar kontrol
        self._cleanup_old_items()
        self._save()

web_database = WebDatabase()
