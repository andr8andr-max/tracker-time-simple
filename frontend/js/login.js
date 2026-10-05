'use strict';

/* Страница входа: авторизация или создание первого администратора. */

const loginForm = document.getElementById('loginForm');
const setupForm = document.getElementById('setupForm');
const hint = document.getElementById('hint');

function setFormError(id, message) {
  const node = document.getElementById(id);
  node.textContent = message || '';
  node.hidden = !message;
}

async function init() {
  let status;
  try {
    status = await api.get('/api/auth/status');
  } catch (err) {
    setFormError('loginError', err.message);
    return;
  }

  if (status.authenticated) {
    window.location.href = status.user.role === 'admin' ? '/admin' : '/';
    return;
  }

  if (status.setup_required) {
    loginForm.hidden = true;
    setupForm.hidden = false;
    hint.textContent = '';
  } else {
    setupForm.hidden = true;
    loginForm.hidden = false;
    hint.textContent = 'Нет учётной записи? Попросите руководителя создать её в панели администратора.';
  }
}

loginForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  setFormError('loginError', '');

  const login = document.getElementById('login').value.trim();
  const password = document.getElementById('password').value;
  if (!login || !password) {
    setFormError('loginError', 'Введите логин и пароль');
    return;
  }

  const button = loginForm.querySelector('button[type="submit"]');
  setBusy(button, true, 'Вход…');
  try {
    const result = await api.post('/api/auth/login', { login, password });
    window.location.href = result.user.role === 'admin' ? '/admin' : '/';
  } catch (err) {
    setFormError('loginError', err.message);
    setBusy(button, false);
  }
});

setupForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  setFormError('setupError', '');

  const fullName = document.getElementById('setupFullName').value.trim();
  const login = document.getElementById('setupLogin').value.trim();
  const password = document.getElementById('setupPassword').value;
  const password2 = document.getElementById('setupPassword2').value;

  if (!fullName || !login) {
    setFormError('setupError', 'Заполните ФИО и логин');
    return;
  }
  if (password.length < 6) {
    setFormError('setupError', 'Пароль должен быть не короче 6 символов');
    return;
  }
  if (password !== password2) {
    setFormError('setupError', 'Пароли не совпадают');
    return;
  }

  const button = setupForm.querySelector('button[type="submit"]');
  setBusy(button, true, 'Создание…');
  try {
    await api.post('/api/auth/setup', { full_name: fullName, login, password });
    window.location.href = '/admin';
  } catch (err) {
    setFormError('setupError', err.message);
    setBusy(button, false);
  }
});

init();
