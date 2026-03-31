# Copilot Proxy

OpenAI-compatible reverse proxy. Чистит невалидные tool_calls, удаляет лишние поля, подменяет system prompt.

Проксирует запросы на `OPENAI_BASE_URL` (берется из окружения shell).

## Запуск

Порт умолчанию: **8779**, если не указывать. Можно переопределить:
```bash
PORT=8779 docker compose up -d
```

Логи:

```bash
docker compose logs -f
```

Остановка:

```bash
docker compose down
```

## Настройка клиента

В своем OpenAI-совместимом клиенте переопределите `OPENAI_BASE_URL` на адрес прокси:

```
OPENAI_BASE_URL=http://localhost:8779
```

## Переменные окружения

| Переменная        | Описание                                                | По умолчанию |
|-------------------|---------------------------------------------------------|--------------|
| `OPENAI_BASE_URL` | URL целевого API (например `https://api.openai.com/v1`) | обязательная |
| `PORT`            | Внешний порт                                            | `8779`       |
