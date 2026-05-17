const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

const BASE = 'https://protocol-over-prose-a2ui.pages.dev';
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const OUT_DIR = path.join(__dirname, 'slides');
const WIDTH = 1920;
const HEIGHT = 1080;

(async () => {
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    defaultViewport: { width: WIDTH, height: HEIGHT, deviceScaleFactor: 2 },
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--font-render-hinting=none'],
  });

  const page = await browser.newPage();

  // Probe total slide count from the runtime
  await page.goto(`${BASE}/1`, { waitUntil: 'networkidle0', timeout: 60000 });
  await new Promise(r => setTimeout(r, 1500));

  const total = await page.evaluate(() => {
    const nav = window.__slidev__?.nav;
    if (nav && nav.total) return nav.total.value ?? nav.total;
    return null;
  });
  console.log('Detected slide count:', total);

  const slideCount = total || 13;

  for (let i = 1; i <= slideCount; i++) {
    const url = `${BASE}/${i}`;
    console.log(`[${i}/${slideCount}] ${url}`);
    await page.goto(url, { waitUntil: 'networkidle0', timeout: 60000 });
    // Give slidev animations / fonts / shiki time to settle
    await new Promise(r => setTimeout(r, 2500));

    const file = path.join(OUT_DIR, `slide-${String(i).padStart(2, '0')}.png`);
    await page.screenshot({ path: file, fullPage: false, type: 'png' });
    console.log(`  -> ${file}`);
  }

  await browser.close();
  console.log('Done.');
})().catch(e => { console.error(e); process.exit(1); });
