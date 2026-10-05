"""
Аутентификация: вход, выход, создание первого администратора.

Пароли хранятся только в виде солёного bcrypt-хеша.
"""

from collections import defaultdict, deque
from threading import Lock

import bcrypt
from flask import Blueprint, jsonify, request, session

from .db import execute, query_all, query_one
from .security import PUBLIC_USER_FIELDS, load_current_user

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

MIN_PASSWORD_LENGTH = 6

# --- Простой троттлинг попыток входа (защита от перебора пароля) -----------
_MAX_ATTEMPTS = 10          # неудачных попыток...
_WINDOW_SECONDS = 900       # ...за 15 минут
_attempts: dict[str, deque] = defaultdict(deque)
_attempts_lock = Lock()


def _throttled(key: str) -> bool:
    """True, если превышен лимит неудачных попыток входа."""
    import time

    now = time.monotonic()
    with _attempts_lock:
        attempts = _attempts[key]
        while attempts and now - attempts[0] > _WINDOW_SECONDS:
            attempts.popleft()
        return len(attempts) >= _MAX_ATTEMPTS


def _register_failure(key: str) -> None:
    import time

    with _attempts_lock:
        _attempts[key].append(time.monotonic())


def _clear_failures(key: str) -> None:
    with _attempts_lock:
        _attempts.pop(key, None)


# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """bcrypt-хеш с индивидуальной солью."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _public_user(row: dict) -> dict:
    return {key: row[key] for key in PUBLIC_USER_FIELDS.split(", ")}


def bootstrap_admin(login: str, password: str, full_name: str = "Администратор") -> None:
    """
    Создаёт первого администратора при первом запуске,
    если в .env заданы ADMIN_LOGIN и ADMIN_PASSWORD и админа ещё нет.
    """
    if query_one("SELECT id FROM users WHERE role = 'admin' AND is_active = 1"):
        return
    execute(
        "INSERT INTO users (login, full_name, role, rate, password_hash) "
        "VALUES (?, ?, 'admin', 0, ?)",
        (login, full_name, hash_password(password)),
    )


@auth_bp.get("/status")
def status():
    """Статус аутентификации + флаг «нужно создать первого администратора»."""
    user = load_current_user()
    setup_required = query_one(
        "SELECT id FROM users WHERE role = 'admin' AND is_active = 1"
    ) is None
    return jsonify(
        {
            "authenticated": user is not None,
            "setup_required": setup_required,
            "user": _public_user(user) if user else None,
        }
    )


@auth_bp.post("/setup")
def setup():
    """Создание первого администратора. Доступно только до его появления."""
    if query_one("SELECT id FROM users WHERE role = 'admin' AND is_active = 1"):
        return jsonify({"error": "Администратор уже создан, войдите в систему"}), 403

    data = request.get_json(silent=True) or {}
    login = str(data.get("login", "")).strip()
    full_name = str(data.get("full_name", "")).strip()
    password = str(data.get("password", ""))

    if not login or not full_name:
        return jsonify({"error": "Заполните логин и ФИО"}), 400
    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов"}), 400
    if query_one("SELECT id FROM users WHERE login = ?", (login,)):
        return jsonify({"error": "Пользователь с таким логином уже существует"}), 409

    user_id = execute(
        "INSERT INTO users (login, full_name, role, rate, password_hash) "
        "VALUES (?, ?, 'admin', 0, ?)",
        (login, full_name, hash_password(password)),
    )
    session.clear()
    session["user_id"] = user_id
    session.permanent = True
    return jsonify({"ok": True, "user": {"id": user_id, "login": login,
                                         "full_name": full_name, "role": "admin"}}), 201


@auth_bp.post("/login")
def login():
    """Вход по логину и паролю. При успехе ставится httpOnly-кука сессии."""
    data = request.get_json(silent=True) or {}
    login_value = str(data.get("login", "")).strip()
    password = str(data.get("password", ""))

    if not login_value or not password:
        return jsonify({"error": "Введите логин и пароль"}), 400

    throttle_key = f"{request.remote_addr}:{login_value.lower()}"
    if _throttled(throttle_key):
        return jsonify({"error": "Слишком много попыток входа. Повторите через 15 минут."}), 429

    user = query_one(
        f"SELECT {PUBLIC_USER_FIELDS}, password_hash FROM users WHERE login = ?",
        (login_value,),
    )
    if user is None or not verify_password(password, user["password_hash"]):
        _register_failure(throttle_key)
        return jsonify({"error": "Неверный логин или пароль"}), 401
    if not user["is_active"]:
        _register_failure(throttle_key)
        return jsonify({"error": "Учётная запись деактивирована. Обратитесь к руководителю."}), 403

    _clear_failures(throttle_key)
    session.clear()  # защита от фиксации сессии
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify({"ok": True, "user": _public_user(user)})


@auth_bp.post("/logout")
def logout():
    """Выход: удаляет сессию на сервере и куку в браузере."""
    session.clear()
    return jsonify({"ok": True})


@auth_bp.get("/me")
def me():
    """Текущий пользователь (null, если не вошли)."""
    user = load_current_user()
    return jsonify({"user": _public_user(user) if user else None})
