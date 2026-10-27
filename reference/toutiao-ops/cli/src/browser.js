import { chromium } from 'playwright-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';
import { homedir } from 'os';
import { join } from 'path';
import { mkdirSync, readFileSync } from 'fs';

chromium.use(StealthPlugin());

const DEFAULT_ACCOUNT = 'default';

const DEFAULT_VIEWPORT = { width: 1440, height: 900 };

// NewsAgg patch (ADR-0024 / ADR-0023)：服务器 2GB 内存必需。
// 上游原本没有这些参数，在容器/小内存环境下会因为 /dev/shm 不足而崩溃。
const LAUNCH_ARGS = [
  '--disable-blink-features=AutomationControlled',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-infobars',
  '--disable-dev-shm-usage',
  '--no-sandbox',
  '--renderer-process-limit=1',
  '--disable-gpu',
];

// NewsAgg note (ADR-0028)：状态目录与 Playwright 浏览器缓存都由 HOME 决定，
// 因此调用方通过设置 HOME 把两者一起重定向到仓内位置，源码无需改动。
const BASE_DIR = join(homedir(), '.toutiao-ops');

/**
 * 获取指定账号的数据目录路径。
 */
export function getAccountDir(account) {
  return join(BASE_DIR, 'accounts', account || DEFAULT_ACCOUNT);
}

/**
 * 获取指定账号的浏览器数据目录。
 */
export function getBrowserDataDir(account) {
  return join(getAccountDir(account), 'browser-data');
}

/**
 * 获取指定账号的截图目录。
 */
export function getScreenshotDir(account) {
  return join(getAccountDir(account), 'screenshots');
}

/**
 * 启动持久化浏览器上下文。
 * 会话数据按账号隔离，保存在 ~/.toutiao-ops/accounts/<name>/browser-data/。
 *
 * @param {object} opts
 * @param {string} [opts.account="default"]
 * @param {boolean} [opts.headless=false]
 * @returns {{ context: BrowserContext, page: Page }}
 */
export async function launchBrowser(opts = {}) {
  const userDataDir = getBrowserDataDir(opts.account);
  mkdirSync(userDataDir, { recursive: true });

  const headless = Boolean(opts.headless);

  const context = await chromium.launchPersistentContext(userDataDir, {
    headless,
    viewport: DEFAULT_VIEWPORT,
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    args: LAUNCH_ARGS,
    ignoreDefaultArgs: ['--enable-automation'],
    permissions: [],
    geolocation: undefined,
  });

  // 预先拒绝地理位置等权限，避免弹出浏览器权限请求
  try {
    await context.grantPermissions([], { origin: 'https://mp.toutiao.com' });
  } catch {}

  // ✅ 本机补丁（reference/ 不入库，只影响这台 Windows 机器）：
  // 实测本机 Playwright 持久化上下文**不落盘**（写入 __persist_probe 后重开即丢失），
  // 所以登录态改以 cookie 文件为准：存在就注入，随后 auth check / 发布都能用。
  // 服务器上不设 NEWSAGG_COOKIE_FILE 时行为不变。
  // 用独立开关 NEWSAGG_COOKIE_INJECT=1，而不是复用 NEWSAGG_COOKIE_FILE：
  // 后者在服务器上也被 run_all.sh 设置，复用它等于顺手改掉服务器已验证的行为。
  const cookieFile = process.env.NEWSAGG_COOKIE_INJECT ? process.env.NEWSAGG_COOKIE_FILE : '';
  if (cookieFile) {
    try {
      const raw = JSON.parse(readFileSync(cookieFile, 'utf8'));
      const list = (raw.cookies || []).map((c) => ({
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path || '/',
        secure: Boolean(c.secure),
        httpOnly: Boolean(c.http_only ?? c.httpOnly),
        expires: c.expires && c.expires > 0 ? c.expires : -1,
      }));
      if (list.length) await context.addCookies(list);
    } catch (e) {
      console.warn(`[browser] 注入 cookie 失败（${cookieFile}）：${e.message}`);
    }
  }

  const page = context.pages()[0] || await context.newPage();
  return { context, page };
}

