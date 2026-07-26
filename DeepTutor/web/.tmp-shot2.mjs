import { chromium } from 'playwright';

const url = 'http://127.0.0.1:8000/studio/';
const token = process.env.STUDIO_TOKEN;
const widths = [1600, 1000];
const browser = await chromium.launch({ executablePath: process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell' });

for (const w of widths) {
  const page = await browser.newPage({ viewport: { width: w, height: 900 } });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  // login
  const input = await page.$('input[placeholder*="Studio"], input[type="password"], .studio-login input, input');
  if (input) {
    await input.fill(token);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(3000);
  }
  await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `/tmp/studio-audit/w${w}.png` });
  const info = await page.evaluate(() => {
    const rd = document.querySelector('.studio-right-dock');
    const layout = document.querySelector('.studio-layout');
    const cs = rd ? getComputedStyle(rd) : null;
    return {
      rightDock: !!rd, w: cs?.width, cls: rd?.className,
      layoutCls: layout?.className,
      inspectorVisible: !!document.querySelector('.studio-inspector:not(.hidden)'),
      narrow: matchMedia('(max-width: 1080px)').matches,
      inner: window.innerWidth,
    };
  });
  console.log(w, JSON.stringify(info));
  if (errors.length) console.log(w, 'errors:', errors.slice(0, 5));
  await page.close();
}
await browser.close();
