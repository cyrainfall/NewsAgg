/**
 * Bilibili Bridge - Background Service Worker
 *
 * 连接本地 Python bridge server（ws://localhost:9335），接收命令并在用户的真实
 * 浏览器里执行，返回结果。所有操作都发生在用户已登录的标签页中，不使用任何
 * 无头浏览器或伪造指纹。
 *
 * 端口固定为 9335：B站技能、抖音技能（9334）、小红书技能（9333）各自独立，
 * 避免多个扩展抢同一个 bridge 连接导致命令被路由到错误的站点。
 *
 * 命令分组：
 * - navigate / wait_for_load : chrome.tabs.update + onUpdated
 * - evaluate / has_element / get_* : chrome.scripting.executeScript (MAIN world)
 * - click / type / press_key : chrome.debugger + CDP Input，产生 isTrusted 事件
 * - set_file_input : chrome.debugger + CDP DOM.setFileInputFiles（本地绝对路径）
 * - screenshot_element / get_cookies
 *
 * 已知限制：所有 DOM 查询与 CDP 操作都作用于**顶层文档**。B站创作中心投稿页是
 * Vue SPA，正常渲染在主文档里；若某天上传区被放进 iframe（旧版曾有
 * name="videoUpload" 的 iframe），probe 的 iframe_report 会把 iframe 列出来，
 * 但本文件里的命令不会进入 iframe。真遇到时按 CLAUDE.md 的"iframe"一节处理。
 */

const BRIDGE_URL = "ws://localhost:9335"
const BILIBILI_URLS = ["https://member.bilibili.com/*", "https://*.bilibili.com/*"]
const CREATOR_HOME = "https://member.bilibili.com/platform/home"

let ws = null

// 有开放 WebSocket 时 service worker 不会被回收，alarm 仅作保底。
chrome.alarms.create("keepAlive", { periodInMinutes: 0.4 })
chrome.alarms.onAlarm.addListener(() => {
  if (!ws || ws.readyState !== WebSocket.OPEN) connect()
})

function setStatus(connected) {
  chrome.storage.session.set({ wsConnected: connected }).catch(() => {})
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "GET_STATUS") {
    sendResponse({
      success: true,
      status: { wsConnected: ws !== null && ws.readyState === WebSocket.OPEN },
    })
    return true
  }
  return false
})

// ───────────────────────── WebSocket ─────────────────────────

function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return

  ws = new WebSocket(BRIDGE_URL)

  ws.onopen = () => {
    console.log("[Bilibili Bridge] 已连接到 bridge server")
    ws.send(JSON.stringify({ role: "extension" }))
    setStatus(true)
  }

  ws.onmessage = async (event) => {
    let msg
    try {
      msg = JSON.parse(event.data)
    } catch {
      return
    }
    try {
      const result = await handleCommand(msg)
      ws.send(JSON.stringify({ id: msg.id, result: result ?? null }))
    } catch (err) {
      ws.send(JSON.stringify({ id: msg.id, error: String(err.message || err) }))
    }
  }

  ws.onclose = () => {
    console.log("[Bilibili Bridge] 连接断开，3s 后重连...")
    setStatus(false)
    setTimeout(connect, 3000)
  }

  ws.onerror = (e) => console.error("[Bilibili Bridge] WS 错误", e)
}

// ───────────────────────── 命令路由 ─────────────────────────

async function handleCommand(msg) {
  const { method, params = {} } = msg

  switch (method) {
    case "navigate":
      return await cmdNavigate(params)
    case "wait_for_load":
      return await cmdWaitForLoad(params)

    case "screenshot_element":
      return await cmdScreenshotElement(params)

    case "set_file_input":
      return await cmdSetFileInputViaDebugger(params)

    case "click_element":
    case "click_nth_element":
    case "click_element_by_text":
      return await cmdClickViaDebugger(method, params)

    case "press_key":
      return await cmdPressKeyViaDebugger(params)

    case "type_text":
      return await cmdTypeTextViaDebugger(params)

    case "get_cookies":
      return await cmdGetCookies(params)

    case "get_page_info":
      return await cmdGetPageInfo()

    case "evaluate":
    case "wait_dom_stable":
    case "wait_for_selector":
    case "has_element":
    case "get_elements_count":
    case "get_element_text":
    case "get_element_attribute":
    case "get_elements_info":
    case "get_url":
    case "get_iframes":
    case "get_scroll_top":
    case "get_viewport_height":
      return await cmdEvaluateInMainWorld(method, params)

    // 其余方法都落到 domExecutor（见 cmdDomInMainWorld）。
    // ⚠️ 新增 MAIN world 方法时，必须在上面的 case 列表里也加一行；否则它会掉进
    //    下面这个 default，被 domExecutor 报成「未知 DOM 命令」—— 而"方法名在
    //    文件里出现过"这种粗检查是查不出来的，得看它是否真的被路由到。
    default:
      return await cmdDomInMainWorld(method, params)
  }
}