/**
 * 安全关闭浏览器上下文
 */
export async function closeBrowser(context) {
  if (context) {
    await context.close();
  }
}

/**
 * 随机延迟，模拟人类操作节奏
 */
export function sleep(min = 200, max = 800) {
  const ms = Math.floor(Math.random() * (max - min + 1)) + min;
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * 模拟人类打字：逐字输入并带随机间隔
 */
export async function humanType(page, selector, text) {
  await page.click(selector);
  for (const ch of text) {
    await page.keyboard.type(ch, { delay: 50 + Math.random() * 120 });
  }
}

/**
 * 等待页面网络基本空闲后再操作
 */
export async function waitForStable(page, timeout = 10000) {
  await page.waitForLoadState('networkidle', { timeout }).catch(() => {});
}

/**
 * 关闭页面上所有弹窗、模态框、横幅、权限提示等遮挡物。
 * 头条后台常见遮挡：活动弹窗、位置权限弹窗、新手引导、首发激励等。
 * 在任何需要点击页面元素之前调用。
 */
export async function dismissOverlays(page) {
  // 关闭浏览器级别权限弹窗（地理位置等）
  page.context().on('page', () => {});
  try {
    // 拒绝位置权限
    await page.context().grantPermissions([], { origin: 'https://mp.toutiao.com' });
  } catch {}

  for (let round = 0; round < 3; round++) {
    await sleep(500, 800);

    // 在 DOM 里关闭各种弹窗
    const closed = await page.evaluate(() => {
      let count = 0;

      // 通用关闭按钮（模态框右上角 X）
      const closeSelectors = [
        '.byte-modal-wrapper .byte-modal-close',
        '.byte-modal-wrapper [class*="close"]',
        '[class*="modal"] [class*="close"]',
        '[class*="dialog"] [class*="close"]',
        '[class*="popup"] [class*="close"]',
        '[role="dialog"] [class*="close"]',
        '[class*="banner"] [class*="close"]',
        '[class*="toast"] [class*="close"]',
        '[class*="guide"] [class*="close"]',
        '[class*="tip"] [class*="close"]',
      ];
      for (const sel of closeSelectors) {
        document.querySelectorAll(sel).forEach(el => {
          try { if (typeof el.click === 'function') el.click(); count++; } catch {}
        });
      }

      // "我知道了"、"已知悉"、"关闭"、"一律不允许" 等确认/关闭按钮
      const dismissTexts = ['我知道了', '已知悉', '知道了', '关闭', '一律不允许', '不再提示', '稍后再说', '取消', '跳过'];
      const allButtons = document.querySelectorAll('button, [role="button"], a, span, div');
      for (const btn of allButtons) {
        const text = btn.textContent?.trim();
        if (text && dismissTexts.some(t => text === t || text.startsWith(t))) {
          try {
            const rect = btn.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0 && rect.width < 400) {
              btn.click();
              count++;
            }
          } catch {}
        }
      }

      // 隐藏所有 modal wrapper 和 drawer
      document.querySelectorAll('.byte-modal-wrapper, [class*="zoomModal"], [class*="modal-mask"], .byte-drawer-wrapper, [class*="drawer-mask"]').forEach(el => {
        el.style.display = 'none';
        count++;
      });

      // 隐藏 banner 遮挡
      document.querySelectorAll('[class*="banner-image"], [class*="banner-wrap"], [class*="banner-container"]').forEach(el => {
        el.style.display = 'none';
        count++;
      });

      return count;
    });

    if (closed === 0) break;
  }

  // 再按一次 Escape 兜底
  await page.keyboard.press('Escape').catch(() => {});
  await sleep(300, 500);
}

/**
 * 在浏览器上下文内发起 fetch 请求（自动携带 Cookie/Referer）
 */
export async function browserFetch(page, url, fetchOpts = {}) {
  return page.evaluate(async ({ url, opts }) => {
    const res = await fetch(url, {
      credentials: 'include',
      ...opts,
    });
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('json')) {
      return { ok: res.ok, status: res.status, data: await res.json() };
    }
    return { ok: res.ok, status: res.status, data: await res.text() };
  }, { url, opts: fetchOpts });
}
