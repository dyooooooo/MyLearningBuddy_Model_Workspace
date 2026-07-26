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
  const b = [...document.querySelectorAll('button, [role="button"]')].find(x => (x.textContent || '').trim() === '导入');
  b?.click();
});
await page.waitForTimeout(1500);
// 点击「普通高中教科书·数学必修 第二册」任务卡片
await page.evaluate(() => {
  const cards = [...document.querySelectorAll('div, li, article')].filter(x =>
    (x.textContent || '').includes('数学必修 第二册') && (x.textContent||'').length < 60);
  cards.sort((a, b) => (a.textContent||'').length - (b.textContent||'').length);
  cards[0]?.click();
});
await page.waitForTimeout(5000);
await page.screenshot({ path: '/tmp/studio-audit/job-detail.png' });
const info = await page.evaluate(() => {
  const main = document.querySelector('.studio-main');
  const editor = document.querySelector('.studio-editor');
  return {
    mainH: main?.clientHeight, editorH: editor?.clientHeight, editorScrollH: editor?.scrollHeight,
    pipelineSlotCls: document.querySelector('.studio-pipeline-slot')?.className,
    bodyText: (document.querySelector('.studio-editor')?.textContent || '').slice(0, 200),
  };
});
console.log(JSON.stringify(info, null, 1));
console.log('errors:', JSON.stringify(errors.slice(0, 8), null, 1));
await browser.close();
