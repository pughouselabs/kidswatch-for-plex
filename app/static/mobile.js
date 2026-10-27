"use strict";
// Secrets stay in the current no-store document, never browser storage/history.
(() => {
  let dispose = () => {};
  function activatePanel() {
    dispose();
    const panel = document.querySelector('[data-phone-pairing]');
    if (!panel) return;
    const status = panel.querySelector('[data-phone-status]');
    const expires = Date.parse(panel.dataset.expires);
    let polling = false;
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
      if (seconds % 5 === 0 && !document.hidden && !polling) {
        polling = true;
        try {
          const response = await fetch('/mobile/pairings/status', {cache: 'no-store', redirect: 'error'});
          if (!response.ok) return;
          const result = await response.json();
          if (result.state !== 'open') {
            clearInterval(timer);
            hideSecret(result.state === 'used' ? 'Phone connected. Review it in Connected phones.' : 'This code expired. Create a new code.');
          }
        } catch (_) { /* Retain the local expiry while the server is unreachable. */ }
        finally { polling = false; }
      }
    }, 1000);
    dispose = () => {
      clearInterval(timer);
      hideSecret('Create a new code to connect a phone.');
    };
  }
  const reportError = form => {
    let status = form.querySelector('[data-phone-error]');
    if (!status) {
      status = document.createElement('p');
      status.dataset.phoneError = '';
      status.setAttribute('role', 'alert');
      form.append(status);
    }
    status.textContent = 'Could not save this. Refresh the page and try again.';
  };
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (form.matches('[data-phone-revoke]')) {
      event.preventDefault();
      if (!window.confirm(form.dataset.phoneRevoke)) return;
      try {
        // Same-origin fetch sends an exact Origin under Referrer-Policy:no-referrer.
        const response = await fetch(form.getAttribute('action'), {method: 'POST', body: new FormData(form), cache: 'no-store', redirect: 'manual'});
        if (response.status === 303 || response.type === 'opaqueredirect') window.location.assign('/mobile/devices');
        else reportError(form);
      } catch (_) { reportError(form); }
      return;
    }
    if (!form.matches('[data-phone-create], [data-phone-cancel]')) return;
    event.preventDefault();
    const create = form.matches('[data-phone-create]');
    try {
      const response = await fetch(create ? '/mobile/pairings' : '/mobile/pairings/cancel', {
        method: 'POST', body: new FormData(form), cache: 'no-store', redirect: 'error', headers: {'Accept': 'text/html'}
      });
      if (!response.ok) { reportError(form); return; }
      if (create) {
        const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
        const next = parsed.querySelector('main');
        if (!next) { reportError(form); return; }
        document.querySelector('main').replaceWith(document.importNode(next, true));
        document.title = parsed.title;
        activatePanel();
        document.querySelector('main').focus();
      } else {
        dispose();
        document.querySelector('[data-phone-status]').textContent = 'Connection code canceled.';
      }
    } catch (_) { reportError(form); }
    finally {
      form.removeAttribute('aria-busy');
      form.querySelectorAll('button').forEach(button => { button.disabled = false; });
    }
  });
  window.addEventListener('pagehide', () => dispose());
  window.addEventListener('pageshow', event => { if (event.persisted) dispose(); });
  activatePanel();
})();
