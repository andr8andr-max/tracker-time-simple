'use strict';

/* =========================================================================
   Экран сотрудника: таймер, ручной ввод, список записей за сегодня.
   Изоляция данных: фронтенд вообще не передаёт user_id — сервер берёт
   владельца записей из сессионной куки.
   ========================================================================= */

const state = {
  user: null,
  projects: [],
  records: [],
  running: null,
  timerId: null,
};

const $ = (id) => document.getElementById(id);

/* --------------------------------------------------------------- отрисовка */

function fillProjectSelect(select, projects, placeholder) {
  clearNode(select);
  select.append(el('option', { value: '', text: placeholder }));
  for (const project of projects) {
    select.append(el('option', { value: project.id, text: project.name }));
  }
}

/** Перезаполняет список проектов, сохраняя уже выбранный (если он ещё активен). */
function refillProjectSelect(select, placeholder) {
  const previous = select.value;
  fillProjectSelect(select, state.projects, placeholder);
  select.value = previous; // проект деактивирован → вернётся пустой плейсхолдер
}

function recordSubtitle(record) {
  const parts = [
    record.project_name || 'Без проекта',
    `${record.start_time} – ${record.end_time || '…'}`,
  ];
  // Итоговый перерыв: ручной перерыв + все паузы таймера.
  // Для незавершённой записи считается на лету — растёт вместе с паузой.
  const breakMin = record.status === 'running'
    ? liveBreakMinutes(record)
    : (record.break_min_total || record.break_min);
  if (breakMin) parts.push(`перерыв ${breakMin} мин`);
  return parts.join(' · ');
}

function recordItem(record) {
  const actions = el('div', { class: 'record-actions' });

  if (record.status === 'running') {
    actions.append(
      el('button', {
        class: 'btn btn-sm',
        type: 'button',
        text: record.is_paused ? 'Продолжить' : 'Пауза',
        onClick: (event) => togglePause(record, event.currentTarget),
      }),
      el('button', {
        class: 'btn btn-sm btn-primary',
        type: 'button',
        text: 'Завершить',
        onClick: (event) => stopTimer(record.id, event.currentTarget),
      })
    );
  }

  actions.append(
    el('button', {
      class: 'btn btn-sm btn-danger',
      type: 'button',
      text: 'Удалить',
      onClick: () => removeRecord(record.id),
    })
  );

  const isLive = record.status === 'running';
  const hours = !isLive && record.duration_hours !== null && record.duration_hours !== undefined
    ? `${formatHours(record.duration_hours)} ч`
    : `${formatElapsed(recordElapsedSeconds(record))}`;

  const statusBadge =
    record.status !== 'running'
      ? null
      : record.is_paused
        ? el('span', { class: 'badge badge-paused', text: 'на паузе' })
        : el('span', { class: 'badge badge-running', text: 'в процессе' });

  const subEl = el('div', { class: 'record-sub', text: recordSubtitle(record) });
  const hoursEl = el('span', { class: 'record-hours', text: hours });

  if (isLive) {
    // Незавершённая запись: счётчик и итоговый перерыв идут в реальном времени
    onLiveUpdate(() => {
      hoursEl.textContent = formatElapsed(recordElapsedSeconds(record));
      subEl.textContent = recordSubtitle(record);
    });
  }

  return el(
    'li',
    { class: 'record-item' },
    el(
      'div',
      { class: 'record-head' },
      el(
        'div',
        {},
        el('div', { class: 'record-task', text: record.task }),
        subEl,
        record.notes ? el('div', { class: 'record-sub', text: `Примечание: ${record.notes}` }) : null,
        statusBadge
      ),
      hoursEl
    ),
    actions
  );
}

function renderToday() {
  const list = $('todayList');
  clearNode(list);

  if (!state.records.length) {
    list.append(el('div', { class: 'empty', text: 'За сегодня записей пока нет' }));
    $('todayTotal').textContent = '';
    return;
  }

  // «Итого» в реальном времени: завершённые записи + текущий работающий таймер
  const updateTotal = () => {
    const total = state.records.reduce((sum, record) => {
      if (record.duration_hours !== null && record.duration_hours !== undefined) {
        return sum + Number(record.duration_hours);
      }
      if (record.status === 'running') {
        return sum + recordElapsedSeconds(record) / 3600;
      }
      return sum;
    }, 0);
    $('todayTotal').textContent = `Итого ${formatHours(total)} ч`;
  };
  updateTotal();
  onLiveUpdate(updateTotal);

  for (const record of state.records) {
    list.append(recordItem(record));
  }
}

function renderUnfinished() {
  // Запущенная запись может быть начата вчера — она приходит из /api/records/running
  const byId = new Map();
  for (const record of state.records) {
    if (record.status === 'running') byId.set(record.id, record);
  }
  if (state.running) byId.set(state.running.id, state.running);

  const card = $('unfinishedCard');
  const list = $('unfinishedList');
  clearNode(list);

  if (!byId.size) {
    card.hidden = true;
    return;
  }
  for (const record of byId.values()) {
    list.append(recordItem(record));
  }
  card.hidden = false;
}

