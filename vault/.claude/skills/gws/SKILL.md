---
name: gws
description: Google Workspace через gws CLI — календарь, Gmail, Google Диск. Триггеры — "календарь", "почта", "gmail", "встречи", "что у меня на сегодня в календаре", "проверь почту", "загрузи на диск", "напомни про встречу", "настрой google".
---

# gws: Google Workspace (календарь, почта, диск)

Доступ к Google-сервисам пользователя через CLI [googleworkspace/cli](https://github.com/googleworkspace/cli)
(команда `gws`). Вызывай его из Bash. В связке со скиллом `cron` это даёт рутины
вида «проверяй мой календарь каждое утро».

## Проверка, что gws готов

```bash
gws --version          # установлен ли
gws calendar +agenda   # если отвечает событиями — авторизация есть
```

Если `gws: command not found` — нужно установить (см. ниже). Если ругается на
авторизацию (`not authenticated`, `login required`) — нужен OAuth (см. ниже),
и это шаг ПОЛЬЗОВАТЕЛЯ (браузер + согласие Google).

## Установка (по запросу пользователя)

Сервер: Linux x86_64, есть node/npm. Варианты по надёжности:

```bash
# 1. Официальный бинарь с GitHub Releases (предпочтительно — без сборки)
#    Скачать архив для linux x86_64 со страницы релизов, распаковать, положить
#    в ~/.local/bin (он уже в PATH):
#    https://github.com/googleworkspace/cli/releases

# 2. npm (быстро, но это сторонний пакет — ставь только с явного согласия)
npm install -g @googleworkspace/cli

# 3. из исходников (если есть cargo)
cargo install --git https://github.com/googleworkspace/cli --locked
```

После установки проверь `gws --version`.

## Авторизация (OAuth — нужен пользователь)

```bash
gws auth setup    # создаёт GCP-проект, включает API, запускает вход
gws auth login    # повторный вход / расширение доступов
# при ограничениях testing-режима можно сузить доступы:
gws auth login -s calendar,gmail,drive
```

`gws auth setup` открывает браузер для согласия Google и просит добавить аккаунт
как test user в GCP Console. На headless-сервере он печатает URL — **перешли этот
URL пользователю в Telegram**, пользователь открывает его в своём браузере и
подтверждает. Креды шифруются и хранятся локально. Сам ты OAuth пройти не можешь —
твоя задача запустить команду, отдать пользователю ссылку и дождаться, пока он
подтвердит.

## Команды

```bash
# Календарь
gws calendar +agenda                                   # ближайшие события
gws calendar events list --params '{"maxResults": 10}'

# Gmail
gws gmail users messages list --params '{"userId": "me", "maxResults": 10}'
gws gmail +send --to alice@example.com --subject "Тема" --body "Текст"

# Диск
gws drive files list --params '{"pageSize": 10}'
gws drive +upload ./file.pdf --name "Имя файла"
```

Точные подкоманды смотри в `gws --help` и `gws <сервис> --help` — API может
меняться между версиями.

## Правила

1. **OAuth — всегда через пользователя.** Запусти команду, дай ссылку, жди
   подтверждения. Не пытайся вводить пароли/коды сам.
2. **Секреты не коммить.** Креды gws лежат локально (вне репо), токены и client
   secret в vault/git не записывай.
3. **Рутины — через скилл `cron`.** «Проверяй календарь в 8 утра» = cron-задание
   с prompt «Вызови `gws calendar +agenda`, если есть события на сегодня — пришли
   списком, иначе начни ответ с [SILENT]».
4. **Чтение по умолчанию, запись по подтверждению.** Список событий/писем — ок
   сразу. Отправку письма (`gws gmail +send`), загрузку на диск, создание событий
   — сначала покажи пользователю, что собираешься сделать, и дождись «да».
