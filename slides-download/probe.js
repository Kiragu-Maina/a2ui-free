const puppeteer = require('puppeteer-core');
(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    headless: 'new',
    defaultViewport: { width: 1920, height: 1080 },
  });
  const page = await browser.newPage();
  await page.goto('https://protocol-over-prose-a2ui.pages.dev/1', { waitUntil: 'networkidle0', timeout: 60000 });
  await new Promise(r => setTimeout(r, 3000));
  const info = await page.evaluate(() => {
    const out = { keys: Object.keys(window).filter(k => /slidev/i.test(k)) };
    try { out.slidev = !!window.__slidev__; } catch {}
    try {
      const nav = window.__slidev__?.nav;
      out.totalRaw = nav?.total;
      out.totalVal = nav?.total?.value;
      out.routes = window.__slidev__?.configs?.routes?.length;
    } catch (e) { out.err = String(e); }
    return out;
  });
  console.log(JSON.stringify(info, null, 2));
  // Try /14 and see if it 404s in app
  await page.goto('https://protocol-over-prose-a2ui.pages.dev/14', { waitUntil: 'networkidle0', timeout: 60000 });
  await new Promise(r => setTimeout(r, 2000));
  const title14 = await page.evaluate(() => document.body.innerText.slice(0, 200));
  console.log('--- /14 body ---');
  console.log(title14);
  await browser.close();
})();
