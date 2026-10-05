"""
CRUD трудозатрат.

Правила изоляции данных:
  * владелец записи определяется ТОЛЬКО из сессии (session user_id);
  * параметр user_id в URL/query никогда не принимается во внимание;
  * руководитель (admin) может читать/править записи всех сотрудников.
"""

from flask import Blueprint, jsonify, request

from .db import execute, query_all, query_one
from .duration import (
    break_minutes_total,
    calculate_duration_hours,
    is_valid_date,
    now_hhmm,
    now_iso,
    pause_delta_seconds,
    to_minutes,
    today_iso,
)
from .security import api_login_required, load_current_user

records_bp = Blueprint("records", __name__, url_prefix="/api/records")

MAX_TASK_LENGTH = 200
MAX_NOTES_LENGTH = 2000


# ---------------------------------------------------------------------------
# Вспомогательные функции валидации
# ---------------------------------------------------------------------------

def _validate_payload(
    data: dict, *, partial: bool = False, paused_seconds: int = 0
) -> tuple[dict | None, str | None]:
    """
    Нормализует и проверяет поля записи.

    partial=True — при обновлении (PATCH-стиль): отсутствующие поля игнорируются.
    paused_seconds — уже накопленная пауза (вычитается из длительности).
    Возвращает (нормализованные_поля, ошибка).
    """
    fields: dict = {}

    if "work_date" in data or not partial:
        work_date = str(data.get("work_date", "")).strip()
        if not is_valid_date(work_date):
            return None, "Некорректная дата. Ожидается формат ГГГГ-ММ-ДД."
        fields["work_date"] = work_date

    if "project_id" in data or not partial:
        try:
            project_id = int(data.get("project_id"))
        except (TypeError, ValueError):
            return None, "Выберите проект из списка."
        project = query_one("SELECT id, is_active FROM projects WHERE id = ?", (project_id,))
        if project is None:
            return None, "Указанный проект не найден."
        if not project["is_active"]:
            return None, "Проект деактивирован — выберите другой."
        fields["project_id"] = project_id

    if "task" in data or not partial:
        task = str(data.get("task", "")).strip()
        if not task:
            return None, "Укажите задачу / работу."
        if len(task) > MAX_TASK_LENGTH:
            return None, f"Задача слишком длинная (максимум {MAX_TASK_LENGTH} символов)."
        fields["task"] = task

    if "start_time" in data or not partial:
        start_time = str(data.get("start_time", "")).strip()
        try:
            to_minutes(start_time)
        except ValueError:
            return None, "Некорректное время начала (ожидается ЧЧ:ММ)."
        fields["start_time"] = start_time[:5]

    # Окончание необязательно: NULL означает «таймер запущен».
    if "end_time" in data:
        raw_end = data.get("end_time")
        if raw_end in (None, "", False):
            fields["end_time"] = None
        else:
            end_time = str(raw_end).strip()
            try:
                to_minutes(end_time)
            except ValueError:
                return None, "Некорректное время окончания (ожидается ЧЧ:ММ)."
            fields["end_time"] = end_time[:5]

    if "break_min" in data or not partial:
        try:
            break_min = int(data.get("break_min", 0) or 0)
        except (TypeError, ValueError):
            return None, "Перерыв должен быть числом минут."
        if break_min < 0 or break_min > 24 * 60:
            return None, "Перерыв должен быть от 0 до 1440 минут."
        fields["break_min"] = break_min

    if "notes" in data:
        notes = str(data.get("notes", "") or "").strip()
        if len(notes) > MAX_NOTES_LENGTH:
            return None, f"Примечание слишком длинное (максимум {MAX_NOTES_LENGTH} символов)."
        fields["notes"] = notes

    # Если указано окончание — валидируем соотношение времён и считаем длительность.
    if fields.get("end_time") is not None and "start_time" in fields:
        overnight = bool(data.get("overnight"))
        start_min = to_minutes(fields["start_time"])
        end_min = to_minutes(fields["end_time"])
        if not overnight and end_min <= start_min:
            return None, "Время окончания должно быть позже времени начала."
        try:
            fields["duration_hours"] = calculate_duration_hours(
                fields["start_time"],
                fields["end_time"],
                fields.get("break_min", 0),
                paused_seconds,
            )
        except ValueError as exc:
            return None, str(exc)
    elif fields.get("end_time") is None:
        fields["duration_hours"] = None

    return fields, None


