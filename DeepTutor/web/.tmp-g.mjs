import { chromium } from 'playwright';
const browser = await chromium.launch({ executablePath: process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell' });
const page = await browser.newPage({ viewport: { width: 1000, height: 900 } });
await page.goto('http://127.0.0.1:8000/studio/', { waitUntil: 'domcontentloaded' });
await (await page.$('input')).fill(process.env.STUDIO_TOKEN);
await page.keyboard.press('Enter');
await page.waitForTimeout(2500);
const info = await page.evaluate(() => {
  const q = (s) => document.querySelector(s);
  return {
    rightW: q('.studio-right-dock')?.clientWidth,
    bottomVisible: !q('.studio-bottom-dock')?.classList.contains('hidden'),
    rightBodyInBottom: !!q('.studio-bottom-dock .studio-right-dock-body'),
  };
});
console.log('narrow:', JSON.stringify(info));
await browser.close();
