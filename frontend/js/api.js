'use strict';

/* =========================================================================
   Общие помощники: запросы к API, создание DOM-элементов, форматирование.

   Вывод данных выполняется ТОЛЬКО через textContent / createTextNode —
   это закрывает XSS-вектор (никакого innerHTML с пользовательскими данными).
   ========================================================================= */

/**
 * Базовый запрос к API с единообразной обработкой ошибок.
 * @param {string} path   путь, например '/api/records'
 * @param {object} [opts] { method, body }
 */
async function apiRequest(path, opts = {}) {
  const { method = 'GET', body } = opts;
  const init = {
    method,
    credentials: 'same-origin', // кука сессии httpOnly отправляется только своему серверу
    headers: {},
  };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, init);
  } catch (_err) {
    throw new Error('Нет связи с сервером. Проверьте интернет и обновите страницу.');
  }

  let data = null;
  try {
    data = await response.json();
  } catch (_err) {
    data = null;
  }

  if (!response.ok) {
    const error = new Error(
      (data && data.error) || `Ошибка запроса (код ${response.status})`
    );
    error.status = response.status;
    throw error;
  }
  return data;
}

const api = {
  get: (path) => apiRequest(path),
  post: (path, body) => apiRequest(path, { method: 'POST', body }),
  put: (path, body) => apiRequest(path, { method: 'PUT', body }),
  del: (path) => apiRequest(path, { method: 'DELETE' }),
};

/* --------------------------------------------------------------------- DOM */

/**
 * Создаёт DOM-элемент.
 * attrs: class, text, value, checked/disabled/selected, остальные — атрибуты,
 * ключи вида onClick — слушатели событий.
 */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.slice(0, 2) === 'on' && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'text') {
      node.textContent = String(value);
    } else if (key === 'class') {
      node.className = value;
    } else if (key === 'value') {
      node.value = value;
    } else if (key === 'checked' || key === 'disabled' || key === 'selected') {
      node[key] = Boolean(value);
    } else {
      node.setAttribute(key, value);
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/** Показывает сообщение в блоке-контейнере (type: error | success | info). */
function showMessage(container, type, text) {
  clearNode(container);
  if (!text) {
    container.hidden = true;
    return;
  }
  container.append(el('div', { class: `alert alert-${type}`, text }));
  container.hidden = false;
}

/** Короткое всплывающее сообщение поверх страницы (не сдвигает содержимое). */
function flash(type, text) {
  const box = document.getElementById('alertBox');
  if (!box) return;
  showMessage(box, type, text);
  clearTimeout(box._flashTimer);
  const ttl = type === 'error' ? 8000 : type === 'info' ? 6000 : 4000;
  box._flashTimer = setTimeout(() => {
    box.hidden = true;
    clearNode(box);
  }, ttl);
}

/** Включает/выключает кнопку. busyText === null — только блокировка, подпись не меняется. */
function setBusy(button, busy, busyText) {
  if (!button) return;
  if (busy) {
    if (busyText !== null) {
      button.dataset.label = button.textContent;
      button.textContent = busyText || 'Подождите…';
    }
    button.disabled = true;
  } else {
    button.disabled = false;
    if (button.dataset.label) {
      button.textContent = button.dataset.label;
      delete button.dataset.label;
    }
  }
}

/**
 * Периодическое обновление данных: каждые 60 секунд и сразу при возврате
 * на вкладку. Пока страница скрыта — сервер не опрашивается.
 * При истёкшей сессии (401) — редирект на страницу входа.
 * Повторяющиеся ошибки не спамят: одно и то же сообщение показывается один раз.
 * @param {() => Promise<void>} refresh — что перечитать с сервера
 * @param {number} [intervalMs]
 */
function scheduleAutoRefresh(refresh, intervalMs = 60000) {
  let inFlight = false;
  let lastError = null;

  const run = async () => {
    if (inFlight || document.visibilityState === 'hidden') return;
    inFlight = true;
    try {
      await refresh();
      lastError = null;
    } catch (err) {
      if (err && err.status === 401) {
        window.location.href = '/login';
        return;
      }
      const message = (err && err.message) || 'Не удалось обновить данные';
      if (message !== lastError) {
        lastError = message;
        flash('error', message);
      }
    } finally {
      inFlight = false;
    }
  };

  setInterval(run, intervalMs);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') run();
  });
  // Возврат на страницу через «Назад/Вперёд» (bfcache): данные могли устареть
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) run();
  });
}

/* ----------------------------------------------- обновления в реальном времени */

/**
 * Итоговый перерыв в минутах, посчитанный на клиенте из уже загруженной
 * записи: ручной перерыв + все паузы (закрытые + текущая, пока она идёт).
 * Формула повторяет backend.duration.break_minutes_total — значения
 * совпадают с сервером, но пересчитываются сразу, а не раз в минуту.
 */
