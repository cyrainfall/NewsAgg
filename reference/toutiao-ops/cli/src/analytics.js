import { launchBrowser, closeBrowser, sleep, browserFetch, waitForStable, dismissOverlays } from './browser.js';
import { ensureLoggedIn } from './auth-guard.js';

const WORKS_URLS = {
  all: 'https://mp.toutiao.com/profile_v4/analysis/works-overall/all',
  article: 'https://mp.toutiao.com/profile_v4/analysis/works-overall/article',
  video: 'https://mp.toutiao.com/profile_v4/analysis/works-overall/video',
  weitoutiao: 'https://mp.toutiao.com/profile_v4/analysis/works-overall/weitoutiao',
};

const FANS_URL = 'https://mp.toutiao.com/profile_v4/analysis/fans/overview';

const INCOME_URLS = {
  all: 'https://mp.toutiao.com/profile_v4/analysis/income-overview',
  article: 'https://mp.toutiao.com/profile_v4/analysis/new-income-advertisement',
  video: 'https://mp.toutiao.com/profile_v4/analysis/video-income',
};

/**
 * 获取作品数据。
 * --type 支持: all / article / video / weitoutiao
 */
export async function getWorksAnalytics(opts) {
  const type = opts.type || 'all';
  const url = WORKS_URLS[type] || WORKS_URLS.all;
  return collectAnalytics(url, `works_${type}`, opts);
}

/**
 * 获取粉丝数据（含性别、年龄、地域、机型价格分布）。
 */
