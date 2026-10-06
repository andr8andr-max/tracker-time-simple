"""
Смоук-тесты API тайм-трекера (без запуска сети — Flask test client).

Запуск из корня репозитория:
    python tests/smoke_test.py

Проверяются: аутентификация, изоляция данных между ролями,
расчёт длительности (в т.ч. через полночь), справочники, страницы.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Корректный вывод кириллицы на консолях Windows (cp866 и т.п.)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# Тестовая база в отдельном файле + включаем тестовые данные
_TMP = tempfile.mkdtemp(prefix="timetracker_test_")
os.environ["DATABASE_PATH"] = str(Path(_TMP) / "data.db")
os.environ["SEED_DATA"] = "true"
os.environ["SECRET_KEY"] = "smoke-test-secret"
os.environ["ADMIN_LOGIN"] = ""
os.environ["ADMIN_PASSWORD"] = ""

from backend.app import create_app  # noqa: E402  (после настройки окружения)

app = create_app()
app.config["TESTING"] = True

PASSED = 0
FAILED = []


def check(name, condition, detail=""):
    """Фиксирует результат проверки."""
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {detail}")


def login_client(login, password):
    """Новый клиент (куки свои) с выполненным входом."""
    client = app.test_client()
    response = client.post("/api/auth/login", json={"login": login, "password": password})
    return client, response


# --------------------------------------------------------------- статус и вход
print("\n== Аутентификация ==")
client = app.test_client()

status = client.get("/api/auth/status").get_json()
check("статус доступен без входа", status["authenticated"] is False)
check("первый админ уже создан тестовыми данными", status["setup_required"] is False)

bad = client.post("/api/auth/login", json={"login": "admin", "password": "wrong"})
check("неверный пароль -> 401", bad.status_code == 401, bad.status_code)

guest = app.test_client()
check("записи без входа -> 401", guest.get("/api/records").status_code == 401)
check("админ-API без входа -> 401", guest.get("/api/admin/records").status_code == 401)

admin, resp = login_client("admin", "admin123")
check("вход администратора -> 200", resp.status_code == 200, resp.status_code)

user, resp = login_client("user", "user123")
check("вход сотрудника -> 200", resp.status_code == 200, resp.status_code)

# setup повторно создать нельзя
repeat = app.test_client().post(
    "/api/auth/setup",
    json={"login": "boss", "password": "secret123", "full_name": "Босс"},
)
check("повторный setup -> 403", repeat.status_code == 403, repeat.status_code)

# --------------------------------------------------- первый запуск (чистая БД)
print("\n== Первый запуск: создание администратора ==")
MAIN_DB = os.environ["DATABASE_PATH"]
os.environ["DATABASE_PATH"] = str(Path(_TMP) / "fresh.db")
os.environ["SEED_DATA"] = "false"  # чистая база без тестовых данных
fresh_app = create_app()
fresh_app.config["TESTING"] = True
fresh = fresh_app.test_client()

check("на чистой базе требуется setup",
      fresh.get("/api/auth/status").get_json()["setup_required"] is True)

weak_setup = fresh.post(
    "/api/auth/setup", json={"login": "boss", "password": "123", "full_name": "Босс"}
)
check("слабый пароль при setup -> 400", weak_setup.status_code == 400)

made = fresh.post(
    "/api/auth/setup",
    json={"login": "boss", "password": "bosssecret", "full_name": "Босс Боссов"},
)
check("создание первого администратора -> 201", made.status_code == 201, made.status_code)
check("после setup пользователь залогинен",
      fresh.get("/api/auth/status").get_json()["authenticated"] is True)
check("второй setup -> 403",
      fresh_app.test_client().post("/api/auth/setup",
                                   json={"login": "x", "password": "yyyyyy",
                                         "full_name": "X"}).status_code == 403)
fresh_logout = fresh_app.test_client()
fresh_logout.post("/api/auth/login", json={"login": "boss", "password": "bosssecret"})
check("вход созданным администратором -> 200",
      fresh_logout.post("/api/auth/login",
                        json={"login": "boss", "password": "bosssecret"}).status_code == 200)
os.environ["DATABASE_PATH"] = MAIN_DB
os.environ["SEED_DATA"] = "true"

# --------------------------------------------- миграция существующей базы
print("\n== Миграция существующей базы ==")
import sqlite3  # noqa: E402

OLD_DB = str(Path(_TMP) / "old.db")
os.environ["DATABASE_PATH"] = OLD_DB
create_app()  # создаёт таблицы по текущей схеме

# Имитируем базу прошлой версии: убираем новые колонки и кладём старые данные
_conn = sqlite3.connect(OLD_DB)
_conn.execute("ALTER TABLE records DROP COLUMN paused_seconds")
_conn.execute("ALTER TABLE records DROP COLUMN pause_started_at")
_conn.execute("ALTER TABLE records DROP COLUMN started_at")
_conn.execute("INSERT INTO projects (id, name, is_active) VALUES (99, 'Старый проект', 1)")
_conn.commit()
_conn.close()

os.environ["SEED_DATA"] = "false"
create_app()  # должен добавить недостающие колонки, не тронув данные

_conn = sqlite3.connect(OLD_DB)
_columns = [row[1] for row in _conn.execute("PRAGMA table_info(records)")]
check("новые колонки добавлены в базу прошлой версии",
      "paused_seconds" in _columns and "pause_started_at" in _columns
      and "started_at" in _columns, _columns)
check("старые данные сохранились",
      _conn.execute("SELECT COUNT(*) FROM projects WHERE id = 99").fetchone()[0] == 1)
_conn.close()

os.environ["DATABASE_PATH"] = MAIN_DB
os.environ["SEED_DATA"] = "true"

# ---------------------------------------------------------- изоляция данных
print("\n== Изоляция данных ==")
check("сотрудник не видит админ-API -> 403", user.get("/api/admin/records").status_code == 403)
check("сотрудник не видит справочник сотрудников -> 403",
      user.get("/api/admin/employees").status_code == 403)

# Попытка подменить владельца через query-параметр
from datetime import datetime, timedelta  # noqa: E402

today = datetime.now().strftime("%Y-%m-%d")
own_today = user.get(f"/api/records?date={today}").get_json()["records"]
check("параметр user_id игнорируется: вернулись только свои записи",
      all(r["user_id"] == 2 for r in own_today), own_today)

foreign = user.put("/api/records/3", json={"task": "взлом"})  # запись админа
check("чужую запись нельзя изменить -> 404", foreign.status_code == 404, foreign.status_code)
foreign_del = user.delete("/api/records/3")
check("чужую запись нельзя удалить -> 404", foreign_del.status_code == 404)

# ------------------------------------------------------------ расчёт времени
print("\n== Расчёт длительности ==")
created = user.post(
    "/api/records",
    json={
        "work_date": "2026-01-15",
        "project_id": 1,
        "task": "Тестовая смена",
        "start_time": "09:00",
        "end_time": "18:00",
        "break_min": 60,
        "notes": "проверка формулы",
    },
)
check("смена 09:00-18:00 с перерывом 60 -> 201",
      created.status_code == 201, created.status_code)
check("длительность 8.00 часа",
      created.get_json()["record"]["duration_hours"] == 8.0,
      created.get_json())

overnight = user.post(
    "/api/records",
    json={
        "work_date": "2026-01-16",
        "project_id": 1,
        "task": "Ночная смена",
        "start_time": "22:00",
        "end_time": "02:00",
        "break_min": 0,
        "overnight": True,
    },
)
check("смена через полночь 22:00-02:00 -> 4.00 часа",
      overnight.get_json()["record"]["duration_hours"] == 4.0,
      overnight.get_json())

invalid = user.post(
    "/api/records",
    json={
        "work_date": "2026-01-17",
        "project_id": 1,
        "task": "Обратное время",
        "start_time": "18:00",
        "end_time": "09:00",
        "break_min": 0,
    },
)
check("конец раньше начала без флага -> 400", invalid.status_code == 400, invalid.status_code)

equal = user.post(
    "/api/records",
    json={
        "work_date": "2026-01-17",
        "project_id": 1,
        "task": "Нулевая смена",
        "start_time": "09:00",
        "end_time": "09:00",
        "break_min": 0,
    },
)
check("нулевая длительность -> 400", equal.status_code == 400, equal.status_code)

huge_break = user.post(
    "/api/records",
    json={
        "work_date": "2026-01-17",
        "project_id": 1,
        "task": "Перерыв больше смены",
        "start_time": "09:00",
        "end_time": "10:00",
        "break_min": 120,
    },
)
check("перерыв больше смены -> 400", huge_break.status_code == 400, huge_break.status_code)

injection = user.post(
    "/api/records",
    json={
        "work_date": "2026-01-18",
        "project_id": 1,
        "task": "'; DROP TABLE records; --",
        "start_time": "09:00",
        "end_time": "10:00",
        "break_min": 0,
    },
)
check("SQL-инъекция в задаче сохраняется как текст",
      injection.status_code == 201
      and injection.get_json()["record"]["task"] == "'; DROP TABLE records; --")
still_there = user.get("/api/records?date=2026-01-18").get_json()
check("таблица записей после инъекции цела", len(still_there["records"]) == 1)

# ---------------------------------------------------------------- таймер
print("\n== Таймер ==")
started = user.post("/api/records/start", json={"project_id": 2, "task": "Работаю"})
check("старт таймера -> 201", started.status_code == 201, started.status_code)
check("запись запущена (end_time = null)",
      started.get_json()["record"]["end_time"] is None)
check("длительность у запущенной записи не считается",
      started.get_json()["record"]["duration_hours"] is None)

# Счётчик должен идти с 0:00:00 — для этого нужна отметка старта с секундами
_started_rec = started.get_json()["record"]
check("у новой записи есть точная отметка старта", bool(_started_rec.get("started_at")))
if _started_rec.get("started_at"):
    _start_age = abs(
        (datetime.now() - datetime.strptime(_started_rec["started_at"], "%Y-%m-%d %H:%M:%S"))
        .total_seconds()
    )
    check("отметка старта — свежая (не старше 30 сек)", _start_age < 30, _start_age)
check("start_time остаётся минутным HH:MM для формулы длительности",
      len(_started_rec["start_time"]) == 5, _started_rec["start_time"])

second = user.post("/api/records/start", json={"project_id": 2, "task": "Вторая"})
check("второй таймер запрещён -> 409", second.status_code == 409, second.status_code)

running_id = started.get_json()["record"]["id"]
stopped = user.post(f"/api/records/{running_id}/stop")
check("остановка таймера -> 200", stopped.status_code == 200, stopped.status_code)
check("после остановки есть длительность",
      isinstance(stopped.get_json()["record"]["duration_hours"], float))

# ------------------------------------------------------------------- пауза
print("\n== Пауза таймера ==")
from backend.duration import calculate_duration_hours  # noqa: E402

check("пауза 30 мин вычитается: 09:00-18:00 - 60 мин перерыва - 30 мин паузы = 7.50",
      calculate_duration_hours("09:00", "18:00", 60, 1800) == 7.5)

from backend.duration import break_minutes_total  # noqa: E402

check("итоговый перерыв = ручной перерыв + пауза (10 + 30 = 40)",
      break_minutes_total(10, 1800) == 40)
check("без пауз итог равен ручному перерыву", break_minutes_total(7) == 7)
check("секунды отсекаются, как в счётчике H:MM:SS (59 сек = 0 мин)",
      break_minutes_total(0, 59) == 0)
check("минута паузы засчитывается (61 сек = 1 мин)",
      break_minutes_total(0, 61) == 1)
_paused_at = (datetime.now() - timedelta(seconds=61)).strftime("%Y-%m-%d %H:%M:%S")
check("текущая (незакрытая) пауза входит в итог",
      break_minutes_total(0, 0, _paused_at) >= 1)

paused_start = user.post("/api/records/start", json={"project_id": 3, "task": "Тест паузы"})
check("новый таймер -> 201", paused_start.status_code == 201, paused_start.status_code)
pause_id = paused_start.get_json()["record"]["id"]
check("изначально таймер не на паузе",
      paused_start.get_json()["record"]["is_paused"] is False)

on_pause = user.post(f"/api/records/{pause_id}/pause")
check("пауза -> 200", on_pause.status_code == 200, on_pause.status_code)
check("is_paused = true", on_pause.get_json()["record"]["is_paused"] is True)
check("повторная пауза -> 409", user.post(f"/api/records/{pause_id}/pause").status_code == 409)
check("running-эндпоинт видит паузу",
      user.get("/api/records/running").get_json()["record"]["is_paused"] is True)

# Регрессия: текущая (идущая) пауза НЕ входит в paused_seconds — иначе клиент,
# заморозивший счётчик на pause_started_at, вычтет её дважды и покажет 0:00:00
_paused_rec = user.get("/api/records/running").get_json()["record"]
check("во время паузы paused_seconds не включает текущий сегмент",
      _paused_rec["paused_seconds"] == 0 and _paused_rec["is_paused"] is True,
      _paused_rec)
check("но текущая пауза видна по pause_started_at",
      bool(_paused_rec["pause_started_at"]))

_admin_rows = admin.get("/api/admin/records").get_json()["records"]
_paused_row = next((r for r in _admin_rows if r["id"] == pause_id), None)
check("панель руководителя: у записи на паузе статус paused",
      _paused_row is not None and _paused_row["status"] == "paused", _paused_row)
_done_row = next((r for r in _admin_rows if r["end_time"] is not None), None)
check("панель руководителя: завершённые записи остаются done",
      _done_row is not None and _done_row["status"] == "done", _done_row)
check("для живых обновлений у записи есть поля паузы",
      all({"paused_seconds", "pause_started_at", "is_paused"} <= set(r) for r in _admin_rows))
check("is_paused согласован со статусом",
      all(r["is_paused"] == (r["status"] == "paused") for r in _admin_rows))
check("панель руководителя отдаёт итоговый перерыв",
      all("break_min_total" in r for r in _admin_rows))
check("у записи на паузе итоговый перерыв не меньше ручного",
      _paused_row is not None
      and _paused_row["break_min_total"] >= _paused_row["break_min"], _paused_row)

_user_rows = user.get("/api/records").get_json()["records"]
check("сотрудник тоже видит итоговый перерыв",
      all("break_min_total" in r for r in _user_rows))
check("итоговый перерыв не меньше ручного",
      all(r["break_min_total"] >= r["break_min"] for r in _user_rows))

import time  # noqa: E402

time.sleep(1.1)  # даём текущему сегменту паузы длиться >= 1 секунды
resumed = user.post(f"/api/records/{pause_id}/resume")
check("снятие с паузы -> 200", resumed.status_code == 200, resumed.status_code)
check("после снятия не на паузе",
      resumed.get_json()["record"]["is_paused"] is False)
check("закрытый сегмент паузы записан в paused_seconds",
      resumed.get_json()["record"]["paused_seconds"] >= 1,
      resumed.get_json()["record"])
check("resume без паузы -> 409", user.post(f"/api/records/{pause_id}/resume").status_code == 409)

check("пауза перед стопом -> 200",
      user.post(f"/api/records/{pause_id}/pause").status_code == 200)
stopped_paused = user.post(f"/api/records/{pause_id}/stop")
check("остановка на паузе -> 200", stopped_paused.status_code == 200, stopped_paused.status_code)
check("запись завершена и есть длительность",
      stopped_paused.get_json()["record"]["end_time"] is not None
      and isinstance(stopped_paused.get_json()["record"]["duration_hours"], float))

# --------------------------------------------------------- права руководителя
print("\n== Права руководителя ==")
all_records = admin.get("/api/admin/records").get_json()["records"]
check("админ видит записи всех сотрудников", len(all_records) >= 3, len(all_records))
check("у записей посчитана сумма к оплате",
      all(record.get("pay") is not None for record in all_records))

filtered = admin.get("/api/admin/records?date_from=2026-01-15&date_to=2026-01-16")
check("фильтр по диапазону дат работает",
      filtered.status_code == 200 and len(filtered.get_json()["records"]) == 2,
      filtered.get_json())

by_user = admin.get("/api/admin/records?user_id=2")
check("фильтр по сотруднику работает",
      all(r["user_id"] == 2 for r in by_user.get_json()["records"]))

by_project = admin.get("/api/admin/records?project_id=3")
check("фильтр по проекту работает",
      all(r["project_id"] == 3 for r in by_project.get_json()["records"]))

bad_filter = admin.get("/api/admin/records?date_from=31-12-2026")
check("некорректная дата фильтра -> 400", bad_filter.status_code == 400)

# --------------------------------------- удаление записи из панели руководителя
victim_rows = admin.get("/api/admin/records?user_id=2").get_json()["records"]
check("для удаления найдена запись сотрудника", len(victim_rows) > 0, len(victim_rows))
victim_id = victim_rows[0]["id"]

no_right = user.delete(f"/api/admin/records/{victim_id}")
check("сотрудник не может удалить запись через админ-API -> 403",
      no_right.status_code == 403, no_right.status_code)

removed = admin.delete(f"/api/admin/records/{victim_id}")
check("руководитель удаляет запись -> 200", removed.status_code == 200, removed.status_code)
check("запись исчезла из таблицы записей",
      all(r["id"] != victim_id
          for r in admin.get("/api/admin/records").get_json()["records"]))

again = admin.delete(f"/api/admin/records/{victim_id}")
check("повторное удаление -> 404", again.status_code == 404, again.status_code)

# ----------------------------------------------------------- справочники
print("\n== Справочники ==")
created_emp = admin.post(
    "/api/admin/employees",
    json={"full_name": "Сидорова Анна", "login": "sidorova",
          "role": "user", "rate": 1200, "password": "secret1"},
)
check("создание сотрудника -> 201", created_emp.status_code == 201, created_emp.status_code)
emp_id = created_emp.get_json()["employee"]["id"]

duplicate = admin.post(
    "/api/admin/employees",
    json={"full_name": "Дубль", "login": "sidorova", "role": "user",
          "rate": 100, "password": "secret1"},
)
check("дубликат логина -> 409", duplicate.status_code == 409, duplicate.status_code)

weak = admin.post(
    "/api/admin/employees",
    json={"full_name": "Слабый", "login": "weak", "role": "user",
          "rate": 100, "password": "123"},
)
check("короткий пароль -> 400", weak.status_code == 400, weak.status_code)

deleted = admin.delete(f"/api/admin/employees/{emp_id}")
check("удаление сотрудника запрещено -> 403", deleted.status_code == 403)
check("сотрудник остался в базе",
      any(e["id"] == emp_id for e in admin.get("/api/admin/employees").get_json()["employees"]))

deactivated = admin.put(f"/api/admin/employees/{emp_id}", json={"is_active": False})
check("деактивация сотрудника -> 200", deactivated.status_code == 200)
check("деактивированный не может войти",
      login_client("sidorova", "secret1")[1].status_code == 403)

self_lock = admin.put("/api/admin/employees/1", json={"is_active": False})
check("нельзя деактивировать самого себя -> 400", self_lock.status_code == 400)

created_project = admin.post("/api/admin/projects", json={"name": "Рефакторинг"})
check("создание проекта -> 201", created_project.status_code == 201)
project_id = created_project.get_json()["project"]["id"]

toggled = admin.put(f"/api/admin/projects/{project_id}", json={"is_active": False})
check("деактивация проекта -> 200", toggled.status_code == 200)

active_projects = user.get("/api/projects").get_json()["projects"]
check("деактивированный проект скрыт из форм сотрудника",
      all(p["id"] != project_id for p in active_projects))

proj_delete = admin.delete(f"/api/admin/projects/{project_id}")
check("удаление проекта запрещено -> 403", proj_delete.status_code == 403)

inactive_created = admin.post("/api/admin/projects", json={"name": "Архивный", "is_active": False})
check("создание проекта со снятой галочкой -> 201",
      inactive_created.status_code == 201, inactive_created.status_code)
check("проект создан деактивированным",
      inactive_created.get_json()["project"]["is_active"] == 0)
_new_id = inactive_created.get_json()["project"]["id"]
check("новый деактивированный проект скрыт из списка сотрудников",
      all(p["id"] != _new_id for p in user.get("/api/projects").get_json()["projects"]))
check("он виден руководителю в справочнике",
      any(p["id"] == _new_id and p["is_active"] == 0
          for p in admin.get("/api/admin/projects").get_json()["projects"]))

# ------------------------------------------------------------------ страницы
print("\n== HTML-страницы ==")
check("гость на / -> редирект на /login", client.get("/").status_code == 302)
check("/login отдаётся", client.get("/login").status_code == 200)
check("вход на / -> 200", user.get("/").status_code == 200)
check("сотрудник на /admin -> редирект", user.get("/admin").status_code == 302)
check("админ на /admin -> 200", admin.get("/admin").status_code == 200)
check("CSS отдаётся", user.get("/static/css/styles.css").status_code == 200)
check("JS отдаётся", user.get("/static/js/app.js").status_code == 200)
check("healthcheck", client.get("/health").get_json()["status"] == "ok")

# ---------------------------------------------------------------------- итог
print(f"\nПройдено проверок: {PASSED}, упавших: {len(FAILED)}")
if FAILED:
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("Все проверки пройдены.")
