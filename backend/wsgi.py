"""WSGI-вход для продакшн-сервера (gunicorn backend.wsgi:app)."""

from backend.app import create_app

app = create_app()
