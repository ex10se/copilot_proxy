# Copilot Proxy

OpenAI-compatible reverse proxy. Чистит невалидные tool_calls, удаляет лишние поля, подменяет system prompt.

Проксирует запросы на `OPENAI_BASE_URL` (берется из окружения shell).

## Запуск

Из любой директории:

```bash
docker compose -f ~/Documents/copilot_proxy/docker-compose.yml up -d
```

Или через alias (добавить в `~/.zshrc`):

```bash
alias copilot_proxy='docker compose -f ~/Documents/copilot_proxy/docker-compose.yml up -d'
```

Логи:

```bash
docker compose -f ~/Documents/copilot_proxy/docker-compose.yml logs -f
```

Остановка:

```bash
docker compose -f ~/Documents/copilot_proxy/docker-compose.yml down
```

## Порт

Фиксированный: **8779** (константа в `settings.py`).

## Переменные окружения

| Переменная | Описание | По умолчанию |
|---|---|---|
| `OPENAI_BASE_URL` | URL целевого API | обязательная |
| `DROP_FIELDS` | Поля для удаления из body (через запятую) | `stream_options,parallel_tool_calls,service_tier` |
| `DEBUG` | Режим отладки | `true` |
