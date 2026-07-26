import { chromium } from 'playwright';
const browser = await chromium.launch({ executablePath: process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell' });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const errors = [];
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', e => errors.push(String(e)));
const failed404 = [];
page.on('response', r => { if (r.status() === 404) failed404.push(r.url()); });
await page.goto('http://127.0.0.1:8000/studio/', { waitUntil: 'domcontentloaded' });
await (await page.$('input')).fill(process.env.STUDIO_TOKEN);
await page.keyboard.press('Enter');
await page.waitForTimeout(2500);
await page.click('button[data-section="import"]');
await page.waitForTimeout(2000);
await page.screenshot({ path: '/tmp/studio-audit/hub2.png' });
await page.evaluate(() => {
  const items = [...document.querySelectorAll('.import-project-item')];
  (items.find(x => (x.textContent || '').includes('第二册')) || items[0])?.click();
});
await page.waitForTimeout(6000);
await page.screenshot({ path: '/tmp/studio-audit/job-open3.png' });
const info = await page.evaluate(() => {
  const q = (s) => document.querySelector(s);
  return {
    rootCls: q('.import-pipeline-panel')?.className,
    previewH: q('.import-center-preview')?.clientHeight,
    previewKids: [...(q('.import-line-panels')?.children || [])].map(c => `${c.className}|h=${c.clientHeight}|w=${c.clientWidth}`),
    slotH: q('.studio-pipeline-slot')?.clientHeight,
  };
});
console.log(JSON.stringify(info, null, 1));
console.log('404s:', JSON.stringify(failed404, null, 1));
console.log('errors:', JSON.stringify(errors.slice(0, 5), null, 1));
await browser.close();
