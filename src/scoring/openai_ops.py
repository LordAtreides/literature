import os
import json
import re
import requests
from time import sleep
from pathlib import Path

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

from src.core.config import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
IMAGES_DIR = PROJECT_ROOT / "docs" / "images"

def translate_articles_to_turkish(items):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not HAS_OPENAI or not api_key:
        logger.warning("OPENAI_API_KEY bulunamadi, ceviri yapilmayacak.")
        return items

    if not items:
        return items

    client = OpenAI(api_key=api_key)
    logger.info("OpenAI ile web makaleleri Türkçeye çevriliyor...")
    
    batch_size = 10
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        
        input_data = []
        for j, item in enumerate(batch):
            input_data.append({
                "id": str(j),
                "title": item.get("title", ""),
                "abstract": item.get("abstract", "")[:500]
            })
            
        system_prompt = (
            "Sen uzman bir akademik jeoloji çevirmenisin. Verilen JSON formatındaki makale listesindeki her 'title' ve 'abstract' değerini mükemmel, akıcı ve akademik bir Türkçe'ye çevir. "
            "SADECE JSON dönmelisin. Şema: {\"results\": [{\"id\": \"string\", \"title\": \"string\", \"abstract\": \"string\"}]} "
            "Markdown formatını kullanma, sadece JSON metnini döndür."
        )
        
        user_message = f"Makaleler:\n{json.dumps(input_data, ensure_ascii=False)}"
        
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message}
                    ],
                    max_tokens=2000,
                    temperature=0.3
                )
                
                result_text = response.choices[0].message.content.strip()
                json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
                if json_match:
                    result_text = json_match.group(0)
                    
                parsed = json.loads(result_text)
                for res in parsed.get("results", []):
                    try:
                        idx = int(res["id"])
                        if idx < len(batch):
                            if res.get("title"):
                                batch[idx]["title"] = res["title"]
                            if res.get("abstract"):
                                batch[idx]["abstract"] = res["abstract"]
                    except Exception: pass
                break
            except Exception as e:
                logger.error(f"OpenAI Ceviri Hatasi [{attempt+1}/3]: {e}")
                sleep(3)
                
    return items

def generate_cover_image(item):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not HAS_OPENAI or not api_key:
        return item
        
    client = OpenAI(api_key=api_key)
    logger.info(f"DALL-E 3 ile '{item.get('title')[:30]}...' icin kapak fotografi uretiliyor...")
    
    prompt = (
        f"A breathtaking, hyper-realistic scientific illustration for an academic geology article. "
        f"Title: {item.get('title')}. "
        f"Style: Premium digital art, dark futuristic aesthetic, glowing subtle elements, highly detailed, suitable for a beautiful website hero banner. "
        f"NO TEXT OR WORDS in the image."
    )
    
    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt[:1000],
            size="1024x1024",
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
        
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        import uuid
        filename = f"cover_{uuid.uuid4().hex[:8]}.jpg"
        filepath = IMAGES_DIR / filename
        
        img_data = requests.get(image_url).content
        with open(filepath, 'wb') as handler:
            handler.write(img_data)
            
        item["image_url"] = f"images/{filename}"
        logger.info(f"Kapak fotografi kaydedildi: {filename}")
        
    except Exception as e:
        logger.error(f"DALL-E 3 Gorsel Uretim Hatasi: {e}")
        
    return item
