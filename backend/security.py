"""
Аутентификация и авторизация.

* Текущий пользователь хранится в сессионной httpOnly-куке (Flask session),
  подписанной SECRET_KEY — подделать/прочитать из JS невозможно.
* ID пользователя берётся ТОЛЬКО из сессии: ни URL, ни параметры запроса
  не участвуют в определении владельца данных (изоляция данных).
"""

from functools import wraps

from flask import g, jsonify, redirect, session, url_for

from .db import query_one

PUBLIC_USER_FIELDS = "id, login, full_name, role, rate, is_active"


def load_current_user() -> dict | None:
    """Загружает пользователя из сессии и кладёт в g.current_user."""
    if hasattr(g, "current_user"):
        return g.current_user

    user = None
    user_id = session.get("user_id")
    if user_id is not None:
        user = query_one(
            f"SELECT {PUBLIC_USER_FIELDS} FROM users WHERE id = ?", (user_id,)
        )
        # Деактивированный сотрудник мгновенно теряет доступ
        if user is not None and not user["is_active"]:
            session.clear()
            user = None

    g.current_user = user
    return user


def is_admin() -> bool:
    user = load_current_user()
    return bool(user and user["role"] == "admin")


# ---------------------------------------------------------------------------
# Декораторы для JSON API
# ---------------------------------------------------------------------------

def api_login_required(view):
    """401, если запрос не аутентифицирован."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if load_current_user() is None:
            return jsonify({"error": "Требуется вход в систему"}), 401
        return view(*args, **kwargs)

    return wrapper


def api_admin_required(view):
    """403, если пользователь не руководитель (проверяется ДО работы с данными)."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        user = load_current_user()
        if user is None:
            return jsonify({"error": "Требуется вход в систему"}), 401
        if user["role"] != "admin":
            return jsonify({"error": "Доступ только для руководителя"}), 403
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Декораторы для HTML-страниц
# ---------------------------------------------------------------------------

def page_login_required(view):
    """Редирект на страницу входа, если сессии нет."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if load_current_user() is None:
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)

    return wrapper


def page_admin_required(view):
    """Редирект на главную для не-администраторов."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        user = load_current_user()
        if user is None:
            return redirect(url_for("login_page"))
        if user["role"] != "admin":
            return redirect(url_for("index_page"))
        return view(*args, **kwargs)

    return wrapper
