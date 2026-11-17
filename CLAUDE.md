# Glean — Claude Code guide

Пайплайн: ссылка на рилс/шортс/карусель → скачивание (yt-dlp с cookies / web-API IG) → Gemini 2.5 Flash → типизированная карточка `.md` в `~/2brain/reels/`.

## Запуск и проверка

- Веб-UI: `uv run reels-inbox web --port 8765 --host 0.0.0.0` (боевой путь — Glean.app в Dock; запасной — START_HERE.command).
- Логи сервера: `data/logs/web.log` — **читай их вместо перезапуска сервера**.
- Тесты: `uv run pytest` (быстрые, гоняй после любых правок src/).
- CLI-смоук: `uv run reels-inbox status` (ledger) и `uv run reels-inbox process "<url>" --json`.

## Непугадываемые факты

- Пакет/CLI **намеренно** называются `reels_inbox`/`reels-inbox`, хотя проект — Glean; команда `process` зарегистрирована явно (`@app.command(name="process")` — не переименовывать функцию без сохранения имени команды).
- `/api/process` асинхронный: POST → `job_id`, фронт поллит `/api/job/{id}`. Не возвращать долгий синхронный ответ — браузеры/VPN рвут соединение.
- Цепочка IG-видео: `yt-dlp` с cookies → публичный `instagram-embed` без cookies → Apify. Если в `data/cookies.txt` нет `sessionid`, yt-dlp пропускается сразу; публичные Reels всё равно скачиваются через embed. Cookies нужны для приватного контента и каруселей.
- Выход `~/2brain/reels/` — зона vault: правила 2brain применяются (raw/ read-only).
- Проект под git (приватный origin `github.com/Zuzechka777/glean`, `.env` и `data/` в .gitignore); черновики и одноразовые скрипты — в `scratch/` (не в корень).
- **Gemini через прокси Happ:** маршрутизация Happ пускает `googleapis.com` напрямую с RU-IP → `400 location is not supported`. `_gemini_proxy()` в `stages/analyze.py` шлёт ТОЛЬКО Gemini через `127.0.0.1:10808` (автодетект; `GEMINI_PROXY` в .env переопределяет, `off` — отключить). Если опять «location»: проверь страну выхода `curl -x http://127.0.0.1:10808 ipinfo.io` — EE/RU Google не принимает, DE работает.
- **Квота free tier = 20 запросов/сутки** на gemini-2.5-flash. При 429 `PerDay`/`location` ретраи и caption-only фолбэк выключены (`is_hard_gemini_error`), чтобы не жечь квоту.
- **Здоровье и очередь (10.10):** `health.py` сам выбирает рабочий порт Happ (10808/10809/10820 → напрямую) пробой `GET /models` (квоту не тратит), кэш 10 мин; `/api/health` + плашка в UI. Если Gemini упёрся в квоту/страну — карточка НЕ пишется, рилс уходит в `data/retry_queue.json`, фоновый воркер раз в 15 мин догоняет очередь. Тест: `tests/test_deferred.py`.