def _get_record_or_404(record_id: int) -> tuple[dict | None, tuple | None]:
    """
    Возвращает запись, если текущий пользователь имеет право её видеть.

    Сотрудник — только свои записи (id берётся из сессии),
    руководитель — любые. Иначе 404 (чтобы не раскрывать чужие ID).
    """
    user = load_current_user()
    record = query_one(
        "SELECT r.*, u.full_name AS user_name, u.rate, p.name AS project_name "
        "FROM records r "
        "JOIN users u ON u.id = r.user_id "
        "LEFT JOIN projects p ON p.id = r.project_id "
        "WHERE r.id = ?",
        (record_id,),
    )
    if record is None:
        return None, (jsonify({"error": "Запись не найдена"}), 404)
    if user["role"] != "admin" and record["user_id"] != user["id"]:
        return None, (jsonify({"error": "Запись не найдена"}), 404)
    return record, None


def _serialize(record: dict) -> dict:
    paused_seconds = int(record.get("paused_seconds") or 0)
    pause_started_at = record.get("pause_started_at")
    return {
        "id": record["id"],
        "user_id": record["user_id"],
        "user_name": record.get("user_name"),
        "work_date": record["work_date"],
        "project_id": record["project_id"],
        "project_name": record.get("project_name"),
        "task": record["task"],
        "start_time": record["start_time"],
        # Точная отметка старта (с секундами) — от неё идёт счётчик 0:00:00
        "started_at": record.get("started_at"),
        "end_time": record["end_time"],
        "break_min": record["break_min"],
        # Итоговый перерыв для отображения: ручной перерыв + все паузы
        "break_min_total": break_minutes_total(
            record["break_min"], paused_seconds, pause_started_at
        ),
        "notes": record["notes"],
        "duration_hours": record["duration_hours"],
        "status": "running" if record["end_time"] is None else "done",
        # Накопленная пауза БЕЗ текущего сегмента: счётчик на клиенте
        # замораживается на pause_started_at, текущую паузу вычитать
        # второй раз нельзя (иначе после долгой паузы счётчик уходит в 0).
        # Текущий сегмент отдельно: pause_started_at + is_paused.
        "paused_seconds": paused_seconds,
        "pause_started_at": pause_started_at,
        "is_paused": bool(pause_started_at),
    }


# ---------------------------------------------------------------------------
# Маршруты
# ---------------------------------------------------------------------------

@records_bp.get("")
@api_login_required
def list_records():
    """
    Записи ТЕКУЩЕГО пользователя (ID берётся из сессии).

    Query-параметр user_id намеренно игнорируется — подменить владельца
    через URL невозможно. Админу для всех записей служит /api/admin/records.
    """
    user = load_current_user()
    work_date = request.args.get("date", today_iso()).strip() or today_iso()
    if not is_valid_date(work_date):
        return jsonify({"error": "Некорректный параметр date"}), 400

    rows = query_all(
        "SELECT r.*, u.full_name AS user_name, p.name AS project_name "
        "FROM records r "
        "JOIN users u ON u.id = r.user_id "
        "LEFT JOIN projects p ON p.id = r.project_id "
        "WHERE r.user_id = ? AND r.work_date = ? "
        "ORDER BY r.start_time DESC, r.id DESC",
        (user["id"], work_date),
    )
    return jsonify({"date": work_date, "records": [_serialize(r) for r in rows]})


