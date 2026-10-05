"""
Панель руководителя: все записи, справочники «Сотрудники» и «Проекты».

Все маршруты закрыты декоратором api_admin_required — роль проверяется
до обращения к данным.
"""

from flask import Blueprint, jsonify, request

from .auth import hash_password
from .db import execute, query_all, query_one
from .duration import break_minutes_total, is_valid_date
from .security import api_admin_required, load_current_user

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")

MIN_PASSWORD_LENGTH = 6


# ---------------------------------------------------------------------------
# Записи всех сотрудников + фильтры
# ---------------------------------------------------------------------------

@admin_bp.get("/records")
@api_admin_required
def all_records():
    """
    Записи всех сотрудников с именем, ставкой и суммой к оплате.

    Фильтры: date_from, date_to, user_id, project_id.
    Длительность считает сервер: (конец - начало) - перерыв/60, округление 2 знака.
    """
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    user_id = (request.args.get("user_id") or "").strip()
    project_id = (request.args.get("project_id") or "").strip()

    conditions, params = [], []
    if date_from:
        if not is_valid_date(date_from):
            return jsonify({"error": "Некорректная начальная дата"}), 400
        conditions.append("r.work_date >= ?")
        params.append(date_from)
    if date_to:
        if not is_valid_date(date_to):
            return jsonify({"error": "Некорректная конечная дата"}), 400
        conditions.append("r.work_date <= ?")
        params.append(date_to)
    if user_id:
        if not user_id.isdigit():
            return jsonify({"error": "Некорректный фильтр по сотруднику"}), 400
        conditions.append("r.user_id = ?")
        params.append(int(user_id))
    if project_id:
        if not project_id.isdigit():
            return jsonify({"error": "Некорректный фильтр по проекту"}), 400
        conditions.append("r.project_id = ?")
        params.append(int(project_id))

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Список условий собран из констант, значения подставляются параметрически.
    rows = query_all(
        "SELECT r.id, r.user_id, u.full_name AS user_name, u.rate, "
        "       r.work_date, r.project_id, p.name AS project_name, r.task, "
        "       r.start_time, r.started_at, r.end_time, r.break_min, r.notes, r.duration_hours, "
        "       r.paused_seconds, r.pause_started_at, "
        "       ROUND(COALESCE(r.duration_hours, 0) * u.rate, 2) AS pay "
        "FROM records r "
        "JOIN users u ON u.id = r.user_id "
        "LEFT JOIN projects p ON p.id = r.project_id "
        f"{where} "
        "ORDER BY r.work_date DESC, r.start_time DESC, r.id DESC",
        tuple(params),
    )

    for row in rows:
        if row["end_time"] is not None:
            row["status"] = "done"
        elif row["pause_started_at"]:
            row["status"] = "paused"
        else:
            row["status"] = "running"
        row["is_paused"] = bool(row["pause_started_at"])
        # Итоговый перерыв для колонки «Перерыв (мин)»: ручной + все паузы
        row["break_min_total"] = break_minutes_total(
            row["break_min"], row["paused_seconds"], row["pause_started_at"]
        )
        # paused_seconds и pause_started_at остаются в ответе: по ним клиент
        # считает в реальном времени перерыв и длительность незавершённых записей
    return jsonify({"records": rows})


# ---------------------------------------------------------------------------
# Справочник сотрудников (без удаления — только деактивация)
# ---------------------------------------------------------------------------

_EMPLOYEE_FIELDS = (
    "SELECT id, login, full_name, role, rate, is_active, created_at, "
    " (SELECT COUNT(*) FROM records WHERE records.user_id = users.id) AS record_count "
    "FROM users"
)


@admin_bp.get("/employees")
@api_admin_required
def employees():
    """Список сотрудников (включая деактивированных — чтобы фильтровать старые записи)."""
    return jsonify({"employees": query_all(f"{_EMPLOYEE_FIELDS} ORDER BY is_active DESC, full_name")})


def _employee_payload(data: dict) -> tuple[dict | None, str | None]:
    full_name = str(data.get("full_name", "")).strip()
    if not full_name:
        return None, "Укажите ФИО сотрудника."
    if len(full_name) > 120:
        return None, "ФИО слишком длинное."

    login = str(data.get("login", "")).strip()
    if not login or len(login) < 2 or len(login) > 50:
        return None, "Логин должен быть от 2 до 50 символов."
    if " " in login:
        return None, "Логин не должен содержать пробелы."

    role = str(data.get("role", "user"))
    if role not in {"admin", "user"}:
        return None, "Роль может быть только admin или user."

    try:
        rate = float(data.get("rate", 0) or 0)
    except (TypeError, ValueError):
        return None, "Ставка должна быть числом (руб/час)."
    if rate < 0 or rate > 1_000_000:
        return None, "Ставка должна быть в диапазоне от 0 до 1 000 000."

    return {"full_name": full_name, "login": login, "role": role, "rate": round(rate, 2)}, None


@admin_bp.post("/employees")
@api_admin_required
def create_employee():
    """Создание сотрудника (или нового руководителя)."""
    data = request.get_json(silent=True) or {}
    fields, error = _employee_payload(data)
    if error:
        return jsonify({"error": error}), 400

    password = str(data.get("password", ""))
    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов"}), 400
    if query_one("SELECT id FROM users WHERE login = ?", (fields["login"],)):
        return jsonify({"error": "Пользователь с таким логином уже существует"}), 409

    user_id = execute(
        "INSERT INTO users (login, full_name, role, rate, password_hash) "
        "VALUES (?, ?, ?, ?, ?)",
        (fields["login"], fields["full_name"], fields["role"], fields["rate"],
         hash_password(password)),
    )
    row = query_one(f"{_EMPLOYEE_FIELDS} WHERE id = ?", (user_id,))
    return jsonify({"ok": True, "employee": row}), 201