// ───────────────────────── Tab 管理 ─────────────────────────

async function getOrOpenBilibiliTab() {
  const tabs = await chrome.tabs.query({ url: BILIBILI_URLS })
  if (tabs.length > 0) {
    // 优先复用创作中心标签页，其次任意 B站标签页 —— 否则会影响用户正在看的视频页
    const creatorTab = tabs.find((t) => (t.url || "").includes("member.bilibili.com"))
    return creatorTab || tabs[0]
  }
  const tab = await chrome.tabs.create({ url: CREATOR_HOME })
  await waitForTabComplete(tab.id, 30000)
  return tab
}

// 从未观察到 "loading" 时的宽限期。
//
// 为什么需要它：导航可能在我们开始监听**之前**就已经开始了（典型的
// `executeScript` 里执行 `location.href = ...` 之后才去注册 onUpdated），
// loading 事件被错过。此时标签页是 "complete"、sawLoading 是 false，如果非要等到
// 超时才兜底放行，一次导航就会白等 60s（实测真实发生：navigate 60s + 随后的
// wait_for_load 180s = 240s 全程静默，看起来就是卡死）。
//
// 宽限期这么短是安全的：真正的导航会在几十到几百毫秒内把状态变成 "loading"，
// 一旦看到 loading，计时就从那一刻重新开始算。
const LOAD_NOT_STARTED_GRACE_MS = 2500

/**
 * 等到标签页真正加载完。
 *
 * 两个方向都要防：
 *
 * 1. **不能过早返回** —— `chrome.tabs.update({url})` 返回后，标签页有几十到几百
 *    毫秒仍处于上一次导航的 "complete" 状态。只判 status === "complete" 会在那一刻
 *    立刻返回，后续 DOM 查询全部落在**旧页面**上（表现为"元素找不到，但页面上明明
 *    有"）。所以要求至少观察到一次 "loading"，否则要等满宽限期。
 * 2. **不能白等** —— 见上面 LOAD_NOT_STARTED_GRACE_MS 的说明。
 *
 * 刻意不按 host 是否匹配来判断：未登录时 member.bilibili.com 会 302 到
 * passport.bilibili.com，那也是一次成功的导航，只是落在了登录页 —— 该由调用方
 * 用文本判断登录态，而不是在这里当成超时。
 */
async function waitForTabComplete(tabId, timeout) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeout
    let sawLoading = false
    let completeSince = null

    function stop() {
      chrome.tabs.onUpdated.removeListener(onUpdated)
    }
    function onUpdated(id, info) {
      if (id !== tabId) return
      if (info.status === "loading") {
        sawLoading = true
        completeSince = null
      } else if (info.status === "complete" && sawLoading) {
        stop()
        resolve()
      }
    }

    chrome.tabs.onUpdated.addListener(onUpdated)

    const poll = async () => {
      const tab = await chrome.tabs.get(tabId).catch(() => null)
      if (!tab) {
        stop()
        reject(new Error("标签页已关闭"))
        return
      }

      if (tab.status === "loading") {
        sawLoading = true
        completeSince = null
      } else if (tab.status === "complete") {
        if (sawLoading) {
          stop()
          resolve()
          return
        }
        completeSince = completeSince ?? Date.now()
        if (Date.now() - completeSince >= LOAD_NOT_STARTED_GRACE_MS) {
          // 一直都 complete、也没见过 loading：这次导航要么早就结束了，
          // 要么压根没发生。放行，不要再耗满超时。
          stop()
          resolve()
          return
        }
      }

      if (Date.now() > deadline) {
        stop()
        if (tab.status === "complete") resolve()
        else reject(new Error("页面加载超时"))
        return
      }
      setTimeout(poll, 300)
    }
    setTimeout(poll, 300)
  })
}

