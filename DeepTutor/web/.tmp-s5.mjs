import { chromium } from 'playwright';
const browser = await chromium.launch({ executablePath: process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell' });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const errors = [];
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', e => errors.push(String(e)));
await page.goto('http://127.0.0.1:8000/studio/', { waitUntil: 'domcontentloaded' });
await (await page.$('input')).fill(process.env.STUDIO_TOKEN);
await page.keyboard.press('Enter');
await page.waitForTimeout(2500);
await page.evaluate(() => {
  [...document.querySelectorAll('button')].find(x => (x.textContent || '').trim() === '导入')?.click();
});
await page.waitForTimeout(1500);
await page.evaluate(() => {
  const items = [...document.querySelectorAll('.import-project-item')];
  const it = items.find(x => (x.textContent || '').includes('第二册')) || items[0];
  it?.click();
});
await page.waitForTimeout(6000);
await page.screenshot({ path: '/tmp/studio-audit/job-open.png' });
const info = await page.evaluate(() => {
  const editor = document.querySelector('.studio-editor');
  const slot = document.querySelector('.studio-pipeline-slot');
  const kids = slot ? [...slot.children].map(c => `${c.className}|${c.clientHeight}`) : [];
  return {
    editorH: editor?.clientHeight, slotH: slot?.clientHeight, slotKids: kids,
    text: (slot?.textContent || '').slice(0, 150),
  };
});
console.log(JSON.stringify(info, null, 1));
console.log('errors:', JSON.stringify(errors.slice(0, 8), null, 1));
await browser.close();
