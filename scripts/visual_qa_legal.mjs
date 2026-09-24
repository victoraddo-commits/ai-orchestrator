import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';

const base = process.env.CC_URL || 'https://127.0.0.1:18000/command-center';
const token = process.env.KAI_SESSION;
const outDir = process.env.OUT_DIR || '/tmp/opencode/ui';
const widths = (process.env.WIDTHS || '360,768,1280').split(',').map(Number);
const SK = 'kai_command_session_v3';

if (!token) { console.error('KAI_SESSION required'); process.exit(2); }
mkdirSync(outDir, { recursive: true });

const TABS = [
  ['ask', 'Ask'], ['reports', 'Reports'], ['gaps', 'Gaps'],
  ['everyday', 'Everyday Law'], ['health', 'Corpus health'], ['licences', 'Licences'],
];

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const report = { base, generated: new Date().toISOString(), panels: [], console: [], pageerrors: [] };

for (const w of widths) {
  const ctx = await browser.newContext({ viewport: { width: w, height: 900 }, deviceScaleFactor: 1, ignoreHTTPSErrors: true });
  await ctx.addInitScript(([k, v]) => { try { localStorage.setItem(k, v); } catch (e) {} },
    [SK, JSON.stringify({ token, role: 'operator', username: 'visual-qa' })]);
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error') report.console.push({ w, text: m.text().slice(0, 300) }); });
  page.on('pageerror', (e) => report.pageerrors.push({ w, text: String(e).slice(0, 300) }));
  page.on('response', (r) => { if (r.status() >= 400) report.panels.push({ tab: '__http', width: w, status: r.status(), url: r.url().slice(0, 160) }); });

  await page.goto(base + '#legal', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('#app-wrap.active', { timeout: 30000 }).catch(() => {});
  await page.waitForSelector('#legal-brain-card', { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(3500);

  for (const [tab, label] of TABS) {
    const rec = { tab, width: w, overflow: null, ready: false, error: null, empty: null };
    try {
      // click the sub-tab
      const clicked = await page.evaluate((t) => {
        const b = document.querySelector(`#legal-brain-tabs [data-lb="${t}"]`);
        if (b) { b.click(); return true; } return false;
      }, tab);
      if (!clicked) { rec.error = 'tab button not found'; report.panels.push(rec); continue; }
      // wait for the active content and for loading to finish
      for (let i = 0; i < 60; i++) {
        const st = await page.evaluate((t) => {
          const c = document.getElementById('lbp-content');
          if (!c) return { has: false };
          return { has: true, tab: c.dataset.lb, loading: !!c.querySelector('.loading'), len: (c.innerText || '').length };
        }, tab);
        if (st.has && st.tab === tab && !st.loading && st.len > 20) { rec.ready = true; break; }
        await page.waitForTimeout(300);
      }
      const d = await page.evaluate((t) => {
        const c = document.getElementById('lbp-content');
        const panel = document.getElementById('panel-legal');
        const txt = (c && c.innerText) || '';
        return {
          overflow: document.documentElement.scrollWidth > window.innerWidth + 1
            || document.body.scrollWidth > window.innerWidth + 1,
          panelText: (panel && panel.innerText || '').slice(0, 120),
          text: txt.slice(0, 240),
          hasEmpty: !!c && !!c.querySelector('.empty'),
          hasError: !!c && !!c.querySelector('.error-text'),
        };
      }, tab);
      rec.overflow = d.overflow;
      rec.empty = d.hasEmpty;
      rec.errorText = d.hasError;
      rec.snippet = d.text.replace(/\s+/g, ' ').slice(0, 140);
      await page.screenshot({ path: `${outDir}/legal-${tab}-${w}.png`, fullPage: true });
    } catch (e) {
      rec.error = e.message;
    }
    report.panels.push(rec);
  }
  // also a full-page shot of the whole legal panel (top)
  await page.screenshot({ path: `${outDir}/legal-panel-${w}.png`, fullPage: true });
  await ctx.close();
}

await browser.close();
writeFileSync(`${outDir}/legal-report.json`, JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