// ───────────────────────── 导航 ─────────────────────────

async function cmdNavigate({ url }) {
  const tab = await getOrOpenBilibiliTab()

  // 目标与当前 URL 相同时必须显式 reload。
  // 给 location.href 赋一个相同的值，Chrome 有时会当成 no-op —— 那样页面还是旧的
  // 状态（带着上一次上传的视频、已填的文案），脚本却以为"已经在新页面上了"。
  const sameUrl = (tab.url || "").split("#")[0] === url.split("#")[0]

  // 已在 bilibili.com 时用页面内 location 跳转，保持 same-origin 导航特征，
  // 与真实用户点击站内链接的行为一致。
  const isOnBilibili = tab.url && tab.url.includes("bilibili.com")
  if (sameUrl) {
    await chrome.tabs.reload(tab.id)
  } else if (isOnBilibili) {
    await chrome.scripting
      .executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: (targetUrl) => {
          window.location.href = targetUrl
        },
        args: [url],
      })
      .catch(() => {})
  } else {
    await chrome.tabs.update(tab.id, { url })
  }

  await waitForTabComplete(tab.id, 60000)

  // 后台标签页会被暂停渲染，覆盖 visibilityState 让页面始终认为自己在前台。
  await chrome.scripting
    .executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: () => {
        try {
          Object.defineProperty(document, "visibilityState", {
            get: () => "visible",
            configurable: true,
          })
          Object.defineProperty(document, "hidden", { get: () => false, configurable: true })
          document.dispatchEvent(new Event("visibilitychange"))
        } catch (_) {}
      },
    })
    .catch(() => {})

  return null
}

async function cmdWaitForLoad({ timeout = 60000 }) {
  const tab = await getOrOpenBilibiliTab()
  await waitForTabComplete(tab.id, timeout)
  return null
}

async function cmdGetPageInfo() {
  const tab = await getOrOpenBilibiliTab()
  return { url: tab.url || "", title: tab.title || "", tabId: tab.id }
}

// ───────────────────────── 截图 ─────────────────────────

async function cmdScreenshotElement({ selector, padding = 0 }) {
  const tab = await getOrOpenBilibiliTab()
  const target = { tabId: tab.id }

  await chrome.debugger.detach(target).catch(() => {})
  await chrome.debugger.attach(target, "1.3")
  try {
    const boxOut = await chrome.debugger.sendCommand(target, "Runtime.evaluate", {
      expression: `(() => {
        const el = document.querySelector(${JSON.stringify(selector)});
        if (!el) return null;
        el.scrollIntoView({ block: "center", behavior: "instant" });
        const r = el.getBoundingClientRect();
        return {
          x: r.left + window.scrollX,
          y: r.top + window.scrollY,
          width: r.width,
          height: r.height,
        };
      })()`,
      returnByValue: true,
    })
    const box = boxOut?.result?.value
    if (!box || box.width <= 0 || box.height <= 0) return { data: "" }

    const shot = await chrome.debugger.sendCommand(target, "Page.captureScreenshot", {
      format: "png",
      clip: {
        x: Math.max(0, box.x - padding),
        y: Math.max(0, box.y - padding),
        width: box.width + padding * 2,
        height: box.height + padding * 2,
        scale: 1.0,
      },
    })
    return { data: shot.data || "" }
  } finally {
    await chrome.debugger.detach(target).catch(() => {})
  }
}

// ───────────────────────── Cookies ─────────────────────────

async function cmdGetCookies({ domain = "bilibili.com" }) {
  return await chrome.cookies.getAll({ domain })
}

// ───────────────────────── MAIN world JS 执行 ─────────────────────────

async function cmdEvaluateInMainWorld(method, params) {
  const tab = await getOrOpenBilibiliTab()
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: mainWorldExecutor,
    args: [method, params],
  })
  const r = results?.[0]?.result
  if (r && typeof r === "object" && "__bl_error" in r) throw new Error(r.__bl_error)
  return r
}

/**
 * 在页面主 world 运行，可访问 window.__INITIAL_STATE__ 等页面全局变量。
 * 注意：此函数被序列化后注入页面，不能引用外部变量。
 */
