# Миграция на ASB v3.0 — что поменять в вашем боте

> **Срочно (к 15.06.2026):** headless-запуски `claude -p` / `claude --print`
> теперь оплачиваются отдельным платным Agent SDK-кредитом, а **не подпиской**.
> Эта версия убирает все такие вызовы — модель работает через **одну постоянную
> интерактивную сессию Claude Code в tmux** (остаётся на подписке, ноль токенов
> по API). Плюс: память с забыванием, рутины/напоминания, ежедневный self-check,
> подключение Google (gws).

Если у вас уже крутится более ранний бот — вот что нужно сделать, чтобы перейти.

## 0. Архитектура изменилась

Раньше память и нутрициолог жили в одном боте. Теперь это **два отдельных
агента**: этот репозиторий — только **память / второй мозг**. Нутрициолог (еда,
КБЖУ, Oura) вынесен в отдельный проект `d-doctor`. Если он вам нужен — ставится
отдельно; здесь его нет.

## 1. Предусловия

```bash
which tmux || sudo apt-get install -y tmux   # tmux обязателен (3.x)
claude --version                              # claude в PATH и залогинен под вашим аккаунтом
```

## 2. Обновить код

```bash
cd <ваш проект>
git pull            # или склонируйте заново
uv sync
bash scripts/check-no-claude-p.sh   # → ✅ No claude -p invocation in the hot path
```

## 3. `.env` — добавить

```ini
# Модель постоянной сессии (пусто = дефолт Claude Code). Рекомендуется Opus.
CLAUDE_MODEL=claude-opus-4-8
TZ=Europe/Moscow
```

## 4. `~/.claude.json` — снять онбординг (иначе сессия зависнет)

Интерактивный claude при первом запуске показывает диалоги (тема, предупреждение
Bypass Permissions), на которых автоматическая сессия зависает. Один раз
проставьте флаги под тем же пользователем, под которым крутится бот:

```bash
python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.claude.json")
d = json.load(open(p))
d["theme"] = "dark"
d["hasCompletedOnboarding"] = True
d["bypassPermissionsModeAccepted"] = True   # сессия стартует с --dangerously-skip-permissions
json.dump(d, open(p, "w"), indent=2)
print("ok; oauthAccount present:", "oauthAccount" in d)
PY
```

Это тот же уровень доступа, что был у старых headless-вызовов — просто теперь его
надо принять один раз для интерактивной сессии.

## 5. Поднять сессию и обновить systemd

```bash
# проверка ядра — поднять сессию вручную
uv run python -c "from d_brain.config import get_settings; from d_brain.services.runtime import get_session; get_session(get_settings()).ensure_session(); print('session ready')"
tmux ls   # должна появиться сессия dbrain_xxxx

# юниты (пути в deploy/*.service — ШАБЛОННЫЕ, поправьте под свой сервер/пользователя)
sudo cp deploy/d-brain-bot.service /etc/systemd/system/        # KillMode=process — переживает рестарт бота
sudo cp deploy/d-brain-watchdog.service /etc/systemd/system/   # держит сессию тёплой, поднимает упавшую
sudo systemctl daemon-reload
sudo systemctl restart d-brain-bot
sudo systemctl enable --now d-brain-watchdog
```

Подробный чеклист — в `deploy/PHASE1-DEPLOY.md` (тоже шаблон, адаптируйте пути).

## 6. Проверка

- Бот в Telegram: `/do привет` → `⏳ Выполняю…` затем ответ. Стриминга больше нет —
  это ожидаемо (цена подписочного режима).
- Рестарт бота → `tmux ls` показывает ТУ ЖЕ сессию (мозг переживает рестарт).
- Ночной `process.sh` → отчёт в Telegram, `ps aux | grep '[c]laude --print'` пусто.
- Гард `bash scripts/check-no-claude-p.sh` → ✅.

## Что нового в v3.0

- **Память:** забывание по Эббингаузу (чаще читаешь — медленнее забывается) +
  доменные скорости + ночная чистка фантомных `[[ссылок]]`.
- **Рутины/напоминания:** скажите боту «напомни завтра в 10…» или «каждое утро
  собирай сводку» — он сам заведёт cron-задание (скилл `vault/.claude/skills/cron`).
- **Doctor:** ежедневный 🟢/🔴 self-check (живость сессии, диск, ключи).
- **gws:** скажите «настрой gws» — агент подключит Google Календарь/Почту/Диск
  (скилл `vault/.claude/skills/gws`).

## Известные нюансы

- Первый `ask()` после холодного старта может занять до ~1.5 мин — watchdog держит
  сессию тёплой.
- Длинные ответы модели и JSON-фазы устойчивы к переносу строк терминала (capture
  с `-J` + чистка control-символов) — без этого ночные отчёты иногда не доходили.