@admin_bp.put("/employees/<int:user_id>")
@api_admin_required
def update_employee(user_id: int):
    """Редактирование: ФИО, роль, ставка, активность; пароль — опционально."""
    current = load_current_user()
    employee = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if employee is None:
        return jsonify({"error": "Сотрудник не найден"}), 404

    data = request.get_json(silent=True) or {}
    merged = {
        "full_name": data.get("full_name", employee["full_name"]),
        "login": data.get("login", employee["login"]),
        "role": data.get("role", employee["role"]),
        "rate": data.get("rate", employee["rate"]),
    }
    fields, error = _employee_payload(merged)
    if error:
        return jsonify({"error": error}), 400

    # Нельзя деактивировать или понизить самого себя — иначе можно заблокировать систему.
    is_active = bool(data.get("is_active", employee["is_active"]))
    if user_id == current["id"] and (not is_active or fields["role"] != "admin"):
        return jsonify({"error": "Нельзя деактивировать или разжаловать собственную учётную запись"}), 400

    duplicate = query_one(
        "SELECT id FROM users WHERE login = ? AND id <> ?", (fields["login"], user_id)
    )
    if duplicate:
        return jsonify({"error": "Пользователь с таким логином уже существует"}), 409

    # Нельзя оставить систему без руководителя.
    active_admins = query_one(
        "SELECT COUNT(*) AS cnt FROM users WHERE role = 'admin' AND is_active = 1"
    )["cnt"]
    if employee["role"] == "admin" and active_admins <= 1 and (
        not is_active or fields["role"] != "admin"
    ):
        return jsonify({"error": "Нельзя деактивировать последнего активного руководителя"}), 400

    password = str(data.get("password") or "")
    password_hash = employee["password_hash"]
    if password:
        if len(password) < MIN_PASSWORD_LENGTH:
            return jsonify({"error": f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов"}), 400
        password_hash = hash_password(password)

    execute(
        "UPDATE users SET login = ?, full_name = ?, role = ?, rate = ?, "
        "is_active = ?, password_hash = ? WHERE id = ?",
        (fields["login"], fields["full_name"], fields["role"], fields["rate"],
         1 if is_active else 0, password_hash, user_id),
    )
    return jsonify({"ok": True, "employee": query_one(f"{_EMPLOYEE_FIELDS} WHERE id = ?", (user_id,))})


@admin_bp.delete("/employees/<int:user_id>")
@api_admin_required
def delete_employee(user_id: int):
    """Удаление сотрудников запрещено — только деактивация."""
    return jsonify(
        {"error": "Удаление сотрудника запрещено: используйте деактивацию, "
                  "чтобы не ломать старые записи."}
    ), 403


# ---------------------------------------------------------------------------
# Справочник проектов
# ---------------------------------------------------------------------------

@admin_bp.get("/projects")
@api_admin_required
def projects():
    """Все проекты, включая деактивированные."""
    return jsonify({"projects": query_all(
        "SELECT p.*, (SELECT COUNT(*) FROM records WHERE project_id = p.id) AS record_count "
        "FROM projects p ORDER BY p.is_active DESC, p.name"
    )})


@admin_bp.post("/projects")
@api_admin_required
def create_project():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "Введите название проекта"}), 400
    if len(name) > 120:
        return jsonify({"error": "Название проекта слишком длинное"}), 400
    if query_one("SELECT id FROM projects WHERE name = ?", (name,)):
        return jsonify({"error": "Проект с таким названием уже существует"}), 409

    project_id = execute("INSERT INTO projects (name) VALUES (?)", (name,))
    return jsonify({"ok": True, "project": query_one("SELECT * FROM projects WHERE id = ?", (project_id,))}), 201


@admin_bp.put("/projects/<int:project_id>")
@api_admin_required
def update_project(project_id: int):
    project = query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if project is None:
        return jsonify({"error": "Проект не найден"}), 404

    data = request.get_json(silent=True) or {}
    name = str(data.get("name", project["name"])).strip()
    if not name:
        return jsonify({"error": "Введите название проекта"}), 400
    if len(name) > 120:
        return jsonify({"error": "Название проекта слишком длинное"}), 400
    duplicate = query_one(
        "SELECT id FROM projects WHERE name = ? AND id <> ?", (name, project_id)
    )
    if duplicate:
        return jsonify({"error": "Проект с таким названием уже существует"}), 409

    is_active = bool(data.get("is_active", project["is_active"]))
    execute(
        "UPDATE projects SET name = ?, is_active = ? WHERE id = ?",
        (name, 1 if is_active else 0, project_id),
    )
    return jsonify({"ok": True, "project": query_one("SELECT * FROM projects WHERE id = ?", (project_id,))})


@admin_bp.delete("/projects/<int:project_id>")
@api_admin_required
def delete_project(project_id: int):
    """Удаление проектов запрещено — только деактивация."""
    return jsonify(
        {"error": "Удаление проекта запрещено: используйте деактивацию, "
                  "чтобы не ломать старые записи."}
    ), 403
