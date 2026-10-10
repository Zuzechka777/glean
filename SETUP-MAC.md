# Установка reels-inbox на Mac

## 1. Разархивируй проект

Перешли `reels-inbox.zip` себе (Telegram / AirDrop / диск) и распакуй:

```bash
unzip reels-inbox.zip -d ~/Downloads/
cd ~/Projects/glean
```

## 2. Запусти setup.sh

```bash
bash setup.sh
```

Скрипт установит Homebrew (если нет), ffmpeg, uv, Python 3.12, зависимости, прогонит тесты и проверит CLI. Занимает ~3–5 минут при первом запуске.

---

## 3. Заполни API-ключи в .env

Открой `.env` (создан скриптом из `.env.example`) и заполни:

### 3.1 GEMINI_API_KEY (обязательно)

1. Зайди на https://aistudio.google.com/ (нужен Google-аккаунт)
2. "Get API key" → "Create API key"
3. Скопируй ключ → `GEMINI_API_KEY=AIza...`

> Нужен VPN? Добавь прокси ниже.

### 3.2 GROQ_API_KEY (опционально, улучшает русский транскрипт)

1. https://console.groq.com/keys
2. Создай ключ → `GROQ_API_KEY=gsk_...`
3. Включи в config.yaml: `analyzer: whisper-gemini`

### 3.3 APIFY_TOKEN (рекомендуется как фолбэк-загрузчик)

1. https://console.apify.com/account/integrations
2. "Create new token" → `APIFY_TOKEN=apify_api_...`
3. **До использования бэклога**: запусти тест на 5 рилсах и сверь реальную цену:
   ```bash
   uv run reels-inbox batch test_links.txt --limit 5
   uv run reels-inbox status   # посмотри total_cost_usd
   ```

### 3.4 Прокси (если Instagram или Google API не доступны без VPN)

Примеры:
```
REELS_PROXY=socks5://127.0.0.1:1080   # локальный SOCKS5 (Tunnelblick, Outline и т.п.)
REELS_PROXY=http://user:pass@host:3128
```

---

## 4. Настрой burner-аккаунт Instagram

yt-dlp качает через cookies залогиненного аккаунта. Используй **отдельный аккаунт** (не основной), чтобы не рисковать баном.

1. Создай новый IG-аккаунт (или возьми запасной).
2. Залогинься в нём в **отдельном профиле Chrome**:
   - `chrome://settings/` → "Add person" → создай профиль «Instagram Burner»
   - Войди в IG в этом профиле.
3. В `config.yaml` укажи профиль:
   ```yaml
   cookies_from_browser: "chrome:Instagram Burner"
   ```
   Для Safari (macOS по умолчанию): `cookies_from_browser: safari` (нужно дать yt-dlp доступ к связке ключей).

> **Важно:** batch-прогон останавливается при первом auth-сбое, чтобы не «сжечь» cookies. Лимит безопасного скачивания через yt-dlp: ~50 рилсов/день с паузами ≥30 сек.

---

## 5. Создай vault и проверь

```bash
uv run reels-inbox init-vault
```

Создаёт `~/ReelsVault/` со структурой папок и dashboard-заметками.

Установи Obsidian: https://obsidian.md/download

В Obsidian:
1. "Open folder as vault" → выбери `~/ReelsVault`
2. Settings → Community plugins → отключи Safe mode
3. Найди и установи:
   - **Dataview** — для таблиц на дашбордах (00 Inbox, 10 By Type, 90 Problems)
   - **Map View** — для карты мест (20 Travel Map)

---

## 6. Тест: обработай один рилс

Найди публичный рилс (travel — лучше для первого теста):

```bash
uv run reels-inbox process "https://www.instagram.com/reel/XXXXXX/"
```

Ожидаемый вывод: `OK travel «...» → Reels/2026-06-11-....md`

Затем открой в Obsidian → Dashboards → 00 Inbox — должна появиться карточка.

Повторный запуск той же ссылки: `SKIP already_processed`.

---

## 7. Telegram-интейк

### Вариант А: через Зюзяна (рекомендую)

Дай Зюзяну/Claude Code такую инструкцию (skill/хук):

> Если в сообщении есть ссылка на Instagram Reel (instagram.com/reel/), выполни:
> `uv run --project ~/Projects/glean reels-inbox process-text "<текст>" --json`
> и перескажи результат: тип, заголовок, краткое summary.

### Вариант Б: свой fallback-бот

Создай бота у @BotFather в Telegram → получи токен.
Узнай свой числовой Telegram ID: напиши @userinfobot.
Заполни в `.env`:
```
TELEGRAM_BOT_TOKEN=1234567890:AAF...
TELEGRAM_USER_ID=123456789
```

Тест (вручную):
```bash
uv run reels-inbox bot
```

Отправь боту ссылку на рилс — он ответит карточкой.

Автозапуск при входе в систему (launchd):

```bash
# Замени YOUR_USERNAME своим именем пользователя macOS
sed -i '' "s/YOUR_USERNAME/$(whoami)/g" launchd/com.user.reels-inbox-bot.plist

cp launchd/com.user.reels-inbox-bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.reels-inbox-bot.plist
launchctl start com.user.reels-inbox-bot

# Проверить:
launchctl list | grep reels-inbox
tail -f data/logs/bot.log
```

---

## 8. Бэклог: обработать все сохранённые рилсы

1. В Instagram: Настройки → Аккаунт → Ваши данные в Instagram → **Скачать данные** (формат JSON, «За всё время»). Ссылка придёт на email через ~1 час.
2. Распакуй архив, найди файл `saved_posts.json` (обычно в `your_instagram_activity/saved/`).
3. Тестовый прогон 10 штук:
   ```bash
   uv run reels-inbox batch /path/to/saved_posts.json --limit 10
   uv run reels-inbox status
   ```
4. Если всё ок — полный прогон (занимает долго из-за пейсинга):
   ```bash
   uv run reels-inbox batch /path/to/saved_posts.json
   ```
5. Retry для неудавшихся:
   ```bash
   uv run reels-inbox retry --status failed-download
   ```

---

## Полезные команды

```bash
uv run reels-inbox status                          # сводка: статусы + расходы
uv run reels-inbox process "URL" --json            # JSON-вывод (для Зюзяна)
uv run reels-inbox process "URL" --force           # перезаписать уже обработанный
uv run reels-inbox batch links.txt --limit 20 -v   # verbose batch
uv run reels-inbox retry                           # retry всех failed-download
```

## Обновление yt-dlp (если Instagram перестал скачиваться)

```bash
uv run python -m pip install -U yt-dlp
# или nightly:
uv run python -m pip install -U "yt-dlp[default]@https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz"
```

## Переключить стратегию анализа

В `config.yaml`:
```yaml
analyzer: whisper-gemini   # точнее для русского; нужен GROQ_API_KEY
# analyzer: gemini-video   # один вызов Gemini, по умолчанию
```

## Диагностика

```bash
tail -f data/logs/reels-inbox.log    # основной лог (DEBUG)
tail -f data/logs/bot-error.log      # ошибки бота
```
