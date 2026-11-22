/**
 * Read the FAA Air Operator 14 CFR search (Tableau on explore.dot.gov)
 * that is embedded from https://www.faa.gov/av-info/air-operator-far-search.
 *
 * There is no public CSV/API. The dashboard's Download sheet is the FAA's
 * own full-table view (worksheets Air Operators, Aircraft, DBA). This script
 * opens that dashboard once in Chrome, reads the "Data Updated" label, and
 * only then exports those worksheets.
 *
 * One browser session, a few Tableau commands, then stop.
 * Exits non-zero on access denial, a missing date, or an empty export.
 */
import puppeteer from 'puppeteer-core';
import fs from 'fs';
import path from 'path';

const SEARCH_PAGE = 'https://www.faa.gov/av-info/air-operator-far-search';
const TABLEAU_URL = 'https://explore.dot.gov/t/FAA/views/AVInfo_AirOperators/AirOperators?:embed=y&:toolbar=bottom&:showVizHome=no';
const PART = '135';
const DOWNLOAD_SHEETS = ['Air Operators', 'Aircraft', 'DBA'];

function arg(name, fallback = '') {
  const i = process.argv.indexOf(name);
  if (i === -1) return fallback;
  return process.argv[i + 1] || fallback;
}

const outPath = arg('--out');
const reuseLabel = arg('--reuse-if-label');
if (!outPath) {
  console.error('usage: node fetch_air_operator.mjs --out <file> [--reuse-if-label "<FAA label>"]');
  process.exit(2);
}

function chromePath() {
  const candidates = [
    process.env.CHROME_PATH,
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/opt/google/chrome/chrome',
  ].filter(Boolean);
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  throw new Error('Google Chrome not found. Set CHROME_PATH.');
}

function decodeBody(bytes) {
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) {
    return new TextDecoder('utf-16le').decode(bytes);
  }
  if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) {
    return new TextDecoder('utf-16be').decode(bytes);
  }
  return new TextDecoder('utf-8').decode(bytes);
}

function parseTable(text) {
  const cleaned = text.replace(/^\uFEFF/, '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
  if (!cleaned) return { columns: [], rows: [] };
  const lines = cleaned.split('\n').filter((l) => l.trim().length);
  const split = (line) => {
    if (line.includes('\t')) return line.split('\t');
    return line.split(',');
  };
  // Tableau sometimes prefixes a title line. Use the first row with 2+ cells as the header.
  let headerIdx = 0;
  for (let i = 0; i < Math.min(lines.length, 8); i++) {
    if (split(lines[i]).filter((c) => c.trim()).length >= 2) {
      headerIdx = i;
      break;
    }
  }
  const columns = split(lines[headerIdx]).map((c) => c.trim());
  const rows = [];
  for (const line of lines.slice(headerIdx + 1)) {
    const cells = split(line).map((c) => c.trim());
    if (cells.every((c) => !c)) continue;
    while (cells.length < columns.length) cells.push('');
    rows.push(cells.slice(0, Math.max(columns.length, cells.length)));
  }
  return { columns, rows };
}

async function launch() {
  const userDataDir = fs.mkdtempSync(path.join('/tmp', 'faa-air-'));
  const browser = await puppeteer.launch({
    executablePath: chromePath(),
    headless: true,
    userDataDir,
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 1000 });
  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  });
  return { browser, page, userDataDir };
}

async function openDashboard(page) {
  const response = await page.goto(TABLEAU_URL, { waitUntil: 'domcontentloaded', timeout: 90000 });
  const status = response ? response.status() : 0;
  await page.waitForFunction(() => {
    const ta = document.querySelector('#tsConfigContainer');
    const text = document.body ? document.body.innerText : '';
    return (ta && ta.value && ta.value.includes('sessionid') && text.includes('Data Updated'))
      || (document.title || '').includes('Access Denied')
      || text.includes('Access Denied');
  }, { timeout: 90000 });
  const snap = await page.evaluate(() => {
    const text = document.body ? document.body.innerText : '';
    const ta = document.querySelector('#tsConfigContainer');
    let cfg = null;
    try { cfg = ta && ta.value ? JSON.parse(ta.value) : null; } catch { cfg = null; }
    const labelMatch = text.match(/Data Updated:\s*([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})/);
    return {
      title: document.title,
      denied: text.includes('Access Denied') || (document.title || '').includes('Access Denied'),
      sessionid: cfg && cfg.sessionid,
      vizql: cfg && cfg.vizql_root,
      updatedLabel: labelMatch ? labelMatch[1] : '',
      textSample: text.slice(0, 400),
    };
  });
  if (status === 403 || snap.denied || !snap.sessionid) {
    throw new Error(`Air Operator search blocked or did not load (HTTP ${status}). ${snap.textSample}`);
  }
  if (!snap.updatedLabel) {
    throw new Error('Air Operator search loaded but the FAA "Data Updated" label was not found. Refusing to continue.');
  }
  return snap;
}

