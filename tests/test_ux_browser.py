"""Opt-in real-browser checks: set KIDSWATCH_PLAYWRIGHT_MODULE to playwright-core.

Uses only the local dev server's fake Plex data. No production accounts or services.
"""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest


@pytest.mark.skipif(not os.environ.get('KIDSWATCH_PLAYWRIGHT_MODULE'),
                    reason='Set KIDSWATCH_PLAYWRIGHT_MODULE to run Chromium interaction checks.')
def test_batch_undo_and_expired_request_draft_in_chromium(tmp_path):
    node, chromium = shutil.which('node'), shutil.which('chromium')
    assert node and chromium, 'Chromium and Node are required for the browser checks.'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    server = subprocess.Popen([sys.executable, '-c',
        'from tests.dev_server import app; import uvicorn; '
        f'uvicorn.run(app, host="127.0.0.1", port={port}, log_level="error")'],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            if server.poll() is not None:
                pytest.fail(server.stderr.read().decode())
            try:
                urlopen(base + '/healthz', timeout=1).close()
                break
            except URLError:
                time.sleep(.1)
        else:
            pytest.fail('The fake-data server did not become ready.')
        script = tmp_path / 'interactions.cjs'
        script.write_text(r'''
const {chromium} = require(process.env.KIDSWATCH_PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({executablePath: process.env.UX_CHROMIUM,
    headless: true, args: ['--no-sandbox']});
  try {
    const context = await browser.newContext({viewport: {width: 390, height: 900}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const base = process.env.UX_BASE;
    await page.goto(base + '/dev-login?u=alex&next=/kids/1/titles');
    const choices = () => page.locator('.tile').evaluateAll(ts => ts.map(t => [t.dataset.key, t.dataset.choice]));
    const before = await choices();
    const batch = async () => {
      await page.locator('summary').filter({hasText: 'Change all'}).click();
      await page.locator('[data-bulk=block]').click();
      await page.locator('[data-confirm-yes]').click();
      await page.locator('.toast-action').waitFor();
    };
    await batch();
    await page.locator('.toast-action').focus();
    await page.waitForTimeout(9000);
    assert.equal(await page.locator('.toast-action').isVisible(), true);
    await page.locator('.toast-action').click();
    await page.waitForFunction(expected => JSON.stringify(Array.from(document.querySelectorAll('.tile'),
      t => [t.dataset.key, t.dataset.choice])) === JSON.stringify(expected), before);
    await batch();
    await page.locator('[data-toggle]').first().click();
    assert.equal(await page.locator('.toast:not(.hide) .toast-action').count(), 0);
    assert.match(await page.locator('[data-toggle]').first().getAttribute('aria-label'), / for Kid1$/);
    await page.goto(base + '/requests');
    await page.locator('[name=kind][value=more_access]').check();
    await page.locator('[name=kid]').selectOption('1');
    await page.locator('[name=message]').fill('Please let Sam help manage Kid1.');
    const renew = await context.newPage();
    await renew.goto(base + '/dev-login?u=alex&next=/home');
    await renew.close();
    await page.getByRole('button', {name: 'Send to Admin', exact: true}).click();
    await page.getByRole('heading', {name: 'This form expired'}).waitFor();
    await page.getByRole('link', {name: 'Return to your request'}).click();
    await page.getByRole('button', {name: 'Restore draft', exact: true}).click();
    assert.equal(await page.locator('[name=message]').inputValue(), 'Please let Sam help manage Kid1.');
    assert.equal(await page.locator('[name=kid]').inputValue(), '1');
    assert.equal(await page.locator('[name=kind]:checked').inputValue(), 'more_access');
    await page.getByRole('button', {name: 'Send to Admin', exact: true}).click();
    await page.waitForLoadState('networkidle');
    assert.equal(await page.evaluate(() => sessionStorage.getItem('kidswatch-request-draft-101')), null);
    await page.locator('[name=message]').fill('Another draft');
    await page.reload();
    await page.getByRole('button', {name: 'Discard draft', exact: true}).click();
    assert.equal(await page.evaluate(() => sessionStorage.getItem('kidswatch-request-draft-101')), null);
    await page.locator('[name=message]').fill('Sign-out draft');
    await page.getByRole('button', {name: 'Sign out', exact: true}).click();
    await page.waitForLoadState('networkidle');
    assert.equal(await page.evaluate(() => sessionStorage.getItem('kidswatch-request-draft-101')), null);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
''')
        result = subprocess.run([node, str(script)], timeout=60, capture_output=True, text=True,
                                env={**os.environ, 'UX_BASE': base, 'UX_CHROMIUM': chromium})
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.terminate()
        try:
            server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate()
