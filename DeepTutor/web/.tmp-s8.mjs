import { chromium } from 'playwright';
const browser = await chromium.launch({ executablePath: process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell' });
const page = await browser.newPage({ viewport: { width: 1000, height: 900 } });
await page.goto('http://127.0.0.1:8000/studio/', { waitUntil: 'domcontentloaded' });
await (await page.$('input')).fill(process.env.STUDIO_TOKEN);
await page.keyboard.press('Enter');
await page.waitForTimeout(2500);
await page.click('button[data-section="import"]');
await page.waitForTimeout(2000);
await page.evaluate(() => {
  const items = [...document.querySelectorAll('.import-project-item')];
  (items.find(x => (x.textContent || '').includes('第二册')) || items[0])?.click();
});
await page.waitForTimeout(6000);
await page.screenshot({ path: '/tmp/studio-audit/narrow-job.png' });
const info = await page.evaluate(() => {
  const q = (s) => document.querySelector(s);
  const r = (s) => { const e = q(s); return e ? { h: e.clientHeight, w: e.clientWidth, cls: e.className } : null; };
  return {
    preview: r('.import-center-preview'),
    mineruPreview: r('.import-line-mineru-preview'),
    rightDock: r('.studio-right-dock'),
    layoutCls: q('.studio-layout')?.className,
  };
});
console.log(JSON.stringify(info, null, 1));
await browser.close();