@records_bp.post("")
@api_login_required
def create_record():
    """Ручной ввод: создаёт завершённую запись и пересчитывает длительность."""
    user = load_current_user()
    data = request.get_json(silent=True) or {}

    # Если время окончания не передано — запись создаётся как «запущенная».
    if "end_time" not in data:
        data["end_time"] = None

    fields, error = _validate_payload(data)
    if error:
        return jsonify({"error": error}), 400

    record_id = execute(
        "INSERT INTO records "
        "(user_id, project_id, work_date, task, start_time, started_at, end_time, "
        " break_min, notes, duration_hours) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user["id"],
            fields["project_id"],
            fields["work_date"],
            fields["task"],
            fields["start_time"],
            # Ручной ввод задаётся с точностью до минуты — секунды нулевые
            f"{fields['work_date']} {fields['start_time']}:00",
            fields.get("end_time"),
            fields["break_min"],
            fields.get("notes", ""),
            fields.get("duration_hours"),
        ),
    )
    record = query_one("SELECT * FROM records WHERE id = ?", (record_id,))
    return jsonify({"ok": True, "record": _serialize(record)}), 201


@records_bp.put("/<int:record_id>")
@api_login_required
def update_record(record_id: int):
    """Обновление записи владельцу или руководителю."""
    record, error_response = _get_record_or_404(record_id)
    if error_response:
        return error_response

    data = request.get_json(silent=True) or {}
    # Объединяем текущие значения с переданными, чтобы проверить итоговое состояние.
    merged = {
        "work_date": data.get("work_date", record["work_date"]),
        "project_id": data.get("project_id", record["project_id"]),
        "task": data.get("task", record["task"]),
        "start_time": data.get("start_time", record["start_time"]),
        "end_time": data.get("end_time", record["end_time"]),
        "break_min": data.get("break_min", record["break_min"]),
        "notes": data.get("notes", record["notes"]),
        "overnight": data.get("overnight", False),
    }
    fields, error = _validate_payload(
        merged, paused_seconds=int(record.get("paused_seconds") or 0)
    )
    if error:
        return jsonify({"error": error}), 400

    execute(
        "UPDATE records SET project_id = ?, work_date = ?, task = ?, "
        "start_time = ?, end_time = ?, break_min = ?, notes = ?, "
        "duration_hours = ?, updated_at = datetime('now', 'localtime') "
        "WHERE id = ?",
        (
            fields["project_id"],
            fields["work_date"],
            fields["task"],
            fields["start_time"],
            fields.get("end_time"),
            fields["break_min"],
            fields.get("notes", ""),
            fields.get("duration_hours"),
            record_id,
        ),
    )
    updated = query_one("SELECT * FROM records WHERE id = ?", (record_id,))
    return jsonify({"ok": True, "record": _serialize(updated)})


@records_bp.delete("/<int:record_id>")
@api_login_required
def delete_record(record_id: int):
    """Удаление — владельцу или руководителю."""
    _record, error_response = _get_record_or_404(record_id)
    if error_response:
        return error_response
    execute("DELETE FROM records WHERE id = ?", (record_id,))
    return jsonify({"ok": True})


@records_bp.post("/start")
@api_login_required
def start_timer():
    """
    Большая кнопка «Начать работу»: создаёт запись без времени окончания
    (end_time = NULL), длительность пока не считается.
    """
    user = load_current_user()

    running = query_one(
        "SELECT id FROM records WHERE user_id = ? AND end_time IS NULL",
        (user["id"],),
    )
    if running:
        return jsonify({"error": "Сначала завершите текущую запись"}), 409

    data = request.get_json(silent=True) or {}
    explicit_start = "start_time" in data
    data.setdefault("work_date", today_iso())
    data.setdefault("start_time", now_hhmm())
    data.setdefault("end_time", None)

    fields, error = _validate_payload(data)
    if error:
        return jsonify({"error": error}), 400

    # Точная отметка старта: с неё идёт счётчик 0:00:00 (start_time — только минуты)
    started_at = (
        f"{fields['work_date']} {fields['start_time']}:00"
        if explicit_start
        else now_iso()
    )

    record_id = execute(
        "INSERT INTO records "
        "(user_id, project_id, work_date, task, start_time, started_at, end_time, "
        " break_min, notes, duration_hours) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, 0, ?, NULL)",
        (
            user["id"],
            fields["project_id"],
            fields["work_date"],
            fields["task"],
            fields["start_time"],
            started_at,
            fields.get("notes", ""),
        ),
    )
    record = query_one("SELECT * FROM records WHERE id = ?", (record_id,))
    return jsonify({"ok": True, "record": _serialize(record)}), 201