function mainWorldExecutor(method, params) {
  function poll(check, interval, timeout) {
    return new Promise((resolve, reject) => {
      const start = Date.now()
      ;(function tick() {
        const result = check()
        if (result !== false && result !== null && result !== undefined) {
          resolve(result)
          return
        }
        if (Date.now() - start >= timeout) {
          reject(new Error("超时"))
          return
        }
        setTimeout(tick, interval)
      })()
    })
  }

  switch (method) {
    case "evaluate": {
      try {
        // eslint-disable-next-line no-new-func
        return Function(`"use strict"; return (${params.expression})`)()
      } catch (e) {
        return { __bl_error: `JS执行错误: ${e.message}` }
      }
    }

    case "has_element":
      return document.querySelector(params.selector) !== null

    case "get_elements_count":
      return document.querySelectorAll(params.selector).length

    case "get_element_text": {
      const el = document.querySelector(params.selector)
      return el ? el.textContent.trim() : null
    }

    case "get_element_attribute": {
      const el = document.querySelector(params.selector)
      return el ? el.getAttribute(params.attr) : null
    }

    case "get_elements_info": {
      return Array.from(document.querySelectorAll(params.selector)).map((el) => {
        const info = { text: (el.textContent || "").trim().slice(0, 120) }
        if (params.attrs) for (const a of params.attrs) info[a] = el.getAttribute(a)
        return info
      })
    }

    case "get_url":
      return window.location.href

    case "get_scroll_top":
      return window.pageYOffset || document.documentElement.scrollTop || 0

    case "get_viewport_height":
      return window.innerHeight

    case "get_iframes": {
      // 只报告，不进入 —— 见文件头"已知限制"。
      return Array.from(document.querySelectorAll("iframe")).map((f) => {
        const r = f.getBoundingClientRect()
        return {
          name: f.getAttribute("name") || "",
          src: f.getAttribute("src") || "",
          width: Math.round(r.width),
          height: Math.round(r.height),
        }
      })
    }

    case "wait_dom_stable": {
      const timeout = params.timeout || 10000
      const interval = params.interval || 500
      return new Promise((resolve) => {
        let last = -1
        const start = Date.now()
        ;(function tick() {
          const size = document.body ? document.body.innerHTML.length : 0
          if (size === last && size > 0) {
            resolve(null)
            return
          }
          last = size
          if (Date.now() - start >= timeout) {
            resolve(null)
            return
          }
          setTimeout(tick, interval)
        })()
      })
    }

    case "wait_for_selector": {
      const timeout = params.timeout || 30000
      return poll(() => !!document.querySelector(params.selector), 200, timeout).catch(() => {
        throw new Error(`等待元素超时: ${params.selector}`)
      })
    }

    default:
      return { __bl_error: `未知 MAIN world 方法: ${method}` }
  }
}

// ───────────────────────── 真实鼠标点击（chrome.debugger + CDP） ─────────

// 在视口坐标 (x, y) 派发真实鼠标事件序列：mouseMoved（轨迹）+ pressed + released。
// 真事件走渲染层，能穿透 shadow DOM，且 isTrusted=true。
async function _dispatchRealClickAt(target, x, y) {
  const startX = x - 20
  const startY = y - 45
  const steps = 5
  for (let i = 1; i <= steps; i++) {
    const t = i / steps
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mouseMoved",
      x: Math.round(startX + (x - startX) * t),
      y: Math.round(startY + (y - startY) * t),
      button: "none",
      buttons: 0,
      modifiers: 0,
    })
    await sleep(8)
  }

  const base = { x, y, button: "left", buttons: 1, clickCount: 1, modifiers: 0 }
  await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
    ...base,
    type: "mousePressed",
  })
  await sleep(30)
  await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
    ...base,
    type: "mouseReleased",
    buttons: 0,
  })
}

