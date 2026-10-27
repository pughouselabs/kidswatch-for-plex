// Pairing secrets remain in this one no-store document, never browser storage.
(() => {
  const panel = document.querySelector('[data-phone-pairing]');
  if (!panel) return;
  const status = panel.querySelector('[data-phone-status]');
  const expires = Date.parse(panel.dataset.expires);
  const hideSecret = message => {
    panel.querySelector('figure')?.remove();
    panel.querySelector('.phone-code')?.remove();
    status.textContent = message;
  };
  const timer = setInterval(async () => {
    const seconds = Math.max(0, Math.ceil((expires - Date.now()) / 1000));
    if (!seconds) {
      clearInterval(timer);
      hideSecret('This code expired. Create a new code.');
      return;
    }
    status.textContent = `Expires in ${seconds} seconds.`;
    if (seconds % 5 === 0 && !document.hidden) {
      try {
        const response = await fetch('/mobile/pairings/status', {cache: 'no-store', redirect: 'error'});
        if (!response.ok) return;
        const result = await response.json();
        if (result.state !== 'open') {
          clearInterval(timer);
          hideSecret(result.state === 'used' ? 'Phone connected. Review it in Connected phones.' : 'This code expired. Create a new code.');
        }
      } catch (_) { /* Keep the visible expiry even if status is unreachable. */ }
    }
  }, 1000);
  panel.querySelector('[data-phone-cancel]')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form), cache: 'no-store', redirect: 'error'});
      if (response.ok) {
        clearInterval(timer);
        hideSecret('Connection code cancelled.');
      } else status.textContent = 'Could not cancel this code. Try again.';
    } catch (_) { status.textContent = 'Could not cancel this code. Try again.'; }
  });
})();
