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
await page.waitForTimeout(4000);
// 点左侧树的第 2 页
await page.evaluate(() => {
  const nodes = [...document.querySelectorAll('.studio-side-panel *')].filter(x => (x.textContent || '').trim() === '2' && x.children.length < 3);
  nodes[0]?.click();
});
await page.waitForTimeout(4000);
const info = await page.evaluate(() => {
  const q = (s) => [...document.querySelectorAll(s)];
  const preview = document.querySelector('.import-center-preview');
  return {
    previewWrapCount: q('.import-preview-wrap').length,
    previewRootCount: q('.import-line-mineru-preview').length,
    openBarCount: q('.import-open-textbook-bar').length,
    previewChildren: preview ? [...preview.children].map(c => `${c.className}|h=${c.clientHeight}|scrollH=${c.scrollHeight}`) : [],
    wrapChain: (() => {
      const w = document.querySelector('.import-preview-wrap');
      const chain = [];
      let e = w;
      while (e && chain.length < 8) { chain.push(`${e.className||e.tagName}|h=${e.clientHeight}`); e = e.parentElement; }
      return chain;
    })(),
  };
});
console.log(JSON.stringify(info, null, 1));
await page.screenshot({ path: '/tmp/studio-audit/dup.png' });
await browser.close();
