#!/bin/bash
# Двойной клик → поднимает локальный веб-интерфейс Glean и открывает браузер.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"

PORT=8765
echo ""
echo "  🎬  glean"
echo "  ────────────────────────────────────────────────"

# Убиваем старый сервер на порту, если висит
OLD_PID=$(lsof -ti:$PORT 2>/dev/null)
if [ -n "$OLD_PID" ]; then
  echo "  🔄  Останавливаю старый сервер (PID $OLD_PID)..."
  kill -9 $OLD_PID 2>/dev/null
  sleep 1
fi

if ! command -v uv &>/dev/null; then
  echo "  ❌  uv не найден. Запусти сначала: bash setup.sh"
  read -p "  Нажми Enter..."; exit 1
fi

echo "  📦  Проверяю зависимости..."
uv sync --quiet

echo "  🚀  Сервер: http://127.0.0.1:$PORT     (Ctrl+C — остановить)"
echo "  ────────────────────────────────────────────────"
echo ""

(sleep 2 && open "http://127.0.0.1:$PORT") &
# --host 0.0.0.0 → доступ и с телефона по Wi-Fi (адрес покажется ниже)
uv run reels-inbox web --port $PORT --host 0.0.0.0
