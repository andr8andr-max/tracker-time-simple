"""
Расчёт длительности смены.

Формула: (Время_окончания - Время_начала) - (Перерыв / 60) - Пауза,
результат округляется до двух знаков.

Если окончание меньше начала (22:00 -> 02:00), считаем,
что работа закончилась на следующий день (прибавляем 24 часа).
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

_TIME_FORMATS = ("%H:%M", "%H:%M:%S")
_PAUSE_FORMAT = "%Y-%m-%d %H:%M:%S"

_ZONE_CACHE: dict = {}


def _app_zone():
    """
    Часовой пояс приложения из переменной TZ (например Europe/Moscow).

    Зону берём через Python zoneinfo, а не через libc: на PaaS glibc
    зачастую игнорирует TZ и datetime.now() возвращает UTC, тогда как
    zoneinfo работает везде, где есть tzdata (в т.д. через pip-пакет).
    """
    name = os.environ.get("TZ")
    if name not in _ZONE_CACHE:
        try:
            _ZONE_CACHE[name] = ZoneInfo(name)
        except Exception:  # noqa: BLE001 — некорректная или отсутствующая зона
            _ZONE_CACHE[name] = None
    return _ZONE_CACHE[name]


def app_now() -> datetime:
    """Текущие дата и время в часовом поясе приложения."""
    zone = _app_zone()
    return datetime.now(zone) if zone else datetime.now()


def _app_now_naive() -> datetime:
    """Те же часы без пояса — для сравнений с наивными метками из БД."""
    now = app_now()
    return now.replace(tzinfo=None) if now.tzinfo else now


def to_minutes(value: str) -> int:
    """Преобразует 'HH:MM' (или 'HH:MM:SS') в количество минут от 00:00."""
    if not isinstance(value, str):
        raise ValueError("Время должно быть в формате HH:MM")
    value = value.strip()
    for fmt in _TIME_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.hour * 60 + parsed.minute
        except ValueError:
            continue
    raise ValueError(f"Некорректное время: {value!r} (ожидается HH:MM)")


def format_minutes(total_minutes: int) -> str:
    """600 -> '10:00'. Используется для значений 'сейчас'."""
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


def is_valid_date(value: str) -> bool:
    """Проверяет, что строка — корректная дата в формате YYYY-MM-DD."""
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def calculate_duration_hours(
    start_time: str, end_time: str, break_min: int, paused_seconds: int = 0
) -> float:
    """
    Считает длительность смены в часах с округлением до 2 знаков.

    Перерыв задаётся в минутах, пауза — в секундах.
    Переход через полночь поддерживается: если окончание <= началу
    (например, 22:00 и 02:00), окончание трактуется как время следующего дня.
    """
    start = to_minutes(start_time)
    end = to_minutes(end_time)

    if end <= start:
        end += 24 * 60  # смена закончилась на следующий день

    break_minutes = int(break_min or 0)
    if break_minutes < 0:
        raise ValueError("Перерыв не может быть отрицательным")

    paused_minutes = int(paused_seconds or 0) / 60
    if paused_minutes < 0:
        raise ValueError("Пауза не может быть отрицательной")

    worked_minutes = end - start - break_minutes - paused_minutes
    if worked_minutes <= 0:
        raise ValueError(
            "Перерыв или пауза превышают длительность смены — проверьте данные"
        )

    return round(worked_minutes / 60, 2)


# ---------------------------------------------------------------------------
# Пауза таймера
# ---------------------------------------------------------------------------

def now_iso() -> str:
    """Текущее локальное время в формате 'YYYY-MM-DD HH:MM:SS'."""
    return app_now().strftime(_PAUSE_FORMAT)


def parse_iso(value: str) -> datetime:
    """Разбирает сохранённую метку паузы (формат now_iso)."""
    return datetime.strptime(value, _PAUSE_FORMAT)


def pause_delta_seconds(pause_started_at: str) -> int:
    """Сколько секунд идёт текущая пауза (с момента постановки на паузу)."""
    try:
        delta = _app_now_naive() - parse_iso(pause_started_at)
    except (TypeError, ValueError):
        return 0
    return max(int(delta.total_seconds()), 0)


def total_paused_seconds(paused_seconds: int, pause_started_at: str | None) -> int:
    """
    Вся пауза записи в секундах: уже накопленная + текущий сегмент
    (если таймер прямо сейчас на паузе).
    """
    total = max(int(paused_seconds or 0), 0)
    if pause_started_at:
        total += pause_delta_seconds(pause_started_at)
    return total


def break_minutes_total(
    break_min: int, paused_seconds: int = 0, pause_started_at: str | None = None
) -> int:
    """
    Итоговый перерыв в минутах: ручной перерыв + все паузы таймера,
    включая текущую (если запись прямо сейчас на паузе).

    Секунды отсекаются (как в счётчике H:MM:SS), чтобы перерыв и таймер
    паузы всегда показывали одни и те же минуты.

    Это только отображение: длительность по-прежнему считается
    как (конец - начало) - перерыв/60 - пауза, чтобы не вычитать дважды.
    """
    manual = max(int(break_min or 0), 0)
    paused = total_paused_seconds(paused_seconds, pause_started_at) / 60
    return int(manual + paused)


def now_hhmm() -> str:
    """Текущее локальное время в формате HH:MM."""
    return app_now().strftime("%H:%M")


def today_iso() -> str:
    """Сегодняшняя дата в формате YYYY-MM-DD."""
    return app_now().strftime("%Y-%m-%d")
