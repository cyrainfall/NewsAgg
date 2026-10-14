/**
 * Douyin Bridge - Background Service Worker
 *
 * 连接本地 Python bridge server（ws://localhost:9334），接收命令并在用户的真实
 * 浏览器里执行，返回结果。所有操作都发生在用户已登录的标签页中，不使用任何
 * 无头浏览器或伪造指纹。
 *
 * 端口固定为 9334：抖音技能与小红书技能（9333）各自独立，避免两个扩展抢连接
 * 导致命令被路由到错误的站点。
 *
 * 命令分组：
 * - navigate / wait_for_load : chrome.tabs.update + onUpdated
 * - evaluate / has_element / get_* : chrome.scripting.executeScript (MAIN world)
 * - click / type / press_key : chrome.debugger + CDP Input，产生 isTrusted 事件
 * - set_file_input : chrome.debugger + CDP DOM.setFileInputFiles（本地绝对路径）
 * - screenshot_element / get_cookies
 */

const BRIDGE_URL = "ws://localhost:9334"
const DOUYIN_URLS = ["https://creator.douyin.com/*", "https://*.douyin.com/*"]

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
    console.log("[Douyin Bridge] 已连接到 bridge server")
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
    console.log("[Douyin Bridge] 连接断开，3s 后重连...")
    setStatus(false)
    setTimeout(connect, 3000)
  }

  ws.onerror = (e) => console.error("[Douyin Bridge] WS 错误", e)
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
    case "get_scroll_top":
    case "get_viewport_height":
      return await cmdEvaluateInMainWorld(method, params)

    default:
      return await cmdDomInMainWorld(method, params)
  }
}

// ───────────────────────── Tab 管理 ─────────────────────────

async function getOrOpenDouyinTab() {
  const tabs = await chrome.tabs.query({ url: DOUYIN_URLS })
  if (tabs.length > 0) {
    // 优先复用创作服务平台标签页，其次任意抖音标签页
    const creatorTab = tabs.find((t) => (t.url || "").includes("creator.douyin.com"))
    return creatorTab || tabs[0]
  }
  const tab = await chrome.tabs.create({ url: "https://creator.douyin.com/creator-micro/home" })
  await waitForTabComplete(tab.id, null, 30000)
  return tab
}

async function waitForTabComplete(tabId, expectedUrlPrefix, timeout) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeout

    function listener(id, info, updatedTab) {
      if (id !== tabId) return
      if (info.status !== "complete") return
      if (expectedUrlPrefix && !updatedTab.url?.startsWith(expectedUrlPrefix.slice(0, 20))) return
      chrome.tabs.onUpdated.removeListener(listener)
      resolve()
    }

    chrome.tabs.onUpdated.addListener(listener)

    const poll = async () => {
      if (Date.now() > deadline) {
        chrome.tabs.onUpdated.removeListener(listener)
        reject(new Error("页面加载超时"))
        return
      }
      const tab = await chrome.tabs.get(tabId).catch(() => null)
      if (tab && tab.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener)
        resolve()
        return
      }
      setTimeout(poll, 400)
    }
    setTimeout(poll, 600)
  })
}

// ───────────────────────── 导航 ─────────────────────────

async function cmdNavigate({ url }) {
  const tab = await getOrOpenDouyinTab()

  // 已在 douyin.com 时用页面内 location 跳转，保持 same-origin 导航特征，
  // 与真实用户点击站内链接的行为一致。
  const isOnDouyin = tab.url && tab.url.includes("douyin.com")
  if (isOnDouyin) {
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

  await waitForTabComplete(tab.id, url, 60000)

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
  const tab = await getOrOpenDouyinTab()
  await waitForTabComplete(tab.id, null, timeout)
  return null
}

async function cmdGetPageInfo() {
  const tab = await getOrOpenDouyinTab()
  return { url: tab.url || "", title: tab.title || "", tabId: tab.id }
}

// ───────────────────────── 截图 ─────────────────────────

async function cmdScreenshotElement({ selector, padding = 0 }) {
  const tab = await getOrOpenDouyinTab()
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

async function cmdGetCookies({ domain = "douyin.com" }) {
  return await chrome.cookies.getAll({ domain })
}

// ───────────────────────── MAIN world JS 执行 ─────────────────────────

async function cmdEvaluateInMainWorld(method, params) {
  const tab = await getOrOpenDouyinTab()
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: mainWorldExecutor,
    args: [method, params],
  })
  const r = results?.[0]?.result
  if (r && typeof r === "object" && "__dy_error" in r) throw new Error(r.__dy_error)
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
        return { __dy_error: `JS执行错误: ${e.message}` }
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
      return { __dy_error: `未知 MAIN world 方法: ${method}` }
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
  const tab = await getOrOpenDouyinTab()
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
        el.scrollIntoView({ block: "center", behavior: "instant" });
        const r = el.getBoundingClientRect();
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        // 优先点击该坐标下最内层的可点元素（处理按钮内嵌 span 的情况）
        const hit = document.elementFromPoint(cx, cy);
        const t = (hit && (hit === el || el.contains(hit))) ? hit : el;
        return { x: cx, y: cy, tag: t.tagName, disabled: !!t.disabled };
      })()`,
      returnByValue: true,
    })

    const pos = evalResult?.result?.value
    if (!pos) throw new Error(`元素不存在: ${JSON.stringify({ selector, index, text })}`)

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
 * 按下并释放按键。
 *
 * 统一走 CDP 真实键盘事件（isTrusted=true），**不**对 contenteditable 特判成
 * execCommand —— 抖音的富文本编辑器是自己管理行模型的（行节点是 .ace-line），
 * 实测 execCommand("insertParagraph") 对它完全无效（换行被吞掉）。
 * 真实 Enter 事件才能让抖音自己的 keydown handler 插入新行。
 */
async function cmdPressKeyViaDebugger({ key }) {
  const tab = await getOrOpenDouyinTab()
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
  const tab = await getOrOpenDouyinTab()

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
  const tab = await getOrOpenDouyinTab()
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
  const tab = await getOrOpenDouyinTab()
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: domExecutor,
    args: [method, params],
  })
  const r = results?.[0]?.result
  if (r && typeof r === "object" && "__dy_error" in r) throw new Error(r.__dy_error)
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
    if (!el) return { __dy_error: `元素不存在: ${selector}` }
    return el
  }

  switch (method) {
    case "input_text": {
      const el = requireEl(params.selector)
      if (el.__dy_error) return el
      el.focus()
      // React/Vue 受控组件需要走原生 setter，否则框架状态不会更新。
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
          resolve({ __dy_error: `元素不存在: ${params.selector}` })
          return
        }
        el.focus()
        document.execCommand("selectAll", false, null)
        document.execCommand("delete", false, null)
        await sleep(80)
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
      return { __dy_error: `未知 DOM 命令: ${method}` }
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms))
}

// ───────────────────────── 启动 ─────────────────────────

connect()
