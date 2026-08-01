import json
import re
from time import sleep
try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

from src.core.config import logger, CLAUDE_API_KEY
from src.core.utils import strip_html_tags

def find_working_claude_model(client):
    try:
        models = [m.id for m in client.models.list().data]
        sonnets = [m for m in models if "sonnet" in m]
        if sonnets:
            sonnets.sort(reverse=True)
            return sonnets[0]
        return models[0] if models else "claude-3-5-sonnet-20240620"
    except Exception:
        return "claude-3-5-sonnet-20240620"

def claude_batch_score(items, config):
    if not items or not CLAUDE_API_KEY:
        for i in items: i["score"] = 5
        return items

    profile_text = config.get("kullanici_profili", {}).get("aciklama", "Jeoloji")
    batch_size = 20
    scored = []
    
    if HAS_ANTHROPIC:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        model_name = "claude-3-haiku-20240307"
        logger.info("Claude Puanlama Modeli: %s", model_name)
    else:
        logger.error("Anthropic kütüphanesi yüklü değil!")
        for i in items: i["score"] = 5
        return items
    
    system_prompt = (
        f"Sen bir jeoloji akademisyenisin. Kullanıcı Profili: {profile_text}. "
        "Aşağıdaki makalelerin bu profile ne kadar uygun olduğunu 1-10 arası puanla. "
        "(9-10: Çok önemli, 7-8: İlgili, 4-6: Dolaylı, 1-3: Çöp). "
        "ONEMLI: Son tarihli (deadline geçmiş), yılı eski (2023 ve öncesi), veya süresi dolmuş BURS/STAJ FIRSAT DUYURULARINA KESİNLİKLE 1 puan ver. "
        "Ancak AKADEMİK MAKALELER için (makale, on_baski) 10 yıllık dahi olsalar, profilinle doğrudan ilgili ve faydalı bir temel araştırmaysa puan KIRMA, yüksek puan ver. "
        "SADECE JSON dönmelisin. Şema: {\"results\": [{\"id\": \"string\", \"score\": integer}]} "
        "Markdown veya başka metin ekleme."
    )

    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        input_data = [{"id": str(j), "title": item["title"], "abstract": strip_html_tags(item.get("abstract",""))[:150]} for j, item in enumerate(batch)]
        user_message = f"İçerikler:\n{json.dumps(input_data, ensure_ascii=False)}"
        
        for attempt in range(3):
            try:
                response = client.messages.create(
                    model=model_name,
                    max_tokens=1024,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}]
                )
                result_text = "".join([getattr(b, "text", "") for b in response.content]).strip()
                json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
                if json_match:
                    result_text = json_match.group(0)
                
                parsed = json.loads(result_text)
                for res in parsed.get("results", []):
                    try:
                        idx = int(res["id"])
                        if idx < len(batch):
                            batch[idx]["score"] = res["score"]
                    except Exception: pass
                break
            except Exception as e:
                logger.error(f"Claude Scoring Hatasi [{attempt+1}/3]: {e}")
                sleep(5)
                
        for item in batch:
            if "score" not in item:
                item["score"] = 5
        scored.extend(batch)
        sleep(2)
        
    logger.info("Claude Puanlama: %d makale süzüldü.", len(scored))
    return scored

def claude_deep_analysis(item):
    if not HAS_ANTHROPIC or not CLAUDE_API_KEY:
        return item.get("abstract", "")[:200] + "..."
        
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    
    system_prompt = """Bir akademik jeoloji metnini TÜRKÇE'ye çevirip özetleyeceksin.
KURALLAR:
- YANITIN KESİNLİKLE %100 TÜRKÇE OLMALIDIR. Başka bir dilde tek bir kelime dahi yazma.
- SADECE VE SADECE 2 SATIR yazacaksın.
- 1. Satır: (Bulgu): [Ana bulgu/keşif nedir?]
- 2. Satır: (Önem): [ÇOK KISA. Sadece 3-7 kelimelik hap bilgi. Uzatma.]
- "Özetle, sonucunda, incelenmiştir" gibi yapay zeka dili (AI cliches) kullanmak YASAK.
- Net, objektif ve doğrudan akademik bilgi ver."""

    user_prompt = f"Başlık: {item.get('title')}\nÖzet: {strip_html_tags(item.get('abstract'))[:1000]}"

    try:
        model_name = find_working_claude_model(client)
        message = client.messages.create(
            model=model_name,
            max_tokens=150,
            system=[
                {
                    "type": "text", 
                    "text": system_prompt, 
                    "cache_control": {"type": "ephemeral"}
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
        )
        return "".join([getattr(b, "text", "") for b in message.content]).strip()
    except Exception as e:
        logger.error(f"Claude API hatasi: {e}")
        return strip_html_tags(item.get("abstract", ""))[:200]
