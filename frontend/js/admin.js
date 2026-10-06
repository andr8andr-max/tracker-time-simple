'use strict';

/* =========================================================================
   Панель руководителя: записи всех сотрудников, фильтры, сортировка,
   итоги, экспорт CSV и справочники (сотрудники, проекты).

   Данные приходят только с /api/admin/* — сервер проверяет роль admin.
   ========================================================================= */

const state = {
  user: null,
  rows: [], // записи, отвечающие текущим фильтрам
  sort: { key: 'work_date', dir: 'desc' },
  employees: [],
  projects: [],
  editingEmployeeId: null,
  editingProjectId: null,
};

const $ = (id) => document.getElementById(id);

/* Колонки таблицы записей. Длительность: (конец - начало) - перерыв/60 - пауза.
   «Перерыв (мин)» показывает итог: ручной перерыв + все паузы таймера. */
const RECORD_COLUMNS = [
  { key: 'user_name', label: 'ФИО' },
  { key: 'work_date', label: 'Дата' },
  { key: 'project_name', label: 'Проект' },
  { key: 'task', label: 'Задача', wrap: true },
  { key: 'start_time', label: 'Начало' },
  { key: 'end_time', label: 'Конец' },
  { key: 'break_min_total', label: 'Перерыв (мин)', numeric: true },
  {
    key: 'duration_hours',
    label: 'Длительность (часы)',
    numeric: true,
    decimal: true,
    format: (value) => (value === null || value === undefined ? '—' : formatHours(value)),
  },
  { key: 'pay', label: 'К оплате, ₽', numeric: true, decimal: true, format: (value) => formatMoney(value) },
  { key: 'notes', label: 'Примечания', wrap: true },
];

/* ------------------------------------------------------------------- вкладки */

const TABS = [
  { button: 'tabRecords', panel: 'panelRecords' },
  { button: 'tabEmployees', panel: 'panelEmployees' },
  { button: 'tabProjects', panel: 'panelProjects' },
];

function selectTab(selectedButtonId) {
  for (const tab of TABS) {
    const active = tab.button === selectedButtonId;
    $(tab.button).setAttribute('aria-selected', String(active));
    $(tab.panel).hidden = !active;
  }
}

for (const tab of TABS) {
  $(tab.button).addEventListener('click', () => selectTab(tab.button));
}

/* --------------------------------------------------- записи: фильтры и таблица */

function compareRows(a, b, column) {
  const left = a[column.key];
  const right = b[column.key];
  if (left === null || left === undefined) return right === null || right === undefined ? 0 : 1;
  if (right === null || right === undefined) return -1;
  if (column.numeric) return Number(left) - Number(right);
  return String(left).localeCompare(String(right), 'ru');
}

function sortedRows() {
  const column = RECORD_COLUMNS.find((item) => item.key === state.sort.key) || RECORD_COLUMNS[0];
  const direction = state.sort.dir === 'asc' ? 1 : -1;
  return [...state.rows].sort((a, b) => compareRows(a, b, column) * direction);
}

function toggleSort(key) {
  if (state.sort.key === key) {
    state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
  } else {
    state.sort = { key, dir: 'asc' };
  }
  renderRecords();
}

