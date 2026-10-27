#!/usr/bin/env node
/**
 * 从 toutiao-ops 的 Playwright profile 里导出头条 cookie（明文）。
 *
 * 为什么要单独写这个，而不是用 scripts/export-cookies.py：
 * 那个脚本读的是**本机真实 Chrome** 的 cookie 库，需要 Keychain 口令解密；
 * 而 toutiao-ops 用的是**它自己的 Playwright profile**（<HOME>/.toutiao-ops/
 * accounts/<name>/browser-data），那份 profile 的 SQLite 里 `encrypted_value`
 * 是空的、Chrome 也解不开（ADR-0071 实测：两套独立的登录态）。
 *
 * 但 **Playwright 自己能读**——它启动 Chromium 后通过 CDP 拿到的是
 * 已经解开的明文 cookie。所以「browserless 读不到 Playwright profile」
 * 这句话要精确化：读不到的是那份 SQLite 的密文，读得到的是浏览器
 * 进程里那份明文。这条路把「服务器上要人工扫码」变成「拷一份 cookie」。
 *
 * 用法：
 *   node scripts/export-pw-cookies.mjs --out /tmp/pw-cookies.json
 *   node scripts/export-pw-cookies.mjs --out ... --account default
 *
 * 输出格式与 export-cookies.py 相同（{cookies:[{domain,name,value,path,
 * secure,http_only,expires}]}），所以两边的消费方可以互换。
 *
 * 安全：不打印 cookie 值，只打印条数与权限位。
 */

import { createRequire } from 'module';
import { homedir } from 'os';
import { join, resolve } from 'path';
import { writeFileSync, mkdirSync, chmodSync } from 'fs';
import { pathToFileURL, fileURLToPath } from 'url';

// fileURLToPath 而不是 .pathname：Windows 上后者给出 "/C:/Users/..."，
// 再 resolve 一次会变成 "C:\C:\Users\..."（实测 MODULE_NOT_FOUND）。
const REPO = resolve(fileURLToPath(new URL('..', import.meta.url)));
const CLI_DIR = join(REPO, 'reference', 'toutiao-ops', 'cli');

// playwright 装在 CLI 的 node_modules 里，脚本本身在 scripts/ 下，
// 直接 import 找不到，所以从 CLI 的 package.json 起一个 require。
const require = createRequire(join(CLI_DIR, 'package.json'));
const { chromium } = require('playwright');

function parseArgs(argv) {
  const out = { out: '', account: 'default' };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--out') out.out = argv[++i];
    else if (argv[i] === '--account') out.account = argv[++i];
  }
  if (!out.out) {
    console.error('用法: node scripts/export-pw-cookies.mjs --out <path> [--account <name>]');
    process.exit(2);
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));

// HOME 决定状态目录在哪（ADR-0028 的重定向）。
const profileDir = join(
  homedir(), '.toutiao-ops', 'accounts', args.account, 'browser-data',
);

let cookies = [];
try {
  const ctx = await chromium.launchPersistentContext(profileDir, {
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
  });
  cookies = await ctx.cookies();
  await ctx.close();
} catch (err) {
  console.error(JSON.stringify({
    ok: false,
    error: `读 Playwright profile 失败: ${err.message}`,
    profile: profileDir,
    hint: '先在本机跑一次 toutiao-ops 的 auth login 扫码，profile 里才有登录态',
  }));
  process.exit(1);
}

const tt = cookies
  .filter((c) => c.domain.includes('toutiao'))
  .map((c) => ({
    domain: c.domain,
    name: c.name,
    value: c.value,
    path: c.path || '/',
    secure: Boolean(c.secure),
    http_only: Boolean(c.httpOnly),
    expires: c.expires && c.expires > 0 ? Math.floor(c.expires) : 0,
  }));

// 至少要有 sessionid，否则等于没导出。
const hasSession = tt.some((c) => c.name === 'sessionid' && c.value);
const payload = {
  exported_at: new Date().toISOString(),
  source: 'playwright-profile',
  profile: profileDir,
  count: tt.length,
  cookies: tt,
};

mkdirSync(resolve(args.out, '..'), { recursive: true });
writeFileSync(args.out, JSON.stringify(payload, null, 2));
chmodSync(args.out, 0o600);

// 最早到期的那条决定这份 cookie 还能用多久。
// 但「全部 cookie 里最早」会被 gd_random 这类一次性 cookie 拉偏
// （实测 2026-10-28，而真正管登录的 sessionid 是 2026-12-13），
// 所以分两个口径报：全部、以及真正承载登录态的那几个。
const AUTH_COOKIES = new Set([
  'sessionid', 'sessionid_ss', 'sid_tt', 'sid_guard', 'uid_tt', 'uid_tt_ss',
  'sid_ucp_v1', 'sid_ucp_sso_v1', 'ssid_ucp_v1', 'ssid_ucp_sso_v1',
  'sso_uid_tt', 'sso_uid_tt_ss', 'passport_csrf_token', 'passport_auth_status',
  'toutiao_sso_user', 'ttwid', 'odin_tt', 'tt_anti_token', 'csrftoken',
]);
const expiries = tt.map((c) => c.expires).filter((e) => e > 0);
const authExpiries = tt
  .filter((c) => AUTH_COOKIES.has(c.name))
  .map((c) => c.expires)
  .filter((e) => e > 0);
const earliest = expiries.length ? Math.min(...expiries) : 0;
const earliestAuth = authExpiries.length ? Math.min(...authExpiries) : 0;
const latest = expiries.length ? Math.max(...expiries) : 0;
const fmt = (ts) => (ts ? new Date(ts * 1000).toISOString().slice(0, 10) : '会话 cookie');

console.log(JSON.stringify({
  ok: hasSession,
  count: tt.length,
  out: args.out,
  mode: '0600',
  has_session: hasSession,
  earliest_expiry: fmt(earliest),
  earliest_auth_expiry: fmt(earliestAuth),
  latest_expiry: fmt(latest),
  message: hasSession
    ? '导出完成。值没打印，只打印了条数与到期日。'
    : 'profile 里没有 sessionid —— 该账号还没登录过，或登录态在别的 account 下。',
}, null, 2));

process.exit(hasSession ? 0 : 1);
