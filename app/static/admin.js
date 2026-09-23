(() => {
  let authenticated = false;
  let initialized = false;
  let pendingResolver = null;

  const t = (key, vars={}) => window.TrackerI18n?.t ? window.TrackerI18n.t(key, vars) : key;

  function controls() { return Array.from(document.querySelectorAll('.admin-control')); }
  function manageButton() { return document.getElementById('manage-button'); }
  function dialog() { return document.getElementById('admin-dialog'); }
  function passwordInput() { return document.getElementById('admin-password'); }
  function messageEl() { return document.getElementById('admin-message'); }
  function rememberInput() { return document.getElementById('admin-remember'); }

  function render() {
    controls().forEach(el => el.classList.toggle('hidden', !authenticated));
    const btn = manageButton();
    if (btn) {
      btn.classList.toggle('admin-unlocked', authenticated);
      btn.textContent = authenticated ? t('admin_unlocked') : t('manage');
      btn.title = authenticated ? t('lock_admin') : t('unlock_admin');
    }
    document.documentElement.dataset.admin = authenticated ? '1' : '0';
    document.dispatchEvent(new CustomEvent('tracker-admin-change', {detail:{authenticated}}));
  }

  async function refreshStatus() {
    try {
      const res = await fetch('/api/admin/status', {cache:'no-store'});
      if (!res.ok) throw new Error('status');
      const data = await res.json();
      authenticated = Boolean(data.authenticated);
    } catch (_) {
      authenticated = false;
    }
    initialized = true;
    render();
    return authenticated;
  }

  function openDialog() {
    const d = dialog();
    const msg = messageEl();
    if (msg) { msg.textContent=''; msg.className='hint'; }
    if (passwordInput()) passwordInput().value='';
    if (d && !d.open) d.showModal();
    setTimeout(()=>passwordInput()?.focus(), 50);
  }

  async function ensure() {
    if (!initialized) await refreshStatus();
    if (authenticated) return true;
    openDialog();
    return new Promise(resolve => { pendingResolver = resolve; });
  }

  async function login() {
    const input = passwordInput();
    const msg = messageEl();
    const button = document.getElementById('admin-unlock-button');
    const password = input?.value || '';
    if (!password) {
      if (msg) { msg.className='hint api-error'; msg.textContent=t('admin_password_required'); }
      return false;
    }
    if (button) button.disabled=true;
    try {
      const res = await fetch('/api/admin/login', {
        method:'POST',
        headers:{'content-type':'application/json'},
        body:JSON.stringify({password, remember: rememberInput()?.checked !== false}),
      });
      const data = await res.json().catch(()=>({}));
      if (!res.ok) throw new Error(data.detail || t('admin_unlock_failed'));
      authenticated = true;
      render();
      if (msg) { msg.className='hint api-ok'; msg.textContent=t('admin_unlocked_message'); }
      setTimeout(()=>dialog()?.close(), 180);
      if (pendingResolver) { pendingResolver(true); pendingResolver=null; }
      return true;
    } catch (err) {
      authenticated = false;
      render();
      if (msg) { msg.className='hint api-error'; msg.textContent=err.message; }
      input?.focus();
      return false;
    } finally {
      if (button) button.disabled=false;
    }
  }

  async function logout() {
    try { await fetch('/api/admin/logout',{method:'POST'}); } catch (_) {}
    authenticated=false;
    render();
  }

  function handleUnauthorized(res) {
    if (res?.status === 401) {
      authenticated=false;
      render();
      openDialog();
      return true;
    }
    return false;
  }

  document.addEventListener('DOMContentLoaded', async () => {
    manageButton()?.addEventListener('click', async () => {
      if (authenticated) await logout();
      else openDialog();
    });
    document.getElementById('admin-unlock-button')?.addEventListener('click', login);
    passwordInput()?.addEventListener('keydown', ev => {
      if (ev.key === 'Enter') { ev.preventDefault(); login(); }
    });
    dialog()?.addEventListener('close', () => {
      if (pendingResolver) { pendingResolver(false); pendingResolver=null; }
    });
    await refreshStatus();
  });

  document.addEventListener('tracker-language-change', render);
  setInterval(refreshStatus, 60_000);

  window.TrackerAdmin = {
    ensure,
    refreshStatus,
    handleUnauthorized,
    get authenticated(){ return authenticated; },
  };
})();