export async function getFansAnalytics(opts) {
  const { context, page } = await launchBrowser(opts);
  try {
    await ensureLoggedIn(page);

    await page.goto(FANS_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await waitForStable(page);
    await sleep(3000, 5000);
    await dismissOverlays(page);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(2000, 3000);

    const fansData = await extractFansDistributions(page);
    const basicMetrics = await extractPageMetrics(page);
    const noiseKeys = ['消息', '头条号', '主页', '设置'];
    for (const k of noiseKeys) delete basicMetrics[k];

    return {
      success: true,
      category: 'fans',
      source: 'dom_scrape',
      metrics: basicMetrics,
      distributions: fansData,
    };
  } finally {
    await closeBrowser(context);
  }
}

/**
 * 获取收益数据。
 * --type 支持: all / article / video
 */
export async function getIncomeAnalytics(opts) {
  const type = opts.type || 'all';
  const url = INCOME_URLS[type] || INCOME_URLS.all;
  return collectAnalytics(url, `income_${type}`, opts);
}

/**
 * 批量获取多篇作品的详细数据（一个浏览器会话取全部）。
 *
 * 为什么要有这个：`getContentDetail` 每篇都要 launchBrowser + 登录 + 导航一次，
 * 而后台「查看数据」弹层读到的三个指标（完读率、平均时长、超过 N% 同类）
 * 都在同一个接口里。逐篇调用意味着 10 篇文章 = 10 次浏览器冷启动。
 * 批量版只启一次浏览器，在页面内并发拉全部 gid。
 *
 * 返回形状：
 *   {success, category:'content_detail_batch', count, items:[{contentId, success, ...}], errors:[]}
 * 单篇失败不影响其余篇（ADR-0006：表现数据读不回来不阻断本轮）。
 */
export async function getContentDetailBatch(opts) {
  const { context, page } = await launchBrowser(opts);
  try {
    await ensureLoggedIn(page);

    await page.goto('https://mp.toutiao.com/profile_v4/manage/content/all', {
      waitUntil: 'domcontentloaded', timeout: 30000,
    });
    await waitForStable(page);
    await sleep(1500, 2500);

    // commander 的变长选项（`<id...>`）把数组挂在 camelCase 名 `contentId` 上，
    // 不是 `contentIds`——写错这个名字会静默拿到 undefined，于是报「未提供 --content-id」。
    const rawIds = opts.contentId;
    const ids = (Array.isArray(rawIds) ? rawIds : [rawIds])
      .filter(Boolean).map(String).filter(Boolean);
    if (!ids.length) {
      return { success: false, category: 'content_detail_batch', error: '未提供 --content-id（可重复）' };
    }
    const contentType = opts.contentType || '1';

    const raw = await page.evaluate(async ({ ids, type }) => {
      const out = [];
      // 逐个 await 而不是 Promise.all：这是同一个会话里的低频读，
      // 并发打太多容易被平台的接口频控挡掉，而错判成「无数据」比慢更糟。
      for (const id of ids) {
        try {
          const url = `https://mp.toutiao.com/mp/agw/statistic/v2/item/info?item_id=${id}&type=${type}&app_id=1231`;
          const res = await fetch(url, { credentials: 'include' });
          const data = await res.json();
          out.push({ id, data });
        } catch (err) {
          out.push({ id, error: String(err && err.message ? err.message : err) });
        }
      }
      return out;
    }, { ids, type: contentType });

    const KNOWN_TYPES = [0, 1, 2, 3, 4, 5, 6, 7];
    const typeNum = Number(contentType);
    const items = [];
    const errors = [];

    for (const entry of raw) {
      if (entry.error) {
        errors.push({ contentId: entry.id, error: entry.error });
        items.push({ contentId: entry.id, success: false, error: entry.error });
        continue;
      }
      const data = entry.data;
      if (data && data.code === 1003) {
        if (!KNOWN_TYPES.includes(typeNum)) {
          items.push({
            contentId: entry.id, success: false, contentType,
            error: `content-type 编号 ${contentType} 不在平台枚举 ${KNOWN_TYPES.join('/')} 内，接口因此返回 1003（不是「没有数据」）`,
          });
        } else {
          items.push({
            contentId: entry.id, success: true, contentType, noData: true,
            note: '平台返回 1003：type 合法，但该作品暂无数据（通常是发布次日 14:00 前数据未就绪）',
          });
        }
        continue;
      }
      if (!data || data.code !== 0) {
        items.push({
          contentId: entry.id, success: false, contentType,
          error: `接口返回 code=${data ? data.code : 'null'}：${data ? (data.message || '') : '无信封'}`,
        });
        continue;
      }
      items.push({ contentId: entry.id, success: true, contentType, data });
    }

    return {
      success: true,
      category: 'content_detail_batch',
      contentType,
      count: items.length,
      items,
      errors,
    };
  } finally {
    await closeBrowser(context);
  }
}

/**
 * 获取单个作品的详细数据。
 * 直接调用头条数据统计 API：/mp/agw/statistic/v2/item/info
 * --content-id  作品 ID（item_id / gidStr）
 * --content-type 内容类型编号（默认 1）: 1=图文文章, 2=视频, 3=微头条, 4=问答, 5=小视频, 6=音频, 7=图集
 *
 * 类型编号取自头条号后台自己的枚举（`@mp/analysis` 前端包里
 * `{All:0,PgcArticle:1,Video:2,Weitoutiao:3,Wenda:4,ShortVideo:5,Audio:6,PgcGallery:7,Invalid:8}`）。
 * 传错编号不会报参数错误，只会返回 `{"code":1003,"item_data":{},
 * "message":"数据开小差啦~请稍后再试"}` —— 与「这个作品没有数据」长得一模一样。
 * 2026-10-26 核实：本仓库原来默认 2，于是所有文章都取到 1003，
 * ADR-0037 据此判定「该接口返回空数据，不可用」。那个结论是错的，
 * 错在参数而不是接口。详见 docs/adr/0068-content-detail-type-param-was-wrong.md。
 */
export async function getContentDetail(opts) {
  const { context, page } = await launchBrowser(opts);
  try {
    await ensureLoggedIn(page);

    // 先导航到头条号页面以获取 cookie 上下文
    await page.goto('https://mp.toutiao.com/profile_v4/manage/content/all', {
      waitUntil: 'domcontentloaded', timeout: 30000,
    });
    await waitForStable(page);
    await sleep(1500, 2500);

    const itemId = opts.contentId;
    const contentType = opts.contentType || '1';

    if (!itemId) {
      // 未指定 ID，从列表中取第一个作品的 ID
      const firstId = await page.evaluate(() => {
        const link = document.querySelector('.genre-item a[href*="toutiao.com/i"], .genre-item a[href*="toutiao.com/item/"]');
        if (link) {
          const m = link.href.match(/\/(?:i|item\/)(\d+)/);
          return m ? m[1] : null;
        }
        return null;
      });

      if (!firstId) {
        return { success: false, error: '未找到作品，请通过 --content-id 指定作品 ID' };
      }

      opts.contentId = firstId;
    }

    // 通过浏览器内 fetch 直接调用统计 API（复用登录 session）
    const data = await page.evaluate(async ({ id, type }) => {
      const url = `https://mp.toutiao.com/mp/agw/statistic/v2/item/info?item_id=${id}&type=${type}&app_id=1231`;
      const res = await fetch(url, { credentials: 'include' });
      return res.json();
    }, { id: opts.contentId, type: contentType });

    // 1003 有两种完全不同的含义，调用方无法区分：
    //   a) type 传错（编号不在平台枚举里）—— 换个 type 就好；
    //   b) 这个作品确实没有数据（刚发布、数据未就绪）。
    // 分不清的时候，调用方只能靠猜，而猜错的代价是「接口不可用」
    // 这种会被写进 ADR 并据此关掉一条能力路的结论（ADR-0037 就这么错的）。
    // 所以这里显式区分：known type 却 1003 → 报「无数据」；未知 type → 报「参数错」。
    const KNOWN_TYPES = [0, 1, 2, 3, 4, 5, 6, 7];
    const typeNum = Number(contentType);
    if (data && data.code === 1003) {
      if (!KNOWN_TYPES.includes(typeNum)) {
        return {
          success: false,
          category: 'content_detail',
          contentId: opts.contentId,
          contentType,
          source: 'api_fetch',
          error: `content-type 编号 ${contentType} 不在平台枚举 ${KNOWN_TYPES.join('/')} 内，接口因此返回 1003（不是「没有数据」）`,
          data,
        };
      }
      return {
        success: true,
        category: 'content_detail',
        contentId: opts.contentId,
        contentType,
        source: 'api_fetch',
        noData: true,
        note: '平台返回 1003：type 合法，但该作品暂无数据（通常是发布次日 14:00 前数据未就绪）',
        data,
      };
    }

    return {
      success: true,
      category: 'content_detail',
      contentId: opts.contentId,
      contentType,
      source: 'api_fetch',
      data,
    };
  } finally {
    await closeBrowser(context);
  }
}

async function collectAnalytics(targetUrl, category, opts) {
  const { context, page } = await launchBrowser(opts);
  try {
    await ensureLoggedIn(page);

    const apiResponses = [];
    page.on('response', async (response) => {
      const url = response.url();
      try {
        const ct = response.headers()['content-type'] || '';
        if (!ct.includes('json')) return;
        // 只拦截与分析数据相关的 API
        const isRelevant = url.includes('analysis') || url.includes('statistic')
          || url.includes('overview') || url.includes('income')
          || url.includes('fans_data') || url.includes('data_overview')
          || url.includes('/pgc/ma/');
        if (!isRelevant) return;
        const json = await response.json();
        apiResponses.push({ url: url.split('?')[0], data: json });
      } catch {}
    });

    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await waitForStable(page);
    await sleep(3000, 5000);
    await dismissOverlays(page);

    if (apiResponses.length > 0) {
      return {
        success: true,
        category,
        source: 'api_intercept',
        data: apiResponses.map(r => r.data),
        apiCount: apiResponses.length,
      };
    }

    // 回退：DOM 提取
    const pageData = await extractPageMetrics(page);
    // 过滤非数据字段（页面导航元素）
    const noiseKeys = ['消息', '头条号', '主页', '设置'];
    for (const k of noiseKeys) delete pageData[k];
    return {
      success: true,
      category,
      source: 'dom_scrape',
      metrics: pageData,
    };
  } finally {
    await closeBrowser(context);
  }
}

async function extractFansDistributions(page) {
  return page.evaluate(() => {
    const result = {};
    const fiberKey = (el) => Object.keys(el).find(k => k.startsWith('__reactInternalInstance') || k.startsWith('__reactFiber'));

    // --- 性别分布：从 .mp-chart-pie 的 React data prop 提取 ---
    const genderBox = (() => {
      for (const t of document.querySelectorAll('.chart-box-title')) {
        if (t.innerText?.trim() === '性别分布') return t.closest('.chart-box');
      }
      return null;
    })();
    if (genderBox) {
      const pieEl = genderBox.querySelector('.mp-chart-pie');
      if (pieEl) {
        const fk = fiberKey(pieEl);
        if (fk) {
          let fiber = pieEl[fk];
          for (let i = 0; i < 20 && fiber; i++) {
            const props = fiber.memoizedProps || fiber.pendingProps;
            if (props?.data && Array.isArray(props.data)) {
              const total = props.data.reduce((s, d) => s + (d.value || 0), 0);
              result.gender = props.data.map(d => ({
                label: d.name,
                value: d.value,
                percent: total > 0 ? ((d.value / total) * 100).toFixed(2) + '%' : '0%',
              }));
              break;
            }
            fiber = fiber.return;
          }
        }
      }
    }

    // --- 年龄 & 机型价格：从 echarts-for-react 的 option prop 提取 ---
    const chartBoxes = document.querySelectorAll('.chart-box');
    for (const box of chartBoxes) {
      const titleEl = box.querySelector('.chart-box-title');
      const title = titleEl?.innerText?.trim();
      if (title !== '年龄分布' && title !== '机型价格分布') continue;

      const echartsEl = box.querySelector('.echarts-for-react');
      if (!echartsEl) continue;
      const fk = fiberKey(echartsEl);
      if (!fk) continue;

      let fiber = echartsEl[fk];
      for (let i = 0; i < 20 && fiber; i++) {
        const props = fiber.memoizedProps || fiber.pendingProps;
        if (props?.option?.series && props?.option?.xAxis) {
          const opt = props.option;
          const xData = (Array.isArray(opt.xAxis) ? opt.xAxis[0] : opt.xAxis).data || [];
          const series = opt.series[0];
          const labels = xData.map(x => typeof x === 'object' ? x.value : x);
          const values = series.data || [];
          const items = labels.map((label, idx) => ({
            label,
            percent: ((values[idx] || 0) * 100).toFixed(2) + '%',
          }));

          if (title === '年龄分布') result.age = items;
          else result.devicePrice = items;
          break;
        }
        fiber = fiber.return;
      }
    }

    // --- 粉丝偏好：粉丝看过的作品 ---
    const articleItems = document.querySelectorAll('.fans-interest-article-container .article-item');
    if (articleItems.length > 0) {
      result.fansViewedWorks = [...articleItems].map(item => {
        const title = item.querySelector('.article-item-title')?.innerText?.trim() || '';
        const desc = item.querySelector('.article-item-desc')?.innerText?.trim() || '';
        const cover = item.querySelector('.article-item-cover img')?.src || '';
        const viewMatch = desc.match(/(\d+)\s*次/);
        return { title, views: viewMatch ? parseInt(viewMatch[1]) : 0, cover };
      }).filter(w => w.title);
    }

    // --- 粉丝偏好：粉丝关注的作者 ---
    const authorItems = document.querySelectorAll('.fans-interest-author .media-item');
    if (authorItems.length > 0) {
      result.fansFollowedAuthors = [...authorItems].map(item => {
        const name = item.querySelector('.media-name')?.innerText?.trim() || '';
        const avatar = item.querySelector('.media-avatar')?.src || '';
        const link = item.href || '';
        return { name, avatar, link };
      }).filter(a => a.name);
    }

    return result;
  });
}

async function extractPageMetrics(page) {
  return page.evaluate(() => {
    const metrics = {};

    // 方法1：找"标签 + 数值"的相邻元素对
    const allEls = document.querySelectorAll('span, div, p, td, h3, h4, label');
    const visited = new Set();
    for (const el of allEls) {
      if (visited.has(el)) continue;
      const text = el.innerText?.trim();
      if (!text || text.length > 50) continue;

      const sibling = el.nextElementSibling;
      if (sibling) {
        const sibText = sibling.innerText?.trim();
        if (sibText && /^[\d,.\-+%万亿千百元￥]+$/.test(sibText.replace(/\s/g, ''))) {
          if (!visited.has(sibling) && text.length < 20) {
            metrics[text] = sibText;
            visited.add(el);
            visited.add(sibling);
          }
        }
      }
    }

    // 方法2：正则匹配关键指标
    const bodyText = document.body.innerText;
    const patterns = [
      [/展现量[：:\s]*([0-9,]+)/, '展现量'],
      [/阅读量[：:\s]*([0-9,]+)/, '阅读量'],
      [/播放量[：:\s]*([0-9,]+)/, '播放量'],
      [/评论量[：:\s]*([0-9,]+)/, '评论量'],
      [/点赞量[：:\s]*([0-9,]+)/, '点赞量'],
      [/收藏量[：:\s]*([0-9,]+)/, '收藏量'],
      [/转发量[：:\s]*([0-9,]+)/, '转发量'],
      [/关注量[：:\s]*([0-9,]+)/, '关注量'],
      [/粉丝[总数量]*[：:\s]*([0-9,]+)/, '粉丝数'],
      [/涨粉[数]*[：:\s]*([0-9,]+)/, '涨粉数'],
      [/掉粉[数]*[：:\s]*([0-9,]+)/, '掉粉数'],
      [/总收入[：:\s]*([\d,.]+)/, '总收入'],
      [/昨日收入[：:\s]*([\d,.]+)/, '昨日收入'],
      [/累计收入[：:\s]*([\d,.]+)/, '累计收入'],
      [/预估收入[：:\s]*([\d,.]+)/, '预估收入'],
      [/基础收益[：:\s]*([\d,.]+)/, '基础收益'],
      [/创作收益[：:\s]*([\d,.]+)/, '创作收益'],
      [/发文量[：:\s]*([0-9,]+)/, '发文量'],
    ];
    for (const [re, label] of patterns) {
      const m = bodyText.match(re);
      if (m && !metrics[label]) {
        metrics[label] = m[1];
      }
    }

    return metrics;
  });
}
