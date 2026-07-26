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
// 左侧树：展开任务，点击“5”页节点
const clicked = await page.evaluate(() => {
  const tree = document.querySelector('.studio-side-panel');
  const nodes = [...tree.querySelectorAll('*')].filter(x =>
    x.children.length === 0 && /^\s*5\s*$/.test(x.textContent || ''));
  const n = nodes[0];
  if (n) { (n.closest('[role="treeitem"], li, div, a, button') || n).click(); return n.tagName + '.' + n.className; }
  return null;
});
await page.waitForTimeout(4000);
const info = await page.evaluate(() => {
  const q = (s) => [...document.querySelectorAll(s)];
  return {
    previewWrapCount: q('.import-preview-wrap').length,
    openBarCount: q('.import-open-textbook-bar').length,
    pageBlocks: q('.import-preview-pages > *').slice(0, 8).map(e => `${e.className}|h=${e.clientHeight}`),
    scrollables: q('.import-center-preview *').filter(e => e.scrollHeight > e.clientHeight + 20 && e.clientHeight > 100)
      .map(e => `${e.className || e.tagName}|h=${e.clientHeight}|sh=${e.scrollHeight}`).slice(0, 10),
  };
});
console.log('clicked:', clicked); console.log(JSON.stringify(info, null, 1));
await page.screenshot({ path: '/tmp/studio-audit/dup2.png' });
await browser.close();
