// Run with node; uses the installed Playwright package and system Chromium.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
const { chromium } = await import(path.join(os.homedir(), '.local/share/mise/installs/npm-playwright/latest/node_modules/playwright/index.mjs'));
const origin = process.env.SITE_ORIGIN || 'http://127.0.0.1:8789';
const screenshotDir = process.env.SITE_SCREENSHOTS || '/tmp/kidswatch-unified-qa';
await fs.mkdir(screenshotDir, { recursive: true });
const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', headless: true });
let variants = 0, interactions = 0;
try {
  for (const width of [1280, 390]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: 950 }, colorScheme, permissions: ['clipboard-read', 'clipboard-write'] });
    for (const route of ['/', '/plex/', '/plex/parents', '/plex/server-owners', '/jellyfin/', '/jellyfin/parents', '/jellyfin/server-owners']) {
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(String(error)));
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
      const response = await page.goto(origin + route);
      assert.equal(response.status(), 200, route);
      assert.match(response.headers()['content-security-policy'], /script-src 'self'/);
      const noOverflow = async () => {
        const size = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
        assert(size[0] <= size[1], `${route} ${width} ${colorScheme}: overflow ${size}`);
      };
      await noOverflow();
      await page.screenshot({ path: path.join(screenshotDir, `${route.replaceAll('/', '_') || 'chooser'}-${width}-${colorScheme}-hero.png`) });
      // Each edition loads exactly one common stylesheet and no inline scripts/styles.
      if (route !== '/') {
        assert.deepEqual(await page.locator('link[rel="stylesheet"]').evaluateAll(nodes => nodes.map(n => n.getAttribute('href'))), ['/style.css']);
        // Chromium's screenshot helper may leave an empty style attribute on inputs.
        assert.equal(await page.locator('script:not([src]), [style]:not([style=""]), style').count(), 0);
        const brand = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--brand').trim());
        assert.equal(brand, route.includes('jellyfin') ? (colorScheme === 'light' ? '#5b3fd1' : '#b9a8ff') : (colorScheme === 'light' ? '#c2410c' : '#ff7a33'));
      }
      const demo = page.locator('[data-level-demo]');
      if (await demo.count()) {
        const tile = demo.locator('li[data-rating="G"]');
        await tile.locator('button').click();
        assert.equal(await tile.locator('button').getAttribute('aria-pressed'), 'false');
        assert(await tile.evaluate(n => n.classList.contains('off')));
        await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-level-demo] li[data-rating="G"] .tile-art')).filter === 'grayscale(1)');
        const visibility = await tile.locator('.tile-art').evaluate(n => ({ filter: getComputedStyle(n).filter, opacity: getComputedStyle(n).opacity, color: getComputedStyle(n).color, rect: n.getBoundingClientRect().width }));
        assert.equal(visibility.filter, 'grayscale(1)');
        assert.equal(visibility.opacity, '1');
        assert.equal(visibility.color, 'rgb(255, 255, 255)');
        assert(visibility.rect > 0);
        await tile.locator('button').click();
        assert.equal(await tile.locator('button').getAttribute('aria-pressed'), 'true');
        await demo.locator('[data-only-allowed]').check();
        assert.equal(await demo.locator('.tiles li:not(.off)').count(), 0);
        await tile.locator('button').click();
        assert.equal(await demo.locator('.tiles li:not(.off)').count(), 1);
        await demo.locator('[data-only-allowed]').uncheck();
        await demo.locator('[data-level="teens"]').click();
        assert.equal(await demo.locator('.tiles li.off').count(), 0);
        await demo.locator('[data-level="little"]').click();
        assert(await demo.locator('.tiles li.off').count() > 0);
        interactions += 8;
      }
      if (await page.locator('[data-parent-demo]').count()) {
        await page.locator('[data-demo-pause] summary').click();
        await page.locator('[data-demo-action="pause:30"]').click();
        assert.equal(await page.locator('[data-demo-badge]').textContent(), 'Paused');
        await page.locator('[data-demo-undo]').click();
        assert.equal(await page.locator('[data-demo-badge]').textContent(), 'Watching');
        await page.locator('[data-demo-add] summary').click();
        await page.locator('[data-demo-action="bonus:15"]').click();
        assert.equal(await page.locator('[data-demo-meter]').getAttribute('max'), '135');
        interactions += 3;
      }
      if (await page.locator('.admin-demo').count()) {
        await page.locator('[data-demo-approve]').click();
        assert.equal(await page.locator('[data-stat="active"]').textContent(), '3');
        await page.locator('#tab-security').click();
        assert(await page.locator('[data-approval-log]').isVisible());
        await page.locator('#tab-kids').focus();
        await page.keyboard.press('ArrowRight');
        assert.equal(await page.locator('#tab-parents').getAttribute('aria-selected'), 'true');
        await page.locator('[data-copy-target="ai-prompt"]').click();
        assert(await page.locator('.prompt-disclosure').evaluate(n => n.open));
        const clipboard = await page.evaluate(() => navigator.clipboard.readText());
        assert(clipboard.includes(route.includes('jellyfin') ? 'KidsWatch for Jellyfin preview' : 'Help me install KidsWatch for Plex'));
        interactions += 4;
      }
      if (await page.locator('[data-share-url]').count()) {
        await page.locator('[data-share-url]').click();
        const copied = await page.evaluate(() => navigator.clipboard.readText());
        assert(copied.includes(route.includes('jellyfin') ? '/jellyfin/server-owners' : '/plex/server-owners'));
        interactions++;
      }
      // Check disclosures while expanded, where long prompts/tables could overflow.
      await page.locator('details:not(.pa-menu)').evaluateAll(nodes => nodes.forEach(n => { n.open = true; }));
      await noOverflow();
      assert.deepEqual(errors, [], `${route} ${width} ${colorScheme}: browser errors`);
      await page.screenshot({ path: path.join(screenshotDir, `${route.replaceAll('/', '_') || 'chooser'}-${width}-${colorScheme}.png`), fullPage: true });
      variants++;
      await page.close();
    }
    assert.equal((await context.cookies()).length, 0);
    await context.close();
  }
  console.log(JSON.stringify({ status: 'PASS', variants, interactions, checks: ['no overflow, including expanded disclosures', 'no console/JS/CSP errors', 'shared CSS and theme colors', 'tap flips and picked-only selection', 'readable grayscale tiles', 'age tabs', 'phone pause/undo/add time', 'owner approval and keyboard tabs', 'prompt clipboard copy', 'share copy', 'no cookies'], screenshots: screenshotDir }, null, 2));
} finally {
  await browser.close();
}
