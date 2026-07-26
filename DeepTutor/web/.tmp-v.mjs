import { chromium } from 'playwright';
const browser = await chromium.launch({ executablePath: process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell' });

async function openStudio(w) {
  const page = await browser.newPage({ viewport: { width: w, height: 900 } });
  await page.goto('http://127.0.0.1:8000/studio/', { waitUntil: 'domcontentloaded' });
  await (await page.$('input')).fill(process.env.STUDIO_TOKEN);
  await page.keyboard.press('Enter');
  await page.waitForTimeout(2500);
  return page;
}

// 宽屏：右栏在，底部栏隐藏
let page = await openStudio(1600);
let info = await page.evaluate(() => {
  const q = (s) => document.querySelector(s);
  return {
    rightW: q('.studio-right-dock')?.clientWidth,
    rightBodyInDock: !!q('.studio-right-dock .studio-right-dock-body'),
    bottomHidden: q('.studio-bottom-dock')?.classList.contains('hidden'),
  };
});
console.log('wide(1600):', JSON.stringify(info));
await page.close();

// 窄屏：右栏隐藏，属性栏在底部
page = await openStudio(1000);
info = await page.evaluate(() => {
  const q = (s) => document.querySelector(s);
  return {
    rightW: q('.studio-right-dock')?.clientWidth,
    rightBodyInBottom: !!q('.studio-bottom-dock .studio-right-dock-body'),
    bottomVisible: !q('.studio-bottom-dock')?.classList.contains('hidden'),
    bottomH: q('.studio-bottom-dock')?.clientHeight,
    inspectorTabs: [...document.querySelectorAll('.studio-bottom-dock .inspector-tab')].map(t => t.textContent),
  };
});
console.log('narrow(1000):', JSON.stringify(info));
await page.screenshot({ path: '/tmp/studio-audit/verify-narrow.png' });
// Ctrl+` 应切到 AI 页签
await page.keyboard.press('Control+`');
await page.waitForTimeout(1200);
info = await page.evaluate(() => ({
  aiTabActive: !!document.querySelector('.studio-bottom-dock .inspector-tab.inspector-tab--active'),
  aiPaneVisible: !document.querySelector('.inspector-pane--ai')?.classList.contains('hidden'),
}));
console.log('narrow Ctrl+`:', JSON.stringify(info));
await page.screenshot({ path: '/tmp/studio-audit/verify-narrow-ai.png' });
// 窄屏打开导入任务
await page.click('button[data-section="import"]');
await page.waitForTimeout(1500);
await page.evaluate(() => {
  const items = [...document.querySelectorAll('.import-project-item')];
  (items.find(x => (x.textContent || '').includes('第二册')) || items[0])?.click();
});
await page.waitForTimeout(5000);
await page.screenshot({ path: '/tmp/studio-audit/verify-narrow-job.png' });
await page.close();
await browser.close();