async function cmdClickViaDebugger(method, { selector, index, text }) {
  const tab = await getOrOpenBilibiliTab()
  const target = { tabId: tab.id }

  let findExpr
  if (method === "click_nth_element") {
    findExpr = `document.querySelectorAll(${JSON.stringify(selector)})[${index}] || null`
  } else if (method === "click_element_by_text") {
    findExpr =
      `Array.from(document.querySelectorAll(${JSON.stringify(selector)}))` +
      `.find(e => (e.textContent || "").trim().includes(${JSON.stringify(text)})) || null`
  } else {
    findExpr = `document.querySelector(${JSON.stringify(selector)})`
  }

  await chrome.debugger.detach(target).catch(() => {})
  await chrome.debugger.attach(target, "1.3")
  try {
    const evalResult = await chrome.debugger.sendCommand(target, "Runtime.evaluate", {
      expression: `(() => {
        const el = ${findExpr};
        if (!el) return null;

        // 把元素真的滚到看得见的位置。
        //
        // 只调 scrollIntoView 是不够的：B站的分区面板是「外层 absolute 容器 + 内层
        // overflow:auto 列表」，实测 scrollIntoView 之后条目依然被内层容器裁掉
        // （元素 rect 的 top 是负数），于是下面的坐标落在视口外，点击什么也不会发生。
        // 所以再沿着祖先链，把所有可滚动容器逐个滚到位。
        el.scrollIntoView({ block: "center", behavior: "instant" });
        for (let cur = el.parentElement; cur && cur !== document.body; cur = cur.parentElement) {
          const st = window.getComputedStyle(cur);
          if (!/(auto|scroll)/.test(st.overflowY + st.overflowX)) continue;
          if (cur.scrollHeight > cur.clientHeight) {
            const cr = cur.getBoundingClientRect();
            const r = el.getBoundingClientRect();
            cur.scrollTop += r.top - cr.top - (cr.height - r.height) / 2;
          }
          if (cur.scrollWidth > cur.clientWidth) {
            const cr = cur.getBoundingClientRect();
            const r = el.getBoundingClientRect();
            cur.scrollLeft += r.left - cr.left - (cr.width - r.width) / 2;
          }
        }

        const r = el.getBoundingClientRect();
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        const desc = (n) => n
          ? (n.tagName + (n.className && typeof n.className === "string"
              ? "." + n.className.trim().split(/\\s+/).slice(0, 2).join(".") : ""))
          : "null";

        // 坐标必须在视口内。落在视口外的点击会被浏览器直接丢弃 —— 页面毫无反应，
        // 脚本却以为点过了（真实踩过：分区条目被内层容器裁掉，点击静默失效）。
        if (cx < 0 || cy < 0 || cx > window.innerWidth || cy > window.innerHeight) {
          return {
            offscreen: true,
            target: desc(el),
            x: Math.round(cx), y: Math.round(cy),
            viewport: window.innerWidth + "x" + window.innerHeight,
          };
        }

        // 真实鼠标事件只知道坐标，不知道"你想点谁"。所以必须在这里确认：这个坐标上
        // 最上层的元素，到底是不是目标（或它的后代/祖先）。
        //
        // 祖先也算通过，是因为有些元素自己不接收指针事件（pointer-events:none），
        // 事件由外层容器接手 —— 那是正常情况。
        //
        // 既不是后代也不是祖先，就说明有东西（弹窗、浮层面板）盖在目标上面。此时
        // 在同样的坐标上点击会**打在浮层上**：页面看起来毫无反应，实际上点了别的
        // 东西（真实踩过：分区面板没关，点标签框点到了面板条目，把分区改掉了）。
        //
        // 另外 elementFromPoint 返回 null 也要拦下来 —— 那说明这个点没有可命中的
        // 元素，点下去同样是白点。以前只判了 hit 非空，等于把这种情况放过去了。
        const hit = document.elementFromPoint(cx, cy);
        if (!hit) {
          return { offscreen: true, target: desc(el), x: Math.round(cx), y: Math.round(cy), hitNull: true };
        }
        if (!el.contains(hit) && !hit.contains(el)) {
          return {
            blocked: true,
            x: cx, y: cy,
            target: desc(el),
            blocker: desc(hit),
            blockerText: (hit.textContent || "").trim().slice(0, 40),
          };
        }

        const t = (hit === el || el.contains(hit)) ? hit : el;
        return { x: cx, y: cy, tag: t.tagName, disabled: !!t.disabled };
      })()`,
      returnByValue: true,
    })

    const pos = evalResult?.result?.value
    if (!pos) throw new Error(`元素不存在: ${JSON.stringify({ selector, index, text })}`)
    if (pos.blocked) {
      throw new Error(
        `目标元素被遮挡，已放弃点击（避免点到浮层上）：目标 ${pos.target}，` +
          `该坐标上实际是 ${pos.blocker}「${pos.blockerText}」。` +
          `请先关闭页面上打开的弹窗/浮层面板后重试。`,
      )
    }
    if (pos.offscreen) {
      throw new Error(
        `目标元素不在可点击范围内，已放弃点击（否则坐标会落在视口外，白点一次）：` +
          `目标 ${pos.target}，坐标 (${pos.x}, ${pos.y})，视口 ${pos.viewport}` +
          `${pos.hitNull ? "，该点无可命中元素" : ""}。` +
          `通常是元素在一个没滚到位的可滚动容器里（长列表、弹窗面板），或还没渲染出来。`,
      )
    }
    if (pos.x <= 0 && pos.y <= 0) {
      throw new Error(`元素不可点击（坐标为 0，可能被隐藏或未渲染）: ${selector}`)
    }

    await _dispatchRealClickAt(target, pos.x, pos.y)
    return { clicked: true, ...pos }
  } finally {
    await chrome.debugger.detach(target).catch(() => {})
  }
}