async function tableau(page, vizql, sessionid, command, fields) {
  const result = await page.evaluate(async (vizql, sessionid, command, fields) => {
    const body = new FormData();
    for (const [key, value] of Object.entries(fields)) body.append(key, value);
    const res = await fetch(`${vizql}/sessions/${sessionid}/commands/${command}`, {
      method: 'POST',
      body,
      credentials: 'include',
      headers: { Accept: 'application/json, text/javascript, */*' },
    });
    const text = await res.text();
    return { status: res.status, text };
  }, vizql, sessionid, command, fields);
  if (result.status !== 200) {
    throw new Error(`Tableau ${command} failed HTTP ${result.status}: ${result.text.slice(0, 240)}`);
  }
  if (/access denied/i.test(result.text.slice(0, 400))) {
    throw new Error(`Tableau ${command} was denied.`);
  }
  let json;
  try { json = JSON.parse(result.text); }
  catch { throw new Error(`Tableau ${command} did not return JSON: ${result.text.slice(0, 240)}`); }
  return json;
}

function dig(obj, path) {
  let cur = obj;
  for (const key of path) {
    if (!cur || typeof cur !== 'object') return undefined;
    cur = cur[key];
  }
  return cur;
}

async function exportSheet(page, vizql, sessionid, sheetName) {
  const dialog = await tableau(page, vizql, sessionid, 'tabsrv/export-crosstab-server-dialog', {
    thumbnailUris: '{}',
  });
  const items = dig(dialog, [
    'vqlCmdResponse', 'layoutStatus', 'applicationPresModel', 'presentationLayerNotification', 0,
    'presModelHolder', 'genExportCrosstabOptionsDialogPresModel', 'thumbnailSheetPickerItems',
  ]) || [];
  const sheet = items.find((item) => item.sheetName === sheetName);
  if (!sheet) {
    const names = items.map((item) => item.sheetName).join(', ');
    throw new Error(`Download sheet "${sheetName}" not in Tableau export list (${names || 'empty'}).`);
  }
  const exported = await tableau(page, vizql, sessionid, 'tabsrv/export-crosstab-to-csvserver', {
    sheetdocId: sheet.sheetdocId,
    useTabs: 'true',
    sendNotifications: 'true',
  });
  const holder = dig(exported, [
    'vqlCmdResponse', 'layoutStatus', 'applicationPresModel', 'presentationLayerNotification', 0, 'presModelHolder',
  ]) || {};
  const resultKey = holder.genExportFilePresModel?.resultKey || holder.genFileDownloadPresModel?.tempfileKey;
  if (!resultKey) {
    throw new Error(`Tableau did not return a download for "${sheetName}". Export may be disabled.`);
  }
  const tableText = await page.evaluate(async (vizql, sessionid, resultKey) => {
    const url = `${vizql}/tempfile/sessions/${sessionid}/?key=${encodeURIComponent(resultKey)}&keepfile=yes&attachment=yes`;
    const res = await fetch(url, { credentials: 'include' });
    if (!res.ok) return { status: res.status, text: '' };
    const buf = new Uint8Array(await res.arrayBuffer());
    let text;
    if (buf.length >= 2 && buf[0] === 0xff && buf[1] === 0xfe) text = new TextDecoder('utf-16le').decode(buf);
    else if (buf.length >= 2 && buf[0] === 0xfe && buf[1] === 0xff) text = new TextDecoder('utf-16be').decode(buf);
    else text = new TextDecoder('utf-8').decode(buf);
    return { status: res.status, text };
  }, vizql, sessionid, resultKey);
  if (tableText.status !== 200 || !tableText.text.trim()) {
    throw new Error(`Empty download for "${sheetName}" (HTTP ${tableText.status}).`);
  }
  const table = parseTable(tableText.text);
  if (!table.columns.length || !table.rows.length) {
    throw new Error(`"${sheetName}" parsed to zero rows.`);
  }
  return table;
}

async function runOnce() {
  const { browser, page } = await launch();
  try {
    const snap = await openDashboard(page);
    if (reuseLabel && snap.updatedLabel === reuseLabel) {
      return {
        unchanged: true,
        updatedLabel: snap.updatedLabel,
        sourcePage: SEARCH_PAGE,
        tableauUrl: TABLEAU_URL,
        partFilter: PART,
      };
    }
    await new Promise((r) => setTimeout(r, 1200));
    await tableau(page, snap.vizql, snap.sessionid, 'tabdoc/set-parameter-value', {
      globalFieldName: '[Parameters].[Operator FAR Parameter]',
      valueString: PART,
      useUsLocale: 'false',
    });
    await new Promise((r) => setTimeout(r, 1200));
    const tables = {};
    for (const name of DOWNLOAD_SHEETS) {
      tables[name] = await exportSheet(page, snap.vizql, snap.sessionid, name);
      await new Promise((r) => setTimeout(r, 1000));
    }
    return {
      unchanged: false,
      updatedLabel: snap.updatedLabel,
      sourcePage: SEARCH_PAGE,
      tableauUrl: TABLEAU_URL,
      partFilter: PART,
      extractedAt: new Date().toISOString(),
      method: 'tableau-crosstab',
      tables,
    };
  } finally {
    await browser.close().catch(() => {});
  }
}

let payload;
try {
  payload = await runOnce();
} catch (first) {
  console.error(`Air Operator fetch failed once: ${first.message}`);
  console.error('Waiting 20s and trying one more time.');
  await new Promise((r) => setTimeout(r, 20000));
  payload = await runOnce();
}

fs.mkdirSync(path.dirname(path.resolve(outPath)), { recursive: true });
fs.writeFileSync(outPath, JSON.stringify(payload));
const counts = payload.tables
  ? Object.fromEntries(Object.entries(payload.tables).map(([k, v]) => [k, v.rows.length]))
  : {};
console.log(JSON.stringify({
  unchanged: payload.unchanged,
  updatedLabel: payload.updatedLabel,
  counts,
}));