function renderTimer() {
  const running = state.running;
  const runningBlock = $('runningBlock');
  const idleBlock = $('idleBlock');
  const manualCard = $('manualCard');

  if (state.timerId) {
    clearInterval(state.timerId);
    state.timerId = null;
  }

  if (running) {
    runningBlock.hidden = false;
    idleBlock.hidden = true;
    manualCard.hidden = true;

    const paused = Boolean(running.is_paused);
    $('runningTitle').textContent = paused ? 'Пауза' : 'Идёт работа';
    const badge = $('runningBadge');
    badge.textContent = paused ? 'на паузе' : 'запущено';
    badge.className = paused ? 'badge badge-paused' : 'badge badge-running';
    $('pauseBtn').textContent = paused ? 'Продолжить работу' : 'Пауза';

    const meta = [
      el('strong', { text: running.task }),
      document.createTextNode(` · ${running.project_name || 'без проекта'}`),
      document.createTextNode(` · начало ${running.start_time}`),
    ];
    let pauseLive = null;
    if (paused && running.pause_started_at) {
      meta.push(
        document.createTextNode(` · пауза с ${running.pause_started_at.slice(11, 16)} · идёт `)
      );
      pauseLive = el('span', {});
      meta.push(pauseLive);
    }
    $('runningMeta').replaceChildren(...meta);

    const tick = () => {
      // Работа заморожена на паузе, а длительность самой паузы растёт
      $('elapsed').textContent = formatElapsed(recordElapsedSeconds(running));
      if (pauseLive) {
        pauseLive.textContent = formatElapsed(secondsSince(running.pause_started_at.slice(11, 16)));
      }
    };
    tick();
    state.timerId = setInterval(tick, 1000);

    // Страховка: после любых действий кнопки блока таймера должны быть активны
    $('pauseBtn').disabled = false;
    $('stopBtn').disabled = false;
  } else {
    runningBlock.hidden = true;
    idleBlock.hidden = false;
    manualCard.hidden = false;
    $('elapsed').textContent = '0:00:00';
  }
}

async function reload() {
  const [data, runningData] = await Promise.all([
    api.get(`/api/records?date=${todayISO()}`),
    api.get('/api/records/running'),
  ]);
  state.records = data.records;
  state.running = runningData.record;
  clearLiveUpdates(); // пересобираем «живые» привязки заново
  renderTimer();
  renderUnfinished();
  renderToday();
}

/** Полное обновление данных экрана: записи, таймер и справочник проектов. */
async function refreshData() {
  await Promise.all([
    reload(),
    api.get('/api/projects').then((data) => {
      state.projects = data.projects;
      refillProjectSelect($('startProject'), '— выберите проект —');
      refillProjectSelect($('mProject'), '— выберите проект —');
    }),
  ]);
}

/* ----------------------------------------------------------------- действия */

async function startWork() {
  const errorBox = $('startError');
  errorBox.hidden = true;

  const projectId = Number($('startProject').value);
  const task = $('startTask').value.trim();
  if (!projectId) {
    errorBox.textContent = 'Выберите проект';
    errorBox.hidden = false;
    return;
  }
  if (!task) {
    errorBox.textContent = 'Укажите, что вы делаете';
    errorBox.hidden = false;
    return;
  }

  const button = $('startBtn');
  setBusy(button, true, 'Запуск…');
  try {
    await api.post('/api/records/start', { project_id: projectId, task });
    $('startTask').value = '';
    await reload();
  } catch (err) {
    handleFormError(null, err, 'Не удалось запустить таймер');
    errorBox.textContent = err.message;
    errorBox.hidden = false;
  } finally {
    setBusy(button, false);
  }
}

async function stopTimer(recordId, button) {
  setBusy(button, true, null); // подпись «Завершить работу» не меняется — только блокировка
  try {
    await api.post(`/api/records/${recordId}/stop`);
    flash('success', 'Работа завершена, длительность сохранена');
    await reload();
  } catch (err) {
    handleFormError(null, err, 'Не удалось остановить таймер');
  } finally {
    setBusy(button, false); // снимает блокировку при успехе и при ошибке
  }
}

async function togglePause(record, button) {
  const action = record.is_paused ? 'resume' : 'pause';
  const label = record.is_paused ? 'Продолжение…' : 'Пауза…';
  setBusy(button, true, label);
  try {
    await api.post(`/api/records/${record.id}/${action}`);
    flash('success', record.is_paused ? 'Таймер продолжен' : 'Таймер поставлен на паузу');
    await reload(); // renderTimer перепишет подпись и снимет блокировку
  } catch (err) {
    setBusy(button, false); // возвращаем исходную подпись и активность
    handleFormError(null, err, 'Не удалось изменить состояние таймера');
  } finally {
    button.disabled = false; // кнопка не должна остаться выключенной
  }
}

