import { readFileSync } from 'fs';
import { marked } from 'marked';
import { launchBrowser, closeBrowser, sleep, waitForStable, dismissOverlays } from './browser.js';
import { ensureLoggedIn } from './auth-guard.js';

const PUBLISH_URL = 'https://mp.toutiao.com/profile_v4/graphic/publish';
const TITLE_MAX_LEN = 30;
const TITLE_MIN_LEN = 2;

/**
 * 发布图文文章。
 * 参数:
 *   --title          文章标题（必填）
 *   --content        正文文本
 *   --content-file   从文件读取正文
 *   --cover          封面图片路径（必填，单图模式）
 *   --cover-mode     封面模式: single / triple / none（默认 single）
 *   --first-publish  勾选"头条首发"
 *   --collection     添加至合集名称
 *   --no-weitoutiao  取消"同时发布微头条"
 *   --declaration    作品声明，逗号分隔
 *   --draft          存草稿
 */
export async function publishArticle(opts) {
  const { context, page } = await launchBrowser(opts);
  try {
    await ensureLoggedIn(page);
    await page.goto(PUBLISH_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await waitForStable(page);
    await sleep(1500, 2500);
    await dismissOverlays(page);

    // ── 标题（2~30 字） ──
    let title = opts.title;
    if (title.length < TITLE_MIN_LEN) {
      throw new Error(`标题过短：至少 ${TITLE_MIN_LEN} 个字，当前 ${title.length} 个字`);
    }
    if (title.length > TITLE_MAX_LEN) {
      title = title.slice(0, TITLE_MAX_LEN);
      process.stderr.write(`[warn] 标题超过 ${TITLE_MAX_LEN} 字限制，已自动截断为：${title}\n`);
    }
    const titleSelector = 'textarea[placeholder*="标题"], input[placeholder*="标题"], [class*="title"] textarea, [class*="title"] input';
    await page.waitForSelector(titleSelector, { timeout: 15000 });
    await sleep(300, 600);
    // NewsAgg patch (ADR-0051)：逐字输入必须读回校验。
    // 2026-10-21 实测：标题「中间层岗位已经换成外包」被平台收成「中间层位」，
    // 少了一个「岗」字——`keyboard.type` 没有抛异常，调用方拿到的 `title`
    // 仍是本地变量（正确的那一份），于是发布"成功"而反查按原标题查不到，
    // 把一次真实发布报成 not_published_needs_review。
    // 这里输入后读回 DOM 值比对，不一致就重试；仍不一致则用 fill 直接设值。
    const typedTitle = await typeTitleVerified(page, titleSelector, title);
    if (typedTitle !== title) {
      process.stderr.write(
        `[warn] 标题读回与预期不一致：预期 ${title.length} 字，实际 ${typedTitle.length} 字\n` +
        `  预期：${title}\n  实际：${typedTitle}\n`,
      );
    }
    // 平台实际收到的标题（ADR-0051）：反查必须按**这个**去查，否则一次
    // 真实发布会因为标题被吞字而被判成「查不到」，导向错误的人工动作。
    const titleOnPage = typedTitle || title;
    await sleep(500, 1000);

    // ── 正文 ──
    let content = opts.content || '';
    if (opts.contentFile) {
      content = readFileSync(opts.contentFile, 'utf-8');
    }
    content = content.replace(/\\n/g, '\n');
    if (content) {
      const editorSelector = '[contenteditable="true"]';
      await page.waitForSelector(editorSelector, { timeout: 15000 });
      await sleep(300, 600);
      await page.click(editorSelector, { force: true });
      await sleep(200, 400);

      if (opts.format === 'markdown' || opts.contentFile?.match(/\.md$/i)) {
        await pasteMarkdownAsRichText(page, content, editorSelector);
      } else {
        await typePlainText(page, content);
      }
    }
    await sleep(500, 1000);

    // ── 展示封面 ──
    await setCoverMode(page, opts.coverMode || 'single', opts.cover);
    await sleep(500, 1000);

    // ── 声明首发 ──
    if (opts.firstPublish) {
      await clickLabel(page, '头条首发');
    }
    await sleep(300, 500);

    // ── 合集 ──
    if (opts.collection) {
      await addToCollection(page, opts.collection);
    }
    await sleep(300, 500);

    // ── 同时发布微头条（默认已勾选，--no-weitoutiao 取消） ──
    if (opts.weitoutiao === false) {
      await uncheckWeitoutiao(page);
    }
    await sleep(300, 500);

    // ── 作品声明 ──
    if (opts.declaration) {
      await setDeclarations(page, opts.declaration);
    }
    await sleep(500, 1000);

    // ── 发布 / 草稿 ──
    await dismissOverlays(page);
    if (opts.draft) {
      // 页面底部没有独立草稿按钮，草稿已自动保存
      return {
        success: true,
        action: 'draft_saved',
        title: titleOnPage,
        expected_title: title,
        url: page.url(),
      };
    }

    // 点击"预览并发布"按钮
    await dismissOverlays(page);
    const publishBtn = page.locator('button:has-text("预览并发布")').first();
    await publishBtn.scrollIntoViewIfNeeded().catch(() => {});
    await sleep(300, 500);
    await publishBtn.click({ force: true, timeout: 10000 });
    await sleep(3000, 5000);
    await waitForStable(page);

    // 预览页面需要再次点击"确认发布"
    const confirmPublish = page.locator('button:has-text("确认发布"), button:has-text("发布")').first();
    await confirmPublish.click({ timeout: 10000 }).catch(() => {});
    await sleep(2000, 4000);

    // 可能还有二次确认弹窗
    const confirmBtn = page.locator('button:has-text("确定"), button:has-text("确认")').first();
    await confirmBtn.click({ timeout: 5000 }).catch(() => {});

    await sleep(2000, 4000);
    await waitForStable(page);

    return {
      success: true,
      action: 'published',
      title: titleOnPage,
      expected_title: title,
      title_truncated_by_platform: titleOnPage !== title,
      url: page.url(),
    };
  } finally {
    await closeBrowser(context);
  }
}

/**
 * 输入标题并**读回校验**（ADR-0051）。
 *
 * 为什么需要：`page.keyboard.type(text, {delay})` 逐字敲键，平台输入框对
 * 中文输入有防抖/联想行为，个别字符会被吞掉。2026-10-21 实测标题
 * 「老板叫停AI裁员，中间层岗位已经换成外包」在平台上变成「…中间层位…」，
 * 少了一个「岗」字——而 `keyboard.type` 全程没有抛异常，返回给调用方的
 * `title` 是本地变量（正确的那一份）。于是：
 *   ① 发布动作"成功"，反查按原标题查不到 → 被误判成 not_published_needs_review；
 *   ② 即使反查通过，平台上挂着的标题也已经是错的。
 * 这是 ADR-0047「动作成功 ≠ 结果正确」的第二个实例，判据同一条：
 * **凡是有外部可见结果的输入，都必须读回产出物本身，而不是相信调用没报错。**
 *
 * 策略：先逐字敲；读回不一致则清空重敲一次；仍不一致则用 fill 直接设值
 * （fill 走一次性赋值，绕过按键路径）；最后无论如何返回实际值，由调用方决定。
 */
async function typeTitleVerified(page, selector, title) {
  const readBack = async () => {
    try {
      return (await page.inputValue(selector)) ?? '';
    } catch {
      return '';
    }
  };

  for (let attempt = 1; attempt <= 2; attempt += 1) {
    await page.click(selector, { force: true });
    await page.keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
    await page.keyboard.press('Backspace');
    await page.keyboard.type(title, { delay: 50 + Math.random() * 80 });
    await sleep(500, 1000);
    const got = (await readBack()).trim();
    if (got === title.trim()) return got;
    process.stderr.write(`[warn] 标题第 ${attempt} 次逐字输入被吞字，重试\n`);
  }

  // 逐字路径两次都失败 → 一次性赋值（fill 不经过按键，不会被输入法吞）
  try {
    await page.fill(selector, title);
    await sleep(500, 800);
  } catch (err) {
    process.stderr.write(`[warn] 标题 fill 失败：${err.message}\n`);
  }
  return (await readBack()).trim();
}

async function setCoverMode(page, mode, coverPath) {
  try {
    const modeLabels = {
      single: '单图',
      triple: '三图',
      none: '无封面',
    };
    const label = modeLabels[mode] || modeLabels.single;

    const radio = page.locator(`text=${label}`).first();
    await radio.click({ timeout: 5000 });
    await sleep(500, 800);

    if (mode !== 'none' && coverPath) {
      const paths = coverPath.split(',').map(p => p.trim()).filter(Boolean);

      // 点击封面区域的 + 号，打开图片上传侧边栏
      const coverArea = page.locator('[class*="cover"] [class*="add"], [class*="cover"] [class*="upload"], [class*="cover"] [class*="plus"]').first();
      await coverArea.click({ timeout: 5000 }).catch(async () => {
        // 备选：点击"预览"旁的 + 号
        await page.locator('[class*="cover-upload"]').first().click({ timeout: 3000 });
      });
      await sleep(1000, 2000);

      // 侧边栏打开后，点击"本地上传"按钮触发文件选择
      const [fileChooser] = await Promise.all([
        page.waitForEvent('filechooser', { timeout: 10000 }),
        page.locator('text=本地上传').first().click({ timeout: 5000 }),
      ]);

      if (fileChooser) {
        await fileChooser.setFiles(paths);
        await sleep(3000, 5000);
      }

      // 等待图片上传完成，点击"确定"关闭侧边栏
      const confirmBtn = page.locator('.byte-drawer-wrapper button:has-text("确定"), .upload-image-panel button:has-text("确定")').first();
      await confirmBtn.waitFor({ timeout: 10000 });
      await sleep(500, 800);
      await confirmBtn.click();
      await sleep(1000, 2000);
    }
  } catch {
    // 封面上传失败，尝试关闭可能残留的侧边栏
    await page.locator('.byte-drawer-wrapper button:has-text("取消")').first()
      .click({ timeout: 3000 }).catch(() => {});
    await page.keyboard.press('Escape').catch(() => {});
    await sleep(500, 800);
  }
}

async function addToCollection(page, collectionName) {
  try {
    const addBtn = page.locator('text=添加至合集').first();
    await addBtn.click({ timeout: 5000 });
    await sleep(500, 1000);

    // 在弹出的合集选择面板中搜索或选择
    const searchInput = page.locator('[class*="collection"] input, [class*="search"] input').first();
    await searchInput.fill(collectionName, { timeout: 5000 }).catch(async () => {
      // 没有搜索框，直接找匹配的合集名
    });
    await sleep(500, 1000);

    const item = page.locator(`text=${collectionName}`).first();
    await item.click({ timeout: 5000 });
    await sleep(300, 600);

    // 点确认
    const confirmBtn = page.locator('button:has-text("确定"), button:has-text("确认")').first();
    await confirmBtn.click({ timeout: 3000 }).catch(() => {});
  } catch {
    // 合集添加失败不阻塞
  }
}

/**
 * 读取某个勾选框的真实勾选状态。
 *
 * 头条发布页的勾选框是 `label.byte-checkbox`，**没有原生 input、没有
 * aria-checked**，状态只体现在 label 的 className 上（含 `byte-checkbox-checked`
 * 即为已勾选）。所以只能读 class，不能读 `checked` property。
 *
 * 返回 null 表示页面上找不到这个勾选框。
 */
async function readCheckboxState(page, labelText) {
  return page.evaluate((text) => {
    const labels = [...document.querySelectorAll('label.byte-checkbox')];
    const hit = labels.find((l) => (l.innerText || '').includes(text));
    if (!hit) return null;
    return String(hit.className).includes('byte-checkbox-checked');
  }, labelText);
}

async function uncheckWeitoutiao(page) {
  // ⚠️ 这里曾经是「无条件点一下」，注释说「默认已勾选，点击取消」。
  // 2026-10-19 的 DOM 探针实测：全新打开页面、填完标题正文之后，
  // 「发布得更多收益」始终是**未勾选**。于是那一次点击不是取消，而是**勾上**，
  // 导致 10-18 那篇同时发了文章和微头条（ADR-0034 判为「包装层漏传参数」是错的）。
  // 现在改为：先读状态，只有确实已勾选才点，点完再验一次。
  const label = '发布得更多收益';
  let before = await readCheckboxState(page, label);
  if (before === null) {
    // 有些账号/有些时候这个勾选框根本不存在。等一会儿再确认一次：
    // 若确实不存在，那就不可能有微头条随之发出，属于安全情形。
    await sleep(800, 1200);
    before = await readCheckboxState(page, label);
  }
  if (before === null) {
    process.stderr.write('[warn] 未找到「发布得更多收益」勾选框：视为该账号无此项，不发布微头条\n');
    return;
  }
  if (before === false) {
    return; // 已经是未勾选，不需要点（点了反而会勾上）
  }

  const checkbox = page.locator('text=' + label).first();
  await checkbox.scrollIntoViewIfNeeded().catch(() => {});
  await checkbox.click({ timeout: 5000 });
  await sleep(200, 400);

  const after = await readCheckboxState(page, label);
  if (after !== false) {
    // 不能静默通过：多发一条微头条是平台侧可见的重复内容。
    throw new Error(`取消「同时发布微头条」失败：点击后状态仍为 ${after}（true=已勾选）`);
  }
}

async function clickLabel(page, labelText) {
  try {
    const el = page.locator(`text=${labelText}`).first();
    await el.scrollIntoViewIfNeeded().catch(() => {});
    await el.click({ timeout: 5000 });
    await sleep(200, 400);
  } catch {}
}

async function setDeclarations(page, declarationStr) {
  const declarations = declarationStr.split(',').map(d => d.trim()).filter(Boolean);
  const labelMap = {
    '取材网络': '取材网络',
    '引用站内': '引用站内',
    '个人观点': '个人观点，仅供参考',
    '引用AI': '引用AI',
    '虚构演绎': '虚构演绎，故事经历',
    '投资观点': '投资观点，仅供参考',
    '健康医疗': '健康医疗分享，仅供参考',
  };

  for (const decl of declarations) {
    const fullLabel = labelMap[decl] || decl;
    const checkbox = page.locator(`text=${fullLabel}`).first();
    await checkbox.scrollIntoViewIfNeeded().catch(() => {});
    await checkbox.click({ timeout: 3000 });
    await sleep(200, 400);
  }

  // 读回校验：这里过去是 try{}catch{} 静默吞掉，点没点上「系统自己不知道」
  // （ADR-0046 已记录该风险）。声明是法规义务（《AI 生成合成内容标识办法》
  // 第十条），失败了必须让调用方知道，而不能当作发布成功。
  //
  // 另：实测声明之间是**单选互斥**（点「个人观点」后再点「引用AI」，
  // 前者会自动取消）。所以逗号分隔多个声明只有最后一个真正生效——
  // 这里显式报错，避免上层以为传多个就都生效。
  const states = [];
  for (const decl of declarations) {
    const fullLabel = labelMap[decl] || decl;
    states.push({ decl, checked: await readCheckboxState(page, fullLabel) });
  }
  const checkedList = states.filter((s) => s.checked === true).map((s) => s.decl);
  const unchecked = states.filter((s) => s.checked !== true).map((s) => s.decl);

  if (checkedList.length === 0) {
    throw new Error(
      `作品声明勾选失败：${declarations.join('、')} 均未生效（读回状态全部为未勾选）`
    );
  }
  if (unchecked.length > 0) {
    throw new Error(
      `作品声明未全部生效：已勾选 [${checkedList.join('、')}]，未勾选 [${unchecked.join('、')}]。` +
        '头条的声明是单选互斥的，一次只能选一个——请只传一个声明。'
    );
  }
}

async function pasteMarkdownAsRichText(page, markdownContent, editorSelector) {
  const html = marked.parse(markdownContent, { breaks: true, gfm: true });
  await page.evaluate(
    ({ html, selector }) => {
      const editor = document.querySelector(selector);
      if (!editor) return;
      editor.focus();
      const dt = new DataTransfer();
      dt.setData('text/html', html);
      dt.setData('text/plain', editor.textContent);
      const evt = new ClipboardEvent('paste', {
        clipboardData: dt,
        bubbles: true,
        cancelable: true,
      });
      editor.dispatchEvent(evt);
    },
    { html, selector: editorSelector },
  );
  await sleep(500, 1000);
}

async function typePlainText(page, content) {
  const paragraphs = content.split('\n');
  for (let i = 0; i < paragraphs.length; i++) {
    const para = paragraphs[i];
    if (para) {
      await page.keyboard.type(para, { delay: 30 + Math.random() * 50 });
    }
    if (i < paragraphs.length - 1) {
      await page.keyboard.press('Enter');
      await sleep(100, 300);
    }
  }
}
