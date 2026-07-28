# 🌍 Jeoloji Takip Botu

Güncel jeoloji sempozyumlarını, makalelerini, haberlerini ve burslarını takip eden, yapay zeka ile özetleyip Telegram'dan bildiren tam otonom sistem.

## Mimari

```
RSS Kaynakları → main.py → Gemini AI (Filtre + Özet) → Telegram Bildirim
                    ↑
        GitHub Actions (08:00 & 20:00 TR)
```

## Kurulum

### 1. GitHub Repo Oluştur
Bu klasörü bir GitHub repository'sine push'la.

### 2. GitHub Secrets Tanımla
Repository → Settings → Secrets and variables → Actions → **New repository secret**:

| Secret Adı          | Açıklama                                      |
|----------------------|------------------------------------------------|
| `GEMINI_API_KEY`     | [Google AI Studio](https://aistudio.google.com/apikey) API anahtarı |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) ile oluşturduğun bot token    |
| `TELEGRAM_CHAT_ID`   | Kendi Telegram chat ID'n (aşağıda nasıl bulacağın anlatılıyor)    |

### 3. Telegram Bot Oluşturma

1. Telegram'da [@BotFather](https://t.me/BotFather)'a git
2. `/newbot` yaz, bot adını ve kullanıcı adını belirle
3. Sana verilen **token**'ı `TELEGRAM_BOT_TOKEN` olarak kaydet
4. Oluşturduğun bota git ve `/start` yaz

### 4. Chat ID Bulma

1. Botuna bir mesaj gönder
2. Tarayıcıda şu adrese git:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. JSON yanıtında `"chat":{"id": 123456789}` kısmındaki sayıyı `TELEGRAM_CHAT_ID` olarak kaydet

### 5. Otomatik Çalışma
Push yaptıktan sonra GitHub Actions otomatik olarak:
- ☀️ Her sabah **08:00** (TR saati)
- 🌙 Her akşam **20:00** (TR saati)

çalışacak. İlk testi elle yapmak için Actions sekmesinden **Run workflow** butonuna tıkla.

## Yerel Test

```bash
export GEMINI_API_KEY="api-anahtarın"
export TELEGRAM_BOT_TOKEN="bot-tokenın"
export TELEGRAM_CHAT_ID="chat-id"

pip install -r requirements.txt
python main.py
```

## Takip Edilen Konular

- 🪨 Jeoloji haberleri
- 🛰️ Uzaktan algılama / Remote Sensing
- 🧊 Kuvaterner araştırmaları
- 🗺️ Coğrafi Bilgi Sistemleri (CBS/GIS)
- 🎓 TÜBİTAK proje ve burs duyuruları
- 📢 Jeoloji sempozyumları