// ─────── 真实键盘（chrome.debugger + CDP Input.dispatchKeyEvent） ───────

const KEY_MAP = {
  Enter: { key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" },
  Tab: { key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 },
  Backspace: { key: "Backspace", code: "Backspace", windowsVirtualKeyCode: 8 },
  Delete: { key: "Delete", code: "Delete", windowsVirtualKeyCode: 46 },
  Escape: { key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 },
  ArrowDown: { key: "ArrowDown", code: "ArrowDown", windowsVirtualKeyCode: 40 },
  ArrowUp: { key: "ArrowUp", code: "ArrowUp", windowsVirtualKeyCode: 38 },
  ArrowLeft: { key: "ArrowLeft", code: "ArrowLeft", windowsVirtualKeyCode: 37 },
  ArrowRight: { key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 },
  Space: { key: " ", code: "Space", windowsVirtualKeyCode: 32, text: " " },
}

/**
 * 按下并释放按键。统一走 CDP 真实键盘事件（isTrusted=true）。
 *
 * 按键就是"按下再抬起"这么一件小事，但必须走真实事件：B站的标签输入框靠
 * keydown 里的 Enter 创建标签，程序化派发的 KeyboardEvent 到不了 Vue 的
 * handler，标签会静静地留在输入框里没被创建。
 */
async function cmdPressKeyViaDebugger({ key }) {
  const tab = await getOrOpenBilibiliTab()
  const info = KEY_MAP[key] || {
    key,
    code: `Key${key.toUpperCase()}`,
    windowsVirtualKeyCode: key.charCodeAt(0),
  }
  const target = { tabId: tab.id }
  await chrome.debugger.attach(target, "1.3")
  try {
    const base = { modifiers: 0, ...info }
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", { ...base, type: "keyDown" })
    await sleep(30)
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", { ...base, type: "keyUp" })
  } finally {
    await chrome.debugger.detach(target).catch(() => {})
  }
  return null
}

// ─────── 真实文字输入（chrome.debugger + CDP Input.insertText） ───────

async function cmdTypeTextViaDebugger({ text, delayMs = 50 }) {
  const tab = await getOrOpenBilibiliTab()

  const ceResult = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: () => document.activeElement?.isContentEditable ?? false,
  })
  const inCE = ceResult?.[0]?.result

  if (inCE) {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: async (chars, delay) => {
        function sleep(ms) {
          return new Promise((r) => setTimeout(r, ms))
        }
        for (const char of chars) {
          document.execCommand("insertText", false, char)
          await sleep(delay)
        }
      },
      args: [[...text], delayMs],
    })
    return null
  }

  const target = { tabId: tab.id }
  await chrome.debugger.attach(target, "1.3")
  try {
    for (const char of text) {
      await chrome.debugger.sendCommand(target, "Input.insertText", { text: char })
      await sleep(delayMs)
    }
  } finally {
    await chrome.debugger.detach(target).catch(() => {})
  }
  return null
}

// ───────────────────────── 文件上传（chrome.debugger + CDP） ─────────