function liveBreakMinutes(record) {
  const manual = Number(record.break_min) || 0;
  let paused = Number(record.paused_seconds) || 0;
  if (record.is_paused && record.pause_started_at) {
    const since = secondsSince(String(record.pause_started_at).slice(11, 16));
    paused += Math.max(0, since);
  }
  // Отсекаем, как и счётчик времени (H:MM:SS) — иначе перерыв и таймер
  // показывали бы разные минуты (например, 97 против 1:36)
  return Math.floor(manual + paused / 60);
}

/**
 * «Живые» обновления интерфейса: раз в секунду пересчитываются элементы,
 * привязанные к незавершённым записям (счётчик отработанного, итоговый
 * перерыв, длительность).
 *
 * Считается ЛОКАЛЬНО из уже загруженных данных — запросы к серверу не
 * выполняются, поэтому нагрузка пренебрежимо мала (несколько строк текста
 * в секунду). Сервер опрашивается отдельно: раз в минуту (scheduleAutoRefresh)
 * — так видны изменения, сделанные другими людьми.
 */
const liveUpdaters = [];

function clearLiveUpdates() {
  liveUpdaters.length = 0;
}

function onLiveUpdate(update) {
  liveUpdaters.push(update);
}

function startLiveTicks(intervalMs = 1000) {
  setInterval(() => {
    for (const update of liveUpdaters) {
      try {
        update();
      } catch (err) {
        // сбой одного элемента не должен останавливать остальные
      }
    }
  }, intervalMs);
}

/* ------------------------------------------------------------- форматирование */

function formatHours(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(2) : '0.00';
}

function formatMoney(value) {
  const num = Number(value) || 0;
  return num.toLocaleString('ru-RU', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function pad2(value) {
  return String(value).padStart(2, '0');
}

function todayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/** 'HH:MM' -> секунды от полуночи. */
function timeToSeconds(hhmm) {
  const [h, m] = String(hhmm || '0:0').split(':').map(Number);
  return (h || 0) * 3600 + (m || 0) * 60;
}

/** Сколько секунд прошло с 'HH:MM' до указанного момента (для таймера). */
function secondsSince(hhmm, now = new Date()) {
  const start = timeToSeconds(hhmm);
  const nowSeconds = now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds();
  let diff = nowSeconds - start;
  if (diff < 0) diff += 24 * 3600; // переход через полночь
  return diff;
}

/** 3725 -> '1:02:05' */
function formatElapsed(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}:${pad2(m)}:${pad2(s % 60)}`;
}

/**
 * Отработанные секунды записи с учётом пауз.
 * Если таймер на паузе — счётчик «замораживается» на момент постановки на паузу.
 * record.paused_seconds — уже закрытые паузы (текущий сегмент в него не входит,
 * он учитывается через pause_started_at), поэтому вычитается ровно один раз.
 */
function recordElapsedSeconds(record, now = new Date()) {
  if (!record || !record.start_time) return 0;
  const paused = Number(record.paused_seconds) || 0;
  let base = now;
  if (record.is_paused && record.pause_started_at) {
    const parsed = new Date(String(record.pause_started_at).replace(' ', 'T'));
    if (!Number.isNaN(parsed.getTime())) base = parsed;
  }
  // Точная отметка старта (с секундами): счётчик начинается с 0:00:00
  if (record.started_at) {
    const startedAt = new Date(String(record.started_at).replace(' ', 'T'));
    if (!Number.isNaN(startedAt.getTime())) {
      return Math.max(Math.round((base - startedAt) / 1000) - paused, 0);
    }
  }
  // Старые записи без отметки — прежняя формула с точностью до минуты
  return Math.max(secondsSince(record.start_time, base) - paused, 0);
}

/**
 * Проверка авторизации. При 401 — редирект на страницу входа.
 * @param {boolean} needAdmin — требовать роль admin
 * @returns {Promise<object>} данные пользователя
 */
async function requireAuth(needAdmin = false) {
  let status;
  try {
    status = await api.get('/api/auth/status');
  } catch (err) {
    showMessage(document.getElementById('alertBox'), 'error', err.message);
    throw err;
  }
  if (!status.authenticated) {
    window.location.href = '/login';
    throw new Error('Не авторизован');
  }
  if (needAdmin && status.user.role !== 'admin') {
    window.location.href = '/';
    throw new Error('Нет прав руководителя');
  }
  return status.user;
}

/** Универсальный обработчик ошибок для форм. */
function handleFormError(container, err, fallback = 'Не удалось выполнить действие') {
  const text = (err && err.message) || fallback;
  if (container) {
    showMessage(container, 'error', text);
  } else {
    flash('error', text);
  }
}
