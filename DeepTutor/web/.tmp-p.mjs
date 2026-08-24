import { chromium } from 'playwright';
const browser = await chromium.launch({ executablePath: process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell' });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await page.goto('http://127.0.0.1:8000/studio/', { waitUntil: 'domcontentloaded' });
await (await page.$('input')).fill(process.env.STUDIO_TOKEN);
await page.keyboard.press('Enter');
await page.waitForTimeout(2500);

// 基线：未打开任务
let m = await page.evaluate(() => ({
  domNodes: document.querySelectorAll('*').length,
  heap: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : -1,
}));
console.log('baseline:', JSON.stringify(m));

// 打开导入任务预览
await page.click('button[data-section="import"]');
await page.waitForTimeout(1500);
await page.evaluate(() => {
  const items = [...document.querySelectorAll('.import-project-item')];
  (items.find(x => (x.textContent || '').includes('第二册')) || items[0])?.click();
});
// 等预览充分加载
await page.waitForTimeout(12000);

m = await page.evaluate(() => {
  const pages = document.querySelector('.import-preview-pages');
  return {
    domNodes: document.querySelectorAll('*').length,
    svg: document.querySelectorAll('svg').length,
    img: document.querySelectorAll('img').length,
    pageBlocks: document.querySelectorAll('.import-page-block').length,
    scrollH: pages?.scrollHeight,
    heap: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : -1,
    mjxContainers: document.querySelectorAll('mjx-container').length,
  };
});
console.log('after open:', JSON.stringify(m));

// 长任务测量：滚动 + resize
await page.evaluate(() => {
  window.__longTasks = [];
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) window.__longTasks.push(Math.round(e.duration));
  }).observe({ entryTypes: ['longtask'] });
});
await page.evaluate(() => {
  const sc = document.querySelector('.import-preview-pages');
  for (let i = 0; i < 30; i++) sc.scrollTop += 800;
});
await page.waitForTimeout(1500);
await page.evaluate(() => { window.dispatchEvent(new Event('resize')); });
await page.setViewportSize({ width: 1400, height: 900 });
await page.waitForTimeout(1500);
await page.setViewportSize({ width: 1600, height: 900 });
await page.waitForTimeout(1500);
const lt = await page.evaluate(() => ({
  count: window.__longTasks.length,
  total: window.__longTasks.reduce((a, b) => a + b, 0),
  max: Math.max(0, ...window.__longTasks),
  samples: window.__longTasks.slice(0, 15),
}));
console.log('longtasks during scroll+resize:', JSON.stringify(lt));
await browser.close();
