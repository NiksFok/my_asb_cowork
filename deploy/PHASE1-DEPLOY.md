# Phase 1 — деплой и тест на сервере (agentic)

Биллинг-миграция: все обращения к модели теперь идут через одну постоянную
интерактивную сессию Claude Code в tmux (подписка), а не headless `claude -p`.

Ветка: `feat/v3-billing-session`. Сервер: `niks@38.180.145.92`, проект
`/home/niks/my_asb`. **Перед деплоем сделай бэкап** (`backup_server.ps1`).

## 0. Предусловия (один раз)
```bash
ssh agentic
which tmux || sudo apt-get install -y tmux      # tmux обязателен
which claude && claude --version                 # claude должен быть в PATH
tmux -V                                           # 3.x
```

## 1. Выкатка кода
```bash
cd /home/niks/my_asb
git fetch origin
git checkout feat/v3-billing-session   # или смёржить в main и pull
uv sync                                 # зависимости не менялись, но на всякий
bash scripts/check-no-claude-p.sh       # → ✅ No claude -p invocation
```

## 2. Поднять сессию вручную (проверка ядра)
```bash
cd /home/niks/my_asb
uv run python -c "from d_brain.config import get_settings; from d_brain.services.runtime import get_session; get_session(get_settings()).ensure_session(); print('session ready')"
tmux ls                                  # должна появиться сессия dbrain_xxxx
tmux attach -t dbrain_*                   # увидишь idle-промпт Claude Code; Ctrl-b d чтобы выйти
```
Если ругается на персону — проверь, что есть `deploy/brain-system.md` с первой
строкой `# d-brain session contract`.

## 3. Юниты systemd
```bash
sudo cp deploy/d-brain-bot.service /etc/systemd/system/        # обновлён (KillMode=process)
sudo cp deploy/d-brain-watchdog.service /etc/systemd/system/   # новый
sudo systemctl daemon-reload
sudo systemctl restart d-brain-bot
sudo systemctl enable --now d-brain-watchdog
sudo systemctl status d-brain-bot d-brain-watchdog --no-pager
```

## 4. Тест бота (чат)
- В Telegram напиши боту любой текст → должно прийти `⏳ Выполняю…`, затем ответ.
- Нажми «✨ Запрос» → отправь голос/текст → ответ одним сообщением (стриминга
  больше нет — это ожидаемо).
- Перезапусти бота: `sudo systemctl restart d-brain-bot` → `tmux ls` показывает
  ТУ ЖЕ сессию (мозг пережил рестарт).

## 5. Тест ночного pipeline
```bash
cd /home/niks/my_asb
TODAY=$(date +%F) bash scripts/process.sh 2>&1 | tail -40
# Отчёт должен прийти в Telegram; во время прогона:
ps aux | grep '[c]laude --print'         # ПУСТО (никаких headless вызовов)
```
Проверь `vault/.session/capture.json`, `execute.json` (валидный JSON),
`vault/daily/$(date +%F).md` обработан.

## 6. Утро и watchdog
```bash
bash scripts/morning.sh 2>&1 | tail -20   # брифинг в Telegram
# Watchdog: убей сессию, должна подняться + прийдёт алерт админу
tmux kill-session -t dbrain_*
sleep 30; tmux ls                          # сессия пересоздана
```

## 7. Финал
```bash
bash scripts/check-no-claude-p.sh          # → ✅
journalctl -u d-brain-bot -n 50 --no-pager # без ошибок
```

## Откат
```bash
git checkout main
sudo cp deploy/d-brain-bot.service /etc/systemd/system/   # из main
sudo systemctl disable --now d-brain-watchdog
sudo systemctl daemon-reload && sudo systemctl restart d-brain-bot
tmux kill-server   # убрать сессии v3
```

## Заметки
- `CLAUDE_MODEL=claude-opus-4-8` в `.env` (можно сменить на sonnet/haiku одной строкой).
- Первый `ask()` после холодного старта ~90 c — watchdog держит сессию тёплой.
- `mcp-config.json` (если есть на сервере) подхватится автоматически; Todoist в
  EXECUTE-фазе идёт через mcp-cli (Bash), как и раньше.
- d-doctor Oura в `process.sh` помечен `ALLOW-CLAUDE-P` (gated, default off) —
  мигрируем в Фазе 6.
