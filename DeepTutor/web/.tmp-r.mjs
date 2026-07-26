import { chromium } from 'playwright';
const browser = await chromium.launch({ executablePath: process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell' });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await page.goto('http://127.0.0.1:8000/studio/', { waitUntil: 'domcontentloaded' });
await (await page.$('input')).fill(process.env.STUDIO_TOKEN);
await page.keyboard.press('Enter');
await page.waitForTimeout(2500);
await page.click('button[data-section="import"]');
await page.waitForTimeout(1500);
// 快速连点任务 + 页码，制造并发
await page.evaluate(() => {
  const items = [...document.querySelectorAll('.import-project-item')];
  (items.find(x => (x.textContent || '').includes('第二册')) || items[0])?.click();
});
await page.waitForTimeout(300);
// 连续快速触发多次 preview 事件（模拟用户连点页码）
await page.evaluate(() => {
  const jid = document.querySelector('.import-project-item') ? undefined : undefined;
});
for (const p of [2, 5, 9, 3]) {
  await page.evaluate((pageNo) => {
    document.dispatchEvent(new CustomEvent('studio:mineru-preview', { detail: { jobId: undefined } }));
  }, p).catch(() => {});
}
// 直接连发 focusMinerUJobTriPane 的底层事件（用真实 jobId）
const jobId = await page.evaluate(async () => {
  const mod = await import('/studio/assets/index.js').catch(() => null);
  return null;
});
// 简化：从 banner 文本拿不到 id，改为连点任务卡片 5 次
for (let i = 0; i < 5; i++) {
  await page.evaluate(() => {
    const items = [...document.querySelectorAll('.import-project-item')];
    (items.find(x => (x.textContent || '').includes('第二册')) || items[0])?.click();
  });
  await page.waitForTimeout(120);
}
await page.waitForTimeout(5000);
const info = await page.evaluate(() => ({
  previewRoots: document.querySelectorAll('.import-line-mineru-preview').length,
  previewWraps: document.querySelectorAll('.import-preview-wrap').length,
  openBars: document.querySelectorAll('.import-open-textbook-bar').length,
  panelsChildren: [...(document.querySelector('.import-line-panels')?.children || [])].length,
}));
console.log(JSON.stringify(info));
await page.screenshot({ path: '/tmp/studio-audit/race.png' });
await browser.close();
