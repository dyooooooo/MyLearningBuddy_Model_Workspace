import { chromium } from 'playwright';
const browser = await chromium.launch({ executablePath: process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell' });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const errors = [];
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', e => errors.push(String(e)));
await page.goto('http://127.0.0.1:8000/studio/', { waitUntil: 'domcontentloaded' });
const input = await page.$('input');
await input.fill(process.env.STUDIO_TOKEN);
await page.keyboard.press('Enter');
await page.waitForTimeout(2500);
// 切到导入 ribbon
await page.evaluate(() => {
  const btns = [...document.querySelectorAll('button, [role="button"]')];
  const b = btns.find(x => (x.textContent || '').includes('导入') && !((x.textContent||'').includes('分段')));
  b?.click();
});
await page.waitForTimeout(2000);
await page.screenshot({ path: '/tmp/studio-audit/import-hub.png' });
// 点第一个导入任务
const clicked = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('[class*="job"], [class*="project"], li, tr')];
  const r = rows.find(x => (x.textContent || '').includes('数学必修'));
  if (r) { r.click(); return (r.textContent || '').slice(0, 60); }
  return null;
});
await page.waitForTimeout(3500);
await page.screenshot({ path: '/tmp/studio-audit/import-task.png' });
console.log('clicked:', clicked);
console.log('errors:', JSON.stringify(errors.slice(0, 8), null, 1));
await browser.close();
