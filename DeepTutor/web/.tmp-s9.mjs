import { chromium } from 'playwright';
const browser = await chromium.launch({ executablePath: process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell' });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await page.goto('http://127.0.0.1:8000/studio/', { waitUntil: 'domcontentloaded' });
await (await page.$('input')).fill(process.env.STUDIO_TOKEN);
await page.keyboard.press('Enter');
await page.waitForTimeout(2500);
await page.click('button[data-section="import"]');
await page.waitForTimeout(1500);
await page.evaluate(() => {
  const items = [...document.querySelectorAll('.import-project-item')];
  (items.find(x => (x.textContent || '').includes('第二册')) || items[0])?.click();
});
await page.waitForTimeout(5000);
const srcs = await page.evaluate(() =>
  [...document.querySelectorAll('.import-center-preview img')].slice(0, 12).map(i => ({
    src: (i.getAttribute('src') || '').slice(0, 110),
    ok: i.complete && i.naturalWidth > 0,
  }))
);
console.log(JSON.stringify(srcs, null, 1));
await browser.close();
