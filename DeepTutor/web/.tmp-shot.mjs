import { chromium } from 'playwright';

const url = process.env.URL || 'http://127.0.0.1:8000/studio/';
const browser = await chromium.launch({ executablePath: process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell' });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const errors = [];
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', e => errors.push(String(e)));
await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForTimeout(2500);
await page.screenshot({ path: '/tmp/studio-audit/wide.png' });
const layoutInfo = await page.evaluate(() => {
  const rd = document.querySelector('.studio-right-dock');
  const layout = document.querySelector('.studio-layout');
  const cs = rd ? getComputedStyle(rd) : null;
  return {
    rightDockExists: !!rd,
    rightDockWidth: cs?.width,
    rightDockDisplay: cs?.display,
    rightDockCollapsed: rd?.className,
    layoutClass: layout?.className,
    ls: {
      rightCollapsed: localStorage.getItem('studio-right-panel-collapsed'),
      leftCollapsed: localStorage.getItem('studio-left-panel-collapsed'),
      inspectorTab: localStorage.getItem('studio.inspector.tab'),
    },
  };
});
console.log(JSON.stringify(layoutInfo, null, 2));
console.log('console errors:', JSON.stringify(errors.slice(0, 10), null, 2));
await browser.close();