function renderRecords() {
  const rows = sortedRows();
  clearLiveUpdates(); // пересобираем «живые» привязки заново
  const table = $('recordsTable');
  clearNode(table);
  table.append(el('caption', { class: 'hidden', text: 'Таблица трудозатрат за выбранный период' }));

  /* Шапка со стрелками сортировки */
  const headerCells = RECORD_COLUMNS.map((column) => {
    const active = state.sort.key === column.key;
    const arrow = active ? (state.sort.dir === 'asc' ? '▲' : '▼') : '↕';
    const th = el('th', {
      class: 'sortable',
      scope: 'col',
      'aria-sort': active ? (state.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none',
      onClick: () => toggleSort(column.key),
      title: `Сортировать по «${column.label}»`,
    });
    th.append(document.createTextNode(`${column.label} `));
    th.append(el('span', { class: 'sort-arrow', text: arrow }));
    return th;
  });
  headerCells.push(el('th', { scope: 'col', text: 'Действия' }));
  table.append(el('thead', {}, el('tr', {}, headerCells)));

  /* Тело таблицы: только textContent — пользовательский ввод не попадает в HTML */
  const body = el('tbody');
  for (const row of rows) {
    const cells = RECORD_COLUMNS.map((column) => {
      const raw = row[column.key];
      const text = column.format ? column.format(raw) : (raw === null || raw === undefined ? '—' : raw);
      return el('td', { class: column.wrap ? 'wrap' : '', text: text === '' ? '—' : text });
    });
    if (row.status === 'paused') {
      cells[5] = el('td', {}, el('span', { class: 'badge badge-paused', text: 'пауза' }));
    } else if (row.status === 'running') {
      cells[5] = el('td', {}, el('span', { class: 'badge badge-running', text: 'идёт' }));
    }

    // Незавершённые записи: перерыв и длительность идут в реальном времени
    // (считаются на клиенте из загруженных данных, без запросов к серверу)
    if (row.status !== 'done') {
      const breakCell = cells[6];
      const durationCell = cells[7];
      onLiveUpdate(() => {
        const liveBreak = liveBreakMinutes(row);
        row.break_min_total = liveBreak;
        breakCell.textContent = String(liveBreak);
        durationCell.textContent = formatHours(recordElapsedSeconds(row) / 3600);
      });
    }

    const actions = el(
      'div',
      { class: 'actions-cell' },
      el('button', {
        class: 'btn btn-sm btn-danger', type: 'button', text: 'Удалить',
        onClick: () => removeAdminRecord(row),
      })
    );

    body.append(el('tr', {}, [...cells, el('td', {}, actions)]));
  }
  table.append(body);

  $('recordsEmpty').hidden = rows.length > 0;
  renderTotals(rows);
}

function renderTotals(rows) {
  const box = $('totalsBox');
  if (!rows.length) {
    box.hidden = true;
    return;
  }
  const hours = rows.reduce((sum, row) => sum + (Number(row.duration_hours) || 0), 0);
  const pay = rows.reduce((sum, row) => sum + (Number(row.pay) || 0), 0);
  $('totalsHours').textContent = `Итого: ${formatHours(hours)} ч`;
  $('totalsPay').textContent = `Итого к оплате: ${formatMoney(pay)} ₽`;
  box.hidden = false;
}

async function removeAdminRecord(row) {
  if (!window.confirm('Удалить запись? Действие нельзя отменить.')) return;
  try {
    await api.del(`/api/admin/records/${row.id}`);
    flash('success', 'Запись удалена');
    await loadRecords();
  } catch (err) {
    flash('error', err.message);
  }
}

async function loadRecords() {
  const params = new URLSearchParams();
  if ($('fFrom').value) params.set('date_from', $('fFrom').value);
  if ($('fTo').value) params.set('date_to', $('fTo').value);
  if ($('fUser').value) params.set('user_id', $('fUser').value);
  if ($('fProject').value) params.set('project_id', $('fProject').value);

  const query = params.toString();
  const data = await api.get(`/api/admin/records${query ? `?${query}` : ''}`);
  state.rows = data.records;
  renderRecords();
}

/* --------------------------------------------------------------- экспорт CSV */

/** Экранирование значения по RFC 4180. */
function csvCell(value) {
  let text = value === null || value === undefined ? '' : String(value);
  text = text.replace(/"/g, '""');
  return /[";\n]/.test(text) ? `"${text}"` : text;
}

/** Разделитель «;» и запятая как десятичный разделитель — корректно открывается в Excel (RU). */
function buildCsv(rows) {
  const header = RECORD_COLUMNS.map((column) => csvCell(column.label)).join(';');
  const lines = rows.map((row) =>
    RECORD_COLUMNS.map((column) => {
      let value = row[column.key];
      if (value === null || value === undefined) value = '';
      if (column.decimal && value !== '') value = Number(value).toFixed(2).replace('.', ',');
      return csvCell(value);
    }).join(';')
  );
  return [header, ...lines].join('\r\n');
}

function exportCsv() {
  const rows = sortedRows();
  if (!rows.length) {
    flash('error', 'Нет данных для экспорта');
    return;
  }
  const blob = new Blob(['\uFEFF' + buildCsv(rows)], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = el('a', { href: url, download: `time-report_${todayISO()}.csv` });
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  flash('success', `Экспортировано записей: ${rows.length}`);
}

/* ------------------------------------------------------------ справочники */

function fillSelect(select, items, allLabel, labelFn) {
  clearNode(select);
  select.append(el('option', { value: '', text: allLabel }));
  for (const item of items) {
    select.append(el('option', { value: item.id, text: labelFn(item) }));
  }
}

async function loadReferenceData() {
  // Выбранные фильтры переживают перезаполнение селектов — иначе
  // авто-обновление раз в минуту сбрасывало бы выбор и перезапускало фильтр
  const keepUser = $('fUser').value;
  const keepProject = $('fProject').value;

  const [employeesData, projectsData] = await Promise.all([
    api.get('/api/admin/employees'),
    api.get('/api/admin/projects'),
  ]);
  state.employees = employeesData.employees;
  state.projects = projectsData.projects;

  fillSelect($('fUser'), state.employees, '— Все сотрудники —',
    (item) => `${item.full_name}${item.is_active ? '' : ' (отключён)'}`);
  fillSelect($('fProject'), state.projects, '— Все проекты —',
    (item) => `${item.name}${item.is_active ? '' : ' (отключён)'}`);
  $('fUser').value = keepUser;
  $('fProject').value = keepProject;

  renderEmployees();
  renderProjects();
}

/** Свежие данные панели: справочники (с сохранением фильтров) + записи. */
async function refreshAdminData() {
  await loadReferenceData();
  await loadRecords();
}

/* --- сотрудники ----------------------------------------------------------- */

function renderEmployees() {
  const table = $('employeesTable');
  clearNode(table);

  const headers = ['ФИО', 'Логин', 'Роль', 'Ставка (руб/час)', 'Статус', 'Записей', 'Действия'];
  table.append(el('thead', {}, el('tr', {}, headers.map((label) => el('th', { scope: 'col', text: label })))));

  const body = el('tbody');
  for (const employee of state.employees) {
    const actions = el(
      'div',
      { class: 'actions-cell' },
      el('button', {
        class: 'btn btn-sm', type: 'button', text: 'Изменить',
        onClick: () => startEditEmployee(employee),
      }),
      el('button', {
        class: `btn btn-sm ${employee.is_active ? 'btn-danger' : 'btn-primary'}`,
        type: 'button',
        text: employee.is_active ? 'Деактивировать' : 'Активировать',
        onClick: () => toggleEmployee(employee),
      }),
      el('button', {
        class: 'btn btn-sm btn-danger', type: 'button', text: 'Удалить',
        onClick: () => tryDeleteEmployee(employee),
      })
    );

    body.append(
      el(
        'tr',
        {},
        el('td', { class: 'wrap', text: employee.full_name }),
        el('td', { text: employee.login }),
        el('td', {}, el('span', {
          class: employee.role === 'admin' ? 'badge badge-admin' : 'badge',
          text: employee.role === 'admin' ? 'Руководитель' : 'Сотрудник',
        })),
        el('td', { text: formatMoney(employee.rate) }),
        el('td', {}, el('span', {
          class: employee.is_active ? 'badge badge-running' : 'badge badge-off',
          text: employee.is_active ? 'активен' : 'отключён',
        })),
        el('td', { text: String(employee.record_count) }),
        el('td', {}, actions)
      )
    );
  }
  table.append(body);
}

function resetEmployeeForm() {
  state.editingEmployeeId = null;
  $('employeeForm').reset();
  $('eActive').checked = true;
  $('eRate').value = '0';
  $('employeeFormTitle').textContent = 'Добавить сотрудника';
  $('employeeSubmit').textContent = 'Создать';
  $('employeeCancel').hidden = true;
  $('employeeError').hidden = true;
}

function startEditEmployee(employee) {
  state.editingEmployeeId = employee.id;
  $('eFullName').value = employee.full_name;
  $('eLogin').value = employee.login;
  $('eRole').value = employee.role;
  $('eRate').value = String(employee.rate);
  $('ePassword').value = '';
  $('eActive').checked = Boolean(employee.is_active);
  $('employeeFormTitle').textContent = `Редактирование: ${employee.full_name}`;
  $('employeeSubmit').textContent = 'Сохранить';
  $('employeeCancel').hidden = false;
  $('employeeError').hidden = true;
  $('eFullName').focus();
}

function validateEmployeeForm(isCreate) {
  const fullName = $('eFullName').value.trim();
  const login = $('eLogin').value.trim();
  const password = $('ePassword').value;
  const rate = Number($('eRate').value || 0);

  if (!fullName) return { error: 'Укажите ФИО' };
  if (login.length < 2 || /\s/.test(login)) return { error: 'Логин: минимум 2 символа, без пробелов' };
  if (isCreate && password.length < 6) return { error: 'Пароль должен быть не короче 6 символов' };
  if (!isCreate && password && password.length < 6) return { error: 'Новый пароль слишком короткий' };
  if (!Number.isFinite(rate) || rate < 0) return { error: 'Ставка должна быть числом от 0' };

  return {
    payload: {
      full_name: fullName,
      login,
      role: $('eRole').value,
      rate,
      password: password || undefined,
      is_active: $('eActive').checked,
    },
  };
}

async function saveEmployee(event) {
  event.preventDefault();
  const errorBox = $('employeeError');
  errorBox.hidden = true;
  const isCreate = state.editingEmployeeId === null;

  const { error, payload } = validateEmployeeForm(isCreate);
  if (error) {
    errorBox.textContent = error;
    errorBox.hidden = false;
    return;
  }

  const button = $('employeeSubmit');
  setBusy(button, true, 'Сохранение…');
  try {
    if (isCreate) {
      await api.post('/api/admin/employees', payload);
      flash('success', 'Сотрудник создан');
    } else {
      await api.put(`/api/admin/employees/${state.editingEmployeeId}`, payload);
      flash('success', 'Данные сотрудника обновлены');
    }
    resetEmployeeForm();
    await loadReferenceData();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.hidden = false;
  } finally {
    setBusy(button, false);
  }
}

async function toggleEmployee(employee) {
  try {
    await api.put(`/api/admin/employees/${employee.id}`, {
      is_active: !Boolean(employee.is_active),
    });
    flash('success', employee.is_active
      ? `${employee.full_name} деактивирован(а)`
      : `${employee.full_name} снова активен(на)`);
    if (state.editingEmployeeId === employee.id) resetEmployeeForm();
    await loadReferenceData();
  } catch (err) {
    flash('error', err.message);
  }
}

async function tryDeleteEmployee(employee) {
  try {
    await api.del(`/api/admin/employees/${employee.id}`);
    flash('success', 'Сотрудник удалён');
    await loadReferenceData();
  } catch (err) {
    flash('error', err.message);
  }
}

/* --- проекты -------------------------------------------------------------- */

function renderProjects() {
  const table = $('projectsTable');
  clearNode(table);

  const headers = ['Название', 'Статус', 'Записей', 'Действия'];
  table.append(el('thead', {}, el('tr', {}, headers.map((label) => el('th', { scope: 'col', text: label })))));

  const body = el('tbody');
  for (const project of state.projects) {
    const actions = el(
      'div',
      { class: 'actions-cell' },
      el('button', {
        class: 'btn btn-sm', type: 'button', text: 'Изменить',
        onClick: () => startEditProject(project),
      }),
      el('button', {
        class: `btn btn-sm ${project.is_active ? 'btn-danger' : 'btn-primary'}`,
        type: 'button',
        text: project.is_active ? 'Деактивировать' : 'Активировать',
        onClick: () => toggleProject(project),
      }),
      el('button', {
        class: 'btn btn-sm btn-danger', type: 'button', text: 'Удалить',
        onClick: () => tryDeleteProject(project),
      })
    );

    body.append(
      el(
        'tr',
        {},
        el('td', { class: 'wrap', text: project.name }),
        el('td', {}, el('span', {
          class: project.is_active ? 'badge badge-running' : 'badge badge-off',
          text: project.is_active ? 'активен' : 'отключён',
        })),
        el('td', { text: String(project.record_count) }),
        el('td', {}, actions)
      )
    );
  }
  table.append(body);
}

function resetProjectForm() {
  state.editingProjectId = null;
  $('projectForm').reset();
  $('pActive').checked = true;
  $('projectFormTitle').textContent = 'Добавить проект';
  $('projectSubmit').textContent = 'Создать';
  $('projectCancel').hidden = true;
  $('projectError').hidden = true;
}

function startEditProject(project) {
  state.editingProjectId = project.id;
  $('pName').value = project.name;
  $('pActive').checked = Boolean(project.is_active);
  $('projectFormTitle').textContent = `Редактирование: ${project.name}`;
  $('projectSubmit').textContent = 'Сохранить';
  $('projectCancel').hidden = false;
  $('projectError').hidden = true;
  $('pName').focus();
}

async function saveProject(event) {
  event.preventDefault();
  const errorBox = $('projectError');
  errorBox.hidden = true;

  const name = $('pName').value.trim();
  if (!name) {
    errorBox.textContent = 'Введите название проекта';
    errorBox.hidden = false;
    return;
  }

  const payload = { name, is_active: $('pActive').checked };
  const button = $('projectSubmit');
  setBusy(button, true, 'Сохранение…');
  try {
    if (state.editingProjectId === null) {
      await api.post('/api/admin/projects', payload);
      flash('success', 'Проект добавлен');
    } else {
      await api.put(`/api/admin/projects/${state.editingProjectId}`, payload);
      flash('success', 'Проект обновлён');
    }
    resetProjectForm();
    await loadReferenceData();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.hidden = false;
  } finally {
    setBusy(button, false);
  }
}

async function toggleProject(project) {
  try {
    await api.put(`/api/admin/projects/${project.id}`, { is_active: !Boolean(project.is_active) });
    flash('success', project.is_active ? 'Проект деактивирован' : 'Проект активирован');
    if (state.editingProjectId === project.id) resetProjectForm();
    await loadReferenceData();
  } catch (err) {
    flash('error', err.message);
  }
}

async function tryDeleteProject(project) {
  if (!window.confirm(`Удалить проект «${project.name}»? Действие нельзя отменить.`)) return;
  try {
    await api.del(`/api/admin/projects/${project.id}`);
    flash('success', 'Проект удалён');
    await loadReferenceData();
  } catch (err) {
    flash('error', err.message);
  }
}

/* --------------------------------------------------------------------- init */

async function init() {
  state.user = await requireAuth(true);
  $('userLabel').textContent = state.user.full_name;

  try {
    await loadReferenceData();
    await loadRecords();
  } catch (err) {
    flash('error', err.message);
  }

  // Панель всегда показывает актуальные данные: раз в минуту и при возврате
  // на вкладку (в т.ч. текущее время паузы и итоговый перерыв)
  scheduleAutoRefresh(refreshAdminData);
  // Перерыв и длительность незавершённых записей — в реальном времени
  startLiveTicks();
}

$('filtersForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await loadRecords();
  } catch (err) {
    flash('error', err.message);
  }
});

$('resetFilters').addEventListener('click', async () => {
  $('fFrom').value = '';
  $('fTo').value = '';
  $('fUser').value = '';
  $('fProject').value = '';
  try {
    await loadRecords();
  } catch (err) {
    flash('error', err.message);
  }
});

$('exportCsv').addEventListener('click', exportCsv);
$('employeeForm').addEventListener('submit', saveEmployee);
$('employeeCancel').addEventListener('click', resetEmployeeForm);
$('projectForm').addEventListener('submit', saveProject);
$('projectCancel').addEventListener('click', resetProjectForm);

$('logoutBtn').addEventListener('click', async () => {
  try {
    await api.post('/api/auth/logout');
  } finally {
    window.location.href = '/login';
  }
});

init();
