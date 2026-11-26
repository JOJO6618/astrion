const form = document.querySelector('#sign-in');
const passwordField = document.querySelector('#password-field');
const password = document.querySelector('#password');
const submit = document.querySelector('#submit');
const message = document.querySelector('#message');

async function json(url, options) {
  const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...options });
  const data = await response.json();
  if (!response.ok || !data.success) throw new Error(data.error || 'The request failed.');
  return data;
}

async function loadStatus() {
  const data = await json('/api/host-mode-enabled');
  const available = data.enabled && data.config_valid;
  passwordField.hidden = !data.password_required;
  password.disabled = !available || !data.password_required;
  password.required = data.password_required;
  submit.disabled = !available;
  if (!available) message.textContent = data.error || 'Host sign-in is unavailable.';
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (submit.disabled) return;
  submit.disabled = true;
  message.textContent = '';
  try {
    const { token } = await json('/api/csrf-token');
    await json('/host-login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
      body: JSON.stringify({ password: password.value }),
    });
    password.value = '';
    window.location.assign('/new');
  } catch (error) {
    message.textContent = error.message;
    loadStatus().catch(() => { submit.disabled = false; });
  }
});

loadStatus().catch((error) => { message.textContent = error.message; });
