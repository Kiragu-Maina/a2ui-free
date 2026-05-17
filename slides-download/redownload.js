const puppeteer = require('puppeteer-core');
const path = require('path');

const BASE = 'https://protocol-over-prose-a2ui.pages.dev';
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const OUT_DIR = path.join(__dirname, 'slides');

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    defaultViewport: { width: 1920, height: 1080, deviceScaleFactor: 2 },
    args: ['--no-sandbox', '--font-render-hinting=none'],
  });
  const page = await browser.newPage();

  // Re-render every slide at max click state (slidev caps to actual count).
  for (let i = 1; i <= 13; i++) {
    const url = `${BASE}/${i}?clicks=99`;
    console.log(`[${i}/13] ${url}`);
    await page.goto(url, { waitUntil: 'networkidle0', timeout: 60000 });
    await new Promise(r => setTimeout(r, 2500));
    const file = path.join(OUT_DIR, `slide-${String(i).padStart(2, '0')}.png`);
    await page.screenshot({ path: file });
  }

  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
