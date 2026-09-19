#!/usr/bin/env node
// Visual QA for the Kai Command Center (directive 2026-09-19).
//
// Renders every *visible* sidebar tab at 360/768/1280, captures screenshots and
// asserts:
//   * no horizontal overflow (document/body scrollWidth <= viewport + 1)
//   * no panel renders a raw JSON dump (`<pre>{...}`)
//   * no panel text contains "Not Found" or "NetworkError" / "Failed to fetch"
//
// Usage (needs Node 20 + Playwright):
//   CC_URL=https://127.0.0.1:18000/command-center KAI_SESSION=<jwt> \
//     /opt/node20/bin/node scripts/visual_qa_cc.mjs
// Env: OUT_DIR, WIDTHS, TABS (csv, default = all gated-visible tabs).
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';

const base = process.env.CC_URL || 'https://127.0.0.1:18000/command-center';
const token = process.env.KAI_SESSION;
const outDir = process.env.OUT_DIR || '/tmp/opencode/cc-visual-qa';
const widths = (process.env.WIDTHS || '360,768,1280').split(',').map(Number);
const onlyTabs = (process.env.TABS || '').split(',').filter(Boolean);
const SK = 'kai_command_session_v3';

if (!token) { console.error('KAI_SESSION required'); process.exit(2); }
mkdirSync(outDir, { recursive: true });

const BAD_TEXT = [/Not Found/i, /NetworkError/i, /Failed to fetch/i];
const RAW_DUMP = /<\s*pre[^>]*>\s*[\{\[]/;

const browser = await chromium.launch();
const report = { base, generated: new Date().toISOString(), violations: [], warnings: [], panels: [] };

// Discover the gated-visible tabs once.
let tabs = [];
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, ignoreHTTPSErrors: true });
  await ctx.addInitScript(([k, v]) => { try { localStorage.setItem(k, v); } catch (e) {} },
    [SK, JSON.stringify({ token, role: 'operator', username: 'visual-qa' })]);
  const page = await ctx.newPage();
  await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#app-wrap.active', { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(6000); // let registry gating finish
  tabs = await page.evaluate(() =>
    [...document.querySelectorAll('#sidebar-nav .nav-item[data-hash]')].map((a) => a.dataset.hash));
  await ctx.close();
}
if (onlyTabs.length) tabs = tabs.filter((t) => onlyTabs.includes(t));
report.tabs = tabs;

for (const w of widths) {
  const ctx = await browser.newContext({
    viewport: { width: w, height: 900 }, deviceScaleFactor: 1, ignoreHTTPSErrors: true,
  });
  await ctx.addInitScript(([k, v]) => { try { localStorage.setItem(k, v); } catch (e) {} },
    [SK, JSON.stringify({ token, role: 'operator', username: 'visual-qa' })]);
  const page = await ctx.newPage();
  for (const tab of tabs) {
    const rec = { tab, width: w, overflow: false, rawDump: false, badText: [], ready: false, error: null };
    try {
      await page.goto(base + '#' + tab, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForSelector('#app-wrap.active', { timeout: 20000 }).catch(() => {});
      for (let i = 0; i < 40; i++) {
        const st = await page.evaluate((t) => {
          const p = document.getElementById('panel-' + t);
          if (!p) return { exists: false };
          const active = p.classList.contains('active');
          return { exists: true, active, loading: !!p.querySelector('.loading') };
        }, tab);
        if (st.exists && st.active && !st.loading) { rec.ready = true; break; }
        await page.waitForTimeout(300);
      }
      const d = await page.evaluate((t) => {
        const p = document.getElementById('panel-' + t);
        const text = p ? (p.innerText || '') : '';
        const html = p ? p.innerHTML : '';
        return {
          overflow: document.documentElement.scrollWidth > window.innerWidth + 1
            || document.body.scrollWidth > window.innerWidth + 1,
          html, text, hasPre: !!p && !!p.querySelector('pre'),
        };
      }, tab);
      rec.overflow = d.overflow;
      rec.rawDump = RAW_DUMP.test(d.html);
      rec.badText = BAD_TEXT.filter((re) => re.test(d.text)).map((re) => String(re));
      if (rec.overflow) report.violations.push({ tab, width: w, kind: 'horizontal-overflow' });
      if (rec.rawDump) report.violations.push({ tab, width: w, kind: 'raw-json-dump' });
      if (rec.badText.length) report.violations.push({ tab, width: w, kind: 'bad-text', patterns: rec.badText });
      await page.screenshot({ path: `${outDir}/${tab}-${w}.png`, fullPage: true });
    } catch (e) {
      rec.error = e.message;
      report.warnings.push({ tab, width: w, error: e.message });
    }
    report.panels.push(rec);
  }
  await ctx.close();
}

await browser.close();
writeFileSync(`${outDir}/report.json`, JSON.stringify(report, null, 2));
console.log(JSON.stringify({
  tabs: report.tabs.length, widths,
  violations: report.violations.length,
  warnings: report.warnings.length,
  detail: report.violations.slice(0, 40),
  report: `${outDir}/report.json`,
}, null, 2));
process.exit(report.violations.length ? 1 : 0);
