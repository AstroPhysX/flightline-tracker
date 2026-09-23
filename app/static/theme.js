(() => {
  const STORAGE_KEY = 'flightline-tracker-theme-v2';
  const supported = new Set(['light', 'dark']);
  let theme = localStorage.getItem(STORAGE_KEY);
  if (!supported.has(theme)) theme = 'light';

  function apply(next, persist = true) {
    theme = supported.has(next) ? next : 'light';
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = 'dark';
    if (persist) localStorage.setItem(STORAGE_KEY, theme);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#202833' : '#0a0d12');
    updateButton();
    document.dispatchEvent(new CustomEvent('tracker-theme-change', { detail: { theme } }));
  }

  function updateButton() {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;
    btn.textContent = theme === 'dark' ? '☾' : '☀';
    btn.title = theme === 'dark' ? 'Dark mode' : 'Light mode';
    btn.setAttribute('aria-label', btn.title);
  }

  function toggle() { apply(theme === 'dark' ? 'light' : 'dark'); }

  // Apply before first paint when this script is loaded in <head>.
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = 'dark';

  window.TrackerTheme = {
    toggle,
    setTheme: apply,
    get theme() { return theme; },
  };

  document.addEventListener('DOMContentLoaded', () => {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#202833' : '#0a0d12');
    updateButton();
    document.getElementById('theme-toggle')?.addEventListener('click', toggle);
  });
})();
