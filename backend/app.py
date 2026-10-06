"""
Фабрика Flask-приложения: конфигурация, безопасность, HTML-страницы.

Запуск: python main.py  (см. README.md)
"""

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, redirect, send_from_directory, url_for

from .admin import admin_bp
from .auth import auth_bp, bootstrap_admin
from .db import close_db, init_db, query_one
from .records import records_bp
from .reference import reference_bp
from .security import (
    load_current_user,
    page_admin_required,
    page_login_required,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"

DEFAULT_SECRET_KEY = "dev-secret-change-me-in-production"


def _load_env() -> None:
    """Загружает .env (если есть) — Flask сделает это сам при наличии python-dotenv."""
    env_file = ROOT_DIR / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file)
        except ImportError:
            pass


def _apply_tz() -> None:
    """
    Перечитывает переменную TZ для libc.

    Нужно для PaaS: если переменная появилась ПОСЛЕ старта процесса,
    libc могла закешировать UTC — datetime.now() продолжил бы считать
    по нему, и все записи уходили бы на разницу в часы.
    """
    if os.environ.get("TZ") and hasattr(time, "tzset"):
        time.tzset()


def create_app() -> Flask:
    _load_env()
    _apply_tz()

    app = Flask(__name__, static_folder=None)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY),
        DATABASE_PATH=os.environ.get("DATABASE_PATH", str(ROOT_DIR / "data.db")),
        SEED_DATA=os.environ.get("SEED_DATA", "false"),
        SESSION_COOKIE_HTTPONLY=True,          # недоступна для JavaScript
        SESSION_COOKIE_SAMESITE="Lax",         # защита от CSRF
        SESSION_COOKIE_SECURE=_as_bool(os.environ.get("SESSION_COOKIE_SECURE", "false")),
        REMEMBER_COOKIE_HTTPONLY=True,
        PERMANENT_SESSION_LIFETIME=timedelta(
            days=int(os.environ.get("PERMANENT_SESSION_DAYS", "7"))
        ),
        MAX_CONTENT_LENGTH=256 * 1024,         # запросы больше 256 КБ не нужны
    )

    if app.config["SECRET_KEY"] == DEFAULT_SECRET_KEY:
        app.logger.warning(
            "SECRET_KEY не задан — используется значение по умолчанию. "
            "Задайте свой ключ в .env перед выходом в продакшен."
        )

    # --- БД -------------------------------------------------------------
    with app.app_context():
        init_db(seed=_as_bool(app.config["SEED_DATA"]))
        # Первый запуск: создаём администратора из .env, если он задан
        admin_login = os.environ.get("ADMIN_LOGIN", "").strip()
        admin_password = os.environ.get("ADMIN_PASSWORD", "")
        if admin_login and admin_password:
            bootstrap_admin(
                admin_login,
                admin_password,
                os.environ.get("ADMIN_NAME", "Администратор"),
            )

    # --- Бэкенд API ------------------------------------------------------
    app.register_blueprint(auth_bp)
    app.register_blueprint(records_bp)
    app.register_blueprint(reference_bp)
    app.register_blueprint(admin_bp)
    app.teardown_appcontext(close_db)

    # --- HTML-страницы ---------------------------------------------------
    _register_pages(app)
    _register_error_handlers(app)
    return app


def _as_bool(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _register_pages(app: Flask) -> None:
    """Отдача статики и страниц из frontend/."""

    @app.get("/")
    @page_login_required
    def index_page():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/login")
    def login_page():
        if load_current_user() is not None:
            return redirect(url_for("index_page"))
        return send_from_directory(FRONTEND_DIR, "login.html")

    @app.get("/admin")
    @page_admin_required
    def admin_page():
        return send_from_directory(FRONTEND_DIR, "admin.html")

    @app.get("/static/<path:filename>")
    def static_files(filename: str):
        return send_from_directory(FRONTEND_DIR, filename)

    @app.get("/health")
    def health():
        """
        Проверка живости + диагностика времени для мониторинга/PaaS.

        Показывает, в каком часовом поясе САМ процесс считает время
        (server_time), что видит в переменной TZ и доступна ли tzdata —
        этого достаточно, чтобы найти причину часовых сдвигов.
        """
        try:
            query_one("SELECT 1 AS ok")
        except Exception:  # noqa: BLE001 — база недоступна
            return jsonify({"status": "error"}), 500

        try:
            ZoneInfo("Europe/Moscow")
            tzdata_ok = True
        except Exception:  # noqa: BLE001 — zoneinfo недоступна
            tzdata_ok = False

        return jsonify({
            "status": "ok",
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "server_time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "tz_env": os.environ.get("TZ"),
            "tzdata_available": tzdata_ok,
        })


def _register_error_handlers(app: Flask) -> None:
    """Понятные сообщения об ошибках вместо технического краха."""

    def wants_json():
        from flask import request

        return request.path.startswith("/api/")

    @app.errorhandler(400)
    def bad_request(error):
        if wants_json():
            return jsonify({"error": getattr(error, "description", "Некорректный запрос")}), 400
        return _error_page("Некорректный запрос", "Проверьте введённые данные."), 400

    @app.errorhandler(401)
    def unauthorized(_error):
        if wants_json():
            return jsonify({"error": "Требуется вход в систему"}), 401
        return redirect(url_for("login_page"))

    @app.errorhandler(403)
    def forbidden(_error):
        if wants_json():
            return jsonify({"error": "Недостаточно прав"}), 403
        return _error_page("Доступ запрещён", "Эта страница доступна только руководителю."), 403

    @app.errorhandler(404)
    def not_found(_error):
        if wants_json():
            return jsonify({"error": "Не найдено"}), 404
        return _error_page("Страница не найдена", "Проверьте адрес или вернитесь на главную."), 404

    @app.errorhandler(413)
    def too_large(_error):
        if wants_json():
            return jsonify({"error": "Слишком большой объём данных"}), 413
        return _error_page("Слишком большой запрос", "Уменьшите объём данных."), 413

    @app.errorhandler(500)
    def server_error(_error):
        if wants_json():
            return jsonify({"error": "Внутренняя ошибка сервера. Попробуйте позже."}), 500
        return _error_page(
            "Внутренняя ошибка сервера",
            "Попробуйте обновить страницу позже. Если ошибка повторяется — сообщите администратору.",
        ), 500


def _error_page(title: str, message: str) -> str:
    """Минимальная HTML-страница ошибки (без внешних зависимостей)."""
    from html import escape

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} — Тайм-трекер</title>
  <link rel="stylesheet" href="/static/css/styles.css">
</head>
<body>
  <main class="container narrow card" style="margin-top:14vh">
    <h1 class="h2">{escape(title)}</h1>
    <p class="muted">{escape(message)}</p>
    <a class="btn btn-primary btn-block" href="/">На главную</a>
  </main>
</body>
</html>"""
