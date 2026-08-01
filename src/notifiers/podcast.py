import os
from src.core.config import logger

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

from src.core.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

def generate_podcast_text(items):
    # Bu metin normalde Claude tarafindan yazdirilabilir, ancak basitce burada olusturuyoruz
    # (veya Claude ile 2 dakikalik script yazdirmak daha zeki olur)
    from src.scoring.claude import CLAUDE_API_KEY
    if not CLAUDE_API_KEY:
        return None
        
    import anthropic
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    
    text_items = "\n".join([f"- {i['title']}: {i.get('summary', i.get('abstract',''))[:200]}" for i in items[:5]])
    system_prompt = (
        "Sen 'Jeoloji Gündemi' adinda 1 dakikalik bir radyo programi sunucususun. "
        "Sana verilen 5 haberi sanki radyoda/podcast'te canli yayinda anlatirmis gibi, "
        "kisa, enerjik ve akici bir Turkce ile seslendirilecek sekilde metne dok. "
        "Sadece konusma metnini ver, baska aciklama yazma. (Ornek: Merhaba jeoloji tutkunlari...)"
    )
    
    try:
        response = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": text_items}]
        )
        return "".join([getattr(b, "text", "") for b in response.content]).strip()
    except Exception as e:
        logger.error(f"Podcast metni olusturulamadi: {e}")
        return None

def create_and_send_podcast(items):
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not HAS_OPENAI or not openai_key:
        logger.info("OPENAI_API_KEY eksik veya kutuphane yok, podcast uretilmeyecek.")
        return False
        
    if not items:
        return False
        
    logger.info("Podcast (Audio Digest) uretimi basladi...")
    script_text = generate_podcast_text(items)
    if not script_text:
        return False
        
    try:
        client = OpenAI(api_key=openai_key)
        response = client.audio.speech.create(
            model="tts-1",
            voice="onyx", # Erkek/Kalin ses, daha belgeselvari
            input=script_text
        )
        
        # Dosyayi kaydet
        file_path = "podcast_digest.mp3"
        response.stream_to_file(file_path)
        
        # Telegrama ses dosyasi olarak at
        import requests
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAudio"
        with open(file_path, "rb") as f:
            resp = requests.post(
                url, 
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": "🎙️ Haftanin Jeoloji Özeti"},
                files={"audio": f}
            )
        resp.raise_for_status()
        logger.info("Podcast basariyla gonderildi!")
        return True
    except Exception as e:
        logger.error(f"Podcast uretim/gonderim hatasi: {e}")
        return False
