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
const ribbonClicked = await page.evaluate(() => {
  const btns = [...document.querySelectorAll('.studio-ribbon button, .studio-sidebar button')];
  const b = btns.find(x => (x.textContent || '').replace(/\s+/g, '') === '导入');
  if (b) { b.click(); return b.className; }
  return null;
});
await page.waitForTimeout(1500);
const state1 = await page.evaluate(() => ({
  ribbon: ribbon => ribbon,
  hub: !document.querySelector('.import-center-hub')?.classList.contains('hidden'),
  rootCls: document.querySelector('.import-pipeline-panel')?.className,
}));
console.log('ribbonClicked:', ribbonClicked, JSON.stringify(state1));
await page.evaluate(() => {
  const items = [...document.querySelectorAll('.import-project-item')];
  (items.find(x => (x.textContent || '').includes('第二册')) || items[0])?.click();
});
await page.waitForTimeout(6000);
await page.screenshot({ path: '/tmp/studio-audit/job-open2.png' });
const info = await page.evaluate(() => {
  const q = (s) => document.querySelector(s);
  return {
    rootCls: q('.import-pipeline-panel')?.className,
    hubHidden: q('.import-center-hub')?.className,
    previewCls: q('.import-center-preview')?.className,
    previewH: q('.import-center-preview')?.clientHeight,
    previewKids: [...(q('.import-line-panels')?.children || [])].map(c => `${c.className}|h=${c.clientHeight}`),
  };
});
console.log(JSON.stringify(info, null, 1));
console.log('errors:', JSON.stringify(errors.slice(0, 8), null, 1));
await browser.close();
