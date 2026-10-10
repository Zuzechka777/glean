#!/usr/bin/env bash
# Развёртывание reels-inbox на macOS.
# Запускать из корня проекта: bash setup.sh
set -euo pipefail

BOLD="\033[1m"; GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; RESET="\033[0m"

info()    { echo -e "${BOLD}▶ $*${RESET}"; }
success() { echo -e "${GREEN}✓ $*${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $*${RESET}"; }
die()     { echo -e "${RED}✗ $*${RESET}"; exit 1; }

# ── 1. Homebrew ───────────────────────────────────────────────────────────────
info "Проверяю Homebrew..."
if ! command -v brew &>/dev/null; then
    warn "Homebrew не найден — устанавливаю..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # добавить в PATH для Apple Silicon
    [[ -f /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
fi
success "Homebrew: $(brew --version | head -1)"

# ── 2. ffmpeg ─────────────────────────────────────────────────────────────────
info "Проверяю ffmpeg..."
if ! command -v ffmpeg &>/dev/null; then
    info "Устанавливаю ffmpeg..."
    brew install ffmpeg
fi
success "ffmpeg: $(ffmpeg -version 2>&1 | head -1)"

# ── 3. uv ─────────────────────────────────────────────────────────────────────
info "Проверяю uv..."
if ! command -v uv &>/dev/null; then
    info "Устанавливаю uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
success "uv: $(uv --version)"

# ── 4. Python 3.12 + зависимости ─────────────────────────────────────────────
info "Устанавливаю Python 3.12 и зависимости проекта..."
uv python install 3.12 --quiet
uv sync --quiet
success "Зависимости установлены"

# ── 5. .env ───────────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    cp .env.example .env
    warn ".env создан из .env.example — заполни ключи API (см. SETUP-MAC.md раздел 3)."
else
    success ".env уже существует"
fi

# ── 6. Тесты ─────────────────────────────────────────────────────────────────
info "Запускаю юнит-тесты (без сети)..."
if uv run pytest -q; then
    success "Все тесты прошли"
else
    die "Тесты упали — проверь вывод выше"
fi

# ── 7. Smoke-check CLI ────────────────────────────────────────────────────────
info "Smoke-check CLI..."
uv run reels-inbox status
success "CLI работает"

echo ""
echo -e "${BOLD}${GREEN}Готово!${RESET}"
echo -e "Следующий шаг → ${BOLD}SETUP-MAC.md раздел 3${RESET}: заполни API-ключи в .env"
echo -e "Затем запусти: ${BOLD}uv run reels-inbox init-vault${RESET}"