async function removeRecord(recordId) {
  if (!window.confirm('Удалить эту запись? Действие нельзя отменить.')) return;
  try {
    await api.del(`/api/records/${recordId}`);
    flash('success', 'Запись удалена');
    await reload();
  } catch (err) {
    handleFormError(null, err, 'Не удалось удалить запись');
  }
}

function validateManualForm() {
  const date = $('mDate').value;
  const projectId = Number($('mProject').value);
  const task = $('mTask').value.trim();
  const start = $('mStart').value;
  const end = $('mEnd').value;
  const breakMin = $('mBreak').value === '' ? 0 : Number($('mBreak').value);
  const overnight = $('mOvernight').checked;

  if (!date) return { error: 'Укажите дату' };
  if (!projectId) return { error: 'Выберите проект' };
  if (!task) return { error: 'Укажите задачу / работу' };
  if (!start || !end) return { error: 'Укажите время начала и окончания' };

  // Клиентская валидация: окончание позже начала (иначе — режим «через полночь»)
  if (!overnight && end <= start) {
    return {
      error: 'Время окончания должно быть позже времени начала. '
        + 'Отметьте «смена через полночь», если работа идёт, например, с 22:00 до 02:00.',
    };
  }
  if (!Number.isFinite(breakMin) || breakMin < 0) {
    return { error: 'Перерыв должен быть числом от 0 и выше' };
  }

  return {
    payload: {
      work_date: date,
      project_id: projectId,
      task,
      start_time: start,
      end_time: end,
      break_min: Math.round(breakMin),
      notes: $('mNotes').value.trim(),
      overnight,
    },
  };
}

/** Сброс полей ручного ввода после успешного сохранения. */
function resetManualForm() {
  $('mDate').value = todayISO();
  $('mProject').value = '';
  $('mTask').value = '';
  $('mStart').value = '';
  $('mEnd').value = '';
  $('mBreak').value = '0';
  $('mNotes').value = '';
  $('mOvernight').checked = false;
}

async function saveManualRecord(event) {
  event.preventDefault();
  const errorBox = $('formError');
  const successBox = $('formSuccess');
  errorBox.hidden = true;
  successBox.hidden = true;

  const { error, payload } = validateManualForm();
  if (error) {
    errorBox.textContent = error;
    errorBox.hidden = false;
    return;
  }

  const button = event.target.querySelector('button[type="submit"]');
  setBusy(button, true, 'Сохранение…');
  try {
    await api.post('/api/records', payload);
    resetManualForm();
    successBox.textContent = 'Данные сохранены';
    successBox.hidden = false;
    await reload();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.hidden = false;
  } finally {
    setBusy(button, false);
  }
}

/* --------------------------------------------------------------------- init */

async function init() {
  state.user = await requireAuth(false);

  $('userLabel').textContent = state.user.full_name;
  if (state.user.role === 'admin') $('adminLink').hidden = false;

  try {
    const data = await api.get('/api/projects');
    state.projects = data.projects;
    fillProjectSelect($('startProject'), state.projects, '— выберите проект —');
    fillProjectSelect($('mProject'), state.projects, '— выберите проект —');
  } catch (err) {
    flash('error', err.message);
  }

  // Значения формы по умолчанию: сегодня и текущее время
  $('mDate').value = todayISO();
  const now = new Date();
  $('mStart').value = `${pad2(now.getHours())}:${pad2(now.getMinutes())}`;

  try {
    await reload();
  } catch (err) {
    flash('error', err.message);
  }

  // Данные на экране всегда свежие: раз в минуту и при возврате на вкладку
  scheduleAutoRefresh(refreshData);
  // Счётчик, длительность паузы и итоговый перерыв — в реальном времени
  startLiveTicks();
}

$('startBtn').addEventListener('click', startWork);
$('stopBtn').addEventListener('click', (event) => {
  if (state.running) stopTimer(state.running.id, event.currentTarget);
});
$('pauseBtn').addEventListener('click', (event) => {
  if (state.running) togglePause(state.running, event.currentTarget);
});
$('manualForm').addEventListener('submit', saveManualRecord);
// Любая правка формы скрывает сообщение «Данные сохранены»
$('manualForm').addEventListener('input', () => { $('formSuccess').hidden = true; });
$('manualForm').addEventListener('change', () => { $('formSuccess').hidden = true; });

$('mOvernight').addEventListener('change', () => {
  if ($('mOvernight').checked) {
    const box = $('formError');
    box.textContent = 'Окончание будет засчитано как следующий день (22:00 – 02:00 = 4 часа).';
    box.hidden = false;
  } else {
    $('formError').hidden = true;
  }
});

$('logoutBtn').addEventListener('click', async () => {
  try {
    await api.post('/api/auth/logout');
  } finally {
    window.location.href = '/login';
  }
});

init();
