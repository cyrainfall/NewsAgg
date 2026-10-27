#!/usr/bin/env node
/**
 * 把导出的 cookie 注入一个 toutiao-ops 的 Playwright profile。
 *
 * 用途：服务器上的发布写路径要走浏览器（ADR-0073 判定纯 HTTP 写路径
 * 不可启用），而浏览器要的是**它自己那份 profile 里的登录态**。
 * 人工扫码是可行的，但每次重建环境都要人来一次；这条路让「拷一份
 * cookie」就够。
 *
 * 做法：启动 Playwright 的 persistent context，用 CDP 级的 addCookies
 * 把 cookie 写进去。Playwright 会把它落进 profile 的 SQLite，
 * 之后 `node index.js auth check` 就能读到。
 *
 * 用法：
 *   node scripts/import-pw-cookies.mjs --from /tmp/pw-cookies.json
 *   node scripts/import-pw-cookies.mjs --from ... --account default
 *
 * 前提：目标机器上已经装好 toutiao-ops 的 node_modules 与 Playwright
 * 浏览器（在 reference/toutiao-ops/cli 下 `npm install`，
 * `npx playwright install chromium`）。
 *
 * 安全：不打印 cookie 值。
 */

import { createRequire } from 'module';
import { homedir } from 'os';
import { join, resolve } from 'path';
import { readFileSync } from 'fs';

const REPO = resolve(new URL('..', import.meta.url).pathname);
const CLI_DIR = join(REPO, 'reference', 'toutiao-ops', 'cli');

const require = createRequire(join(CLI_DIR, 'package.json'));
const { chromium } = require('playwright');

function parseArgs(argv) {
  const out = { from: '', account: 'default' };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--from') out.from = argv[++i];
    else if (argv[i] === '--account') out.account = argv[++i];
  }
  if (!out.from) {
    console.error('用法: node scripts/import-pw-cookies.mjs --from <path> [--account <name>]');
    process.exit(2);
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));

let payload;
try {
  payload = JSON.parse(readFileSync(args.from, 'utf8'));
} catch (err) {
  console.error(JSON.stringify({ ok: false, error: `读不了 ${args.from}: ${err.message}` }));
  process.exit(1);
}

const cookies = Array.isArray(payload.cookies) ? payload.cookies : [];
if (cookies.length === 0) {
  console.error(JSON.stringify({ ok: false, error: '文件里没有 cookie' }));
  process.exit(1);
}

// Playwright 的 cookie 形状与导出格式一致，只多一个 expires 的语义差别：
// 导出用 Unix 秒，Playwright 也用 Unix 秒（0 表示会话 cookie）。
const toPlaywright = cookies
  .filter((c) => c.name && c.value)
  .map((c) => ({
    name: c.name,
    value: c.value,
    domain: c.domain,
    path: c.path || '/',
    secure: Boolean(c.secure),
    httpOnly: Boolean(c.http_only),
    ...(c.expires > 0 ? { expires: Math.floor(c.expires) } : {}),
  }));

const profileDir = join(
  homedir(), '.toutiao-ops', 'accounts', args.account, 'browser-data',
);

let added = 0;
let failed = null;
try {
  const ctx = await chromium.launchPersistentContext(profileDir, {
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
  });
  await ctx.addCookies(toPlaywright);
  added = toPlaywright.length;
  await ctx.close();
} catch (err) {
  failed = err.message;
}

console.log(JSON.stringify({
  ok: !failed,
  injected: added,
  profile: profileDir,
  account: args.account,
  error: failed,
  message: failed
    ? '注入失败，profile 未改动'
    : '注入完成。接着跑 node index.js auth check 验证。',
}, null, 2));

process.exit(failed ? 1 : 0);
