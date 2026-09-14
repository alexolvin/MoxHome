"""Web UI и API (этап U1): FastAPI + серверный HTML + минимум JavaScript.

Доступ только внутри Tailscale (bind из конфигурации); при заданном
SERVER_AUTH_SECRET — общий секрет через HTTP Basic (пароль = секрет).
API и страницы читают те же сервисы, что и воркер (services/jobs,
services/languages) — дублирующей бизнес-логики нет (Rule 8).
"""