@records_bp.post("/<int:record_id>/pause")
@api_login_required
def pause_timer(record_id: int):
    """
    Ставит таймер на паузу: запоминает момент остановки.
    Текущий сегмент паузы вычитается из длительности при завершении.
    """
    record, error_response = _get_record_or_404(record_id)
    if error_response:
        return error_response
    if record["end_time"] is not None:
        return jsonify({"error": "Запись уже завершена"}), 409
    if record.get("pause_started_at"):
        return jsonify({"error": "Таймер уже на паузе"}), 409

    execute(
        "UPDATE records SET pause_started_at = ?, "
        "updated_at = datetime('now', 'localtime') WHERE id = ?",
        (now_iso(), record_id),
    )
    updated = query_one("SELECT * FROM records WHERE id = ?", (record_id,))
    return jsonify({"ok": True, "record": _serialize(updated)})


@records_bp.post("/<int:record_id>/resume")
@api_login_required
def resume_timer(record_id: int):
    """Снимает таймер с паузы: добавляет прошедшее время паузы к накопленному."""
    record, error_response = _get_record_or_404(record_id)
    if error_response:
        return error_response
    if record["end_time"] is not None:
        return jsonify({"error": "Запись уже завершена"}), 409
    if not record.get("pause_started_at"):
        return jsonify({"error": "Таймер не на паузе"}), 409

    paused_total = int(record.get("paused_seconds") or 0) + pause_delta_seconds(
        record["pause_started_at"]
    )
    execute(
        "UPDATE records SET paused_seconds = ?, pause_started_at = NULL, "
        "updated_at = datetime('now', 'localtime') WHERE id = ?",
        (paused_total, record_id),
    )
    updated = query_one("SELECT * FROM records WHERE id = ?", (record_id,))
    return jsonify({"ok": True, "record": _serialize(updated)})


@records_bp.post("/<int:record_id>/stop")
@api_login_required
def stop_timer(record_id: int):
    """Останавливает таймер: ставит текущее время и считает длительность."""
    record, error_response = _get_record_or_404(record_id)
    if error_response:
        return error_response
    if record["end_time"] is not None:
        return jsonify({"error": "Запись уже завершена"}), 409

    # Если на паузе — сначала закрываем текущий сегмент паузы
    paused_seconds = int(record.get("paused_seconds") or 0)
    if record.get("pause_started_at"):
        paused_seconds += pause_delta_seconds(record["pause_started_at"])
        execute(
            "UPDATE records SET paused_seconds = ?, pause_started_at = NULL WHERE id = ?",
            (paused_seconds, record_id),
        )

    end_time = now_hhmm()
    try:
        duration = calculate_duration_hours(
            record["start_time"], end_time, record["break_min"], paused_seconds
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    execute(
        "UPDATE records SET end_time = ?, duration_hours = ?, "
        "updated_at = datetime('now', 'localtime') WHERE id = ?",
        (end_time, duration, record_id),
    )
    updated = query_one("SELECT * FROM records WHERE id = ?", (record_id,))
    return jsonify({"ok": True, "record": _serialize(updated)})


@records_bp.get("/running")
@api_login_required
def running_record():
    """Незавершённая запись текущего пользователя (или null)."""
    user = load_current_user()
    record = query_one(
        "SELECT r.*, u.full_name AS user_name, p.name AS project_name "
        "FROM records r "
        "JOIN users u ON u.id = r.user_id "
        "LEFT JOIN projects p ON p.id = r.project_id "
        "WHERE r.user_id = ? AND r.end_time IS NULL "
        "ORDER BY r.id DESC LIMIT 1",
        (user["id"],),
    )
    return jsonify({"record": _serialize(record) if record else None})


@records_bp.get("/<int:record_id>")
@api_login_required
def get_record(record_id: int):
    """Получение одной записи (админу или владельцу — см. _get_record_or_404)."""
    record, error_response = _get_record_or_404(record_id)
    if error_response:
        return error_response
    return jsonify({"record": _serialize(record)})
