"""
Работа с базой данных SQLite.

Все запросы в проекте выполняются через параметризованные вызовы
cursor.execute(sql, params) — это полностью исключает SQL-инъекции.
"""

import sqlite3
from pathlib import Path

from flask import current_app, g

# Корень репозитория (backend/ -> ../)
ROOT_DIR = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT_DIR / "db" / "schema.sql"
SEED_MARKER = "-- === SEED ==="


def get_db() -> sqlite3.Connection:
    """Соединение с БД, привязанное к текущему запросу (создаётся один раз)."""
    if "db" not in g:
        db_path = current_app.config["DATABASE_PATH"]
        # Создаём родительскую папку, если задан путь вида ./var/data.db
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_exception=None) -> None:
    """Закрывает соединение по завершении запроса."""
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def query_all(sql: str, params: tuple = ()) -> list[dict]:
    """Выполняет SELECT и возвращает список словарей."""
    rows = get_db().execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    """Выполняет SELECT и возвращает одну строку (или None)."""
    row = get_db().execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def execute(sql: str, params: tuple = ()) -> int:
    """Выполняет INSERT/UPDATE/DELETE, коммитит и возвращает lastrowid."""
    conn = get_db()
    cursor = conn.execute(sql, params)
    conn.commit()
    return cursor.lastrowid


def _migrate(conn: sqlite3.Connection) -> None:
    """
    Лёгкая миграция существующих баз: добавляет новые колонки,
    если их нет. Существующие данные не трогает.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
    migrations = {
        "paused_seconds": "ALTER TABLE records ADD COLUMN paused_seconds INTEGER NOT NULL DEFAULT 0",
        "pause_started_at": "ALTER TABLE records ADD COLUMN pause_started_at TEXT",
        "started_at": "ALTER TABLE records ADD COLUMN started_at TEXT",
    }
    for column, statement in migrations.items():
        if column not in columns:
            conn.execute(statement)


def init_db(seed: bool = False) -> None:
    """
    Создаёт таблицы при первом запуске.

    seed=True — дополнительно загружает тестовые данные
    (2 пользователя и 3 проекта) из db/schema.sql.
    """
    raw = SCHEMA_FILE.read_text(encoding="utf-8")
    # Ищем маркер SEED только как отдельную строку (не внутри комментариев)
    lines = raw.splitlines(keepends=True)
    marker_index = next(
        (i for i, line in enumerate(lines) if line.strip() == SEED_MARKER), None
    )
    if marker_index is None:
        ddl_sql, seed_sql = raw, ""
    else:
        ddl_sql = "".join(lines[:marker_index])
        seed_sql = "".join(lines[marker_index + 1 :])

    # Соединение напрямую (ещё нет контекста запроса и приложения-фабрики в g)
    Path(current_app.config["DATABASE_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(current_app.config["DATABASE_PATH"])
    try:
        conn.executescript(ddl_sql)
        _migrate(conn)
        if seed and seed_sql.strip():
            conn.executescript(seed_sql)
        conn.commit()
    finally:
        conn.close()