async function cmdSetFileInputViaDebugger({ selector, files }) {
  const tab = await getOrOpenBilibiliTab()
  const target = { tabId: tab.id }

  await chrome.debugger.attach(target, "1.3")
  try {
    const { root } = await chrome.debugger.sendCommand(target, "DOM.getDocument", { depth: 0 })
    const { nodeId } = await chrome.debugger.sendCommand(target, "DOM.querySelector", {
      nodeId: root.nodeId,
      selector,
    })
    if (!nodeId) throw new Error(`文件输入框不存在: ${selector}`)
    await chrome.debugger.sendCommand(target, "DOM.setFileInputFiles", {
      nodeId,
      files, // Python 侧传入的本地绝对路径数组
    })
  } finally {
    await chrome.debugger.detach(target).catch(() => {})
  }
  return null
}

// ───────────────────────── DOM 操作（MAIN world） ────────────────────

async function cmdDomInMainWorld(method, params) {
  const tab = await getOrOpenBilibiliTab()
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: domExecutor,
    args: [method, params],
  })
  const r = results?.[0]?.result
  if (r && typeof r === "object" && "__bl_error" in r) throw new Error(r.__bl_error)
  return r ?? null
}

/**
 * DOM 操作执行器，在页面 MAIN world 运行。
 * 不能引用外部变量，所有逻辑自包含。
 */
function domExecutor(method, params) {
  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms))
  }

  function requireEl(selector) {
    const el = document.querySelector(selector)
    if (!el) return { __bl_error: `元素不存在: ${selector}` }
    return el
  }

  switch (method) {
    case "input_text": {
      const el = requireEl(params.selector)
      if (el.__bl_error) return el
      el.focus()
      // Vue / React 受控组件需要走原生 setter，否则框架状态不会更新。
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set
      if (setter) setter.call(el, params.text)
      else el.value = params.text
      el.dispatchEvent(new Event("input", { bubbles: true }))
      el.dispatchEvent(new Event("change", { bubbles: true }))
      return null
    }

    case "input_content_editable": {
      return new Promise(async (resolve) => {
        const el = document.querySelector(params.selector)
        if (!el) {
          resolve({ __bl_error: `元素不存在: ${params.selector}` })
          return
        }
        el.focus()
        document.execCommand("selectAll", false, null)
        document.execCommand("delete", false, null)
        await sleep(80)
        // 分段写入，段之间用 insertParagraph 换行。
        // ⚠️ 不是所有富文本编辑器都支持程序化换行（抖音的编辑器就完全不支持）。
        //    调用方必须**回读校验**，见 publish_video 里的简介回读逻辑。
        const lines = params.text.split("\n")
        for (let i = 0; i < lines.length; i++) {
          if (lines[i]) document.execCommand("insertText", false, lines[i])
          if (i < lines.length - 1) {
            document.execCommand("insertParagraph", false, null)
            await sleep(30)
          }
        }
        resolve(null)
      })
    }

    case "scroll_by":
      window.scrollBy(params.x || 0, params.y || 0)
      return null
    case "scroll_to":
      window.scrollTo(params.x || 0, params.y || 0)
      return null
    case "scroll_to_bottom":
      window.scrollTo(0, document.body.scrollHeight)
      return null

    case "scroll_element_into_view": {
      const el = document.querySelector(params.selector)
      if (el) el.scrollIntoView({ behavior: "instant", block: "center" })
      return null
    }

    case "remove_element": {
      const el = document.querySelector(params.selector)
      if (el) el.remove()
      return null
    }

    case "hover_element": {
      const el = document.querySelector(params.selector)
      if (el) {
        const rect = el.getBoundingClientRect()
        const x = rect.left + rect.width / 2
        const y = rect.top + rect.height / 2
        el.dispatchEvent(new MouseEvent("mouseover", { clientX: x, clientY: y, bubbles: true }))
        el.dispatchEvent(new MouseEvent("mousemove", { clientX: x, clientY: y, bubbles: true }))
      }
      return null
    }

    case "select_all_text": {
      const el = document.querySelector(params.selector)
      if (el) {
        el.focus()
        if (el.select) el.select()
        else document.execCommand("selectAll")
      }
      return null
    }

    default:
      return { __bl_error: `未知 DOM 命令: ${method}` }
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms))
}

// ───────────────────────── 启动 ─────────────────────────

connect()
