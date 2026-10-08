-- =============================================================
-- Схема базы данных тайм-трекера (SQLite)
-- Файл: db/schema.sql
--
-- Разделён на две части:
--   1) DDL  — структура таблиц (выполняется всегда);
--   2) SEED — тестовые данные: 2 пользователя и 3 проекта
--      (выполняется только если в .env указано SEED_DATA=true).
-- Разделитель — строка, состоящая из двух тире, тройного равно
-- и слова SEED (см. ниже, перед тестовыми данными).
-- =============================================================

PRAGMA foreign_keys = ON;

-- -------------------------------------------------------------
-- Пользователи: руководитель (admin) и сотрудники (user)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    login         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    full_name     TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user'
                        CHECK (role IN ('admin', 'user')),
    rate          REAL    NOT NULL DEFAULT 0 CHECK (rate >= 0),
    password_hash TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- -------------------------------------------------------------
-- Справочник проектов. Удаляется, только если по проекту нет
-- записей (иначе API вернёт 409); деактивация доступна всегда —
-- чтобы не ломать старые записи.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    is_active  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- -------------------------------------------------------------
-- Трудозатраты. end_time IS NULL означает, что таймер запущен.
-- pause_started_at NOT NULL означает, что таймер на паузе.
-- duration_hours пересчитывается сервером при сохранении.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id)    ON DELETE RESTRICT,
    project_id    INTEGER REFERENCES projects(id)          ON DELETE SET NULL,
    work_date     TEXT    NOT NULL,              -- YYYY-MM-DD
    task          TEXT    NOT NULL,
    start_time    TEXT    NOT NULL,              -- HH:MM
    started_at    TEXT,                          -- точный момент старта (для счётчика)
    end_time      TEXT,                          -- HH:MM, NULL = таймер запущен
    break_min     INTEGER NOT NULL DEFAULT 0 CHECK (break_min >= 0),
    notes         TEXT    NOT NULL DEFAULT '',
    duration_hours REAL,                         -- часы, 2 знака; NULL пока запущен
    paused_seconds INTEGER NOT NULL DEFAULT 0,   -- общая пауза, сек (накопленная)
    pause_started_at TEXT,                       -- начало текущей паузы, NULL если нет
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_records_user_date ON records(user_id, work_date);
CREATE INDEX IF NOT EXISTS idx_records_date      ON records(work_date);

-- === SEED ===
-- Тестовые данные (2 сотрудника и 3 проекта).
-- Пароли: admin / admin123, user / user123 (bcrypt, соль 12 раундов).

INSERT OR IGNORE INTO projects (id, name, is_active) VALUES
    (1, 'Сайт компании',      1),
    (2, 'Мобильное приложение', 1),
    (3, 'Внутренняя разработка', 1);

INSERT OR IGNORE INTO users (id, login, full_name, role, rate, password_hash, is_active) VALUES
    (1, 'admin', 'Иванов Иван Иванович', 'admin', 0,
     '$2b$12$LKKRwMyyoT6Vw4Wg/.XEnOtF.5r4r1t.YfrzxCYwTbSiv7.cAmE0y', 1),
    (2, 'user',  'Петров Пётр Петрович', 'user', 1500,
     '$2b$12$kW3WlopWDuRZZXB3HQnA9u/K7gYadPvc.e5XPkb2ybtlNzOxulsmu', 1);

-- Демо-записи: обычная смена, часть дня и смена через полночь (22:00 -> 02:00).
INSERT OR IGNORE INTO records
    (id, user_id, project_id, work_date, task, start_time, end_time, break_min, notes, duration_hours)
VALUES
    (1, 2, 1, date('now', 'localtime', '-1 day'), 'Вёрстка главной страницы',
     '09:00', '18:00', 60, 'Согласование макета', 8.00),
    (2, 2, 2, date('now', 'localtime'), 'Рефакторинг модуля авторизации',
     '14:00', '17:30', 0, '', 3.50),
    (3, 1, 3, date('now', 'localtime'), 'Ночное обслуживание сервера',
     '22:00', '02:00', 0, 'Переход через полночь', 4.00);
