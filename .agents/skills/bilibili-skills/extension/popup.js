/**
 * Bilibili Bridge - Popup
 * 显示与本地 bridge server 的连接状态，并提供打开创作中心的快捷入口。
 */

const bridgeText = document.getElementById("bridge-text")
const bridgeDot = document.getElementById("bridge-dot")
const bridgeBadge = document.getElementById("bridge-status")
const hint = document.getElementById("hint")

function render(connected) {
  bridgeBadge.className = "badge " + (connected ? "ok" : "err")
  bridgeDot.className = "dot " + (connected ? "ok" : "err")
  bridgeText.textContent = connected ? "已连接" : "未连接"
  hint.textContent = connected
    ? "可以开始使用：在 Agent 中下达 B站投稿指令即可。"
    : "未连接到 ws://localhost:9335。请先运行 python scripts/bridge_server.py。"
}

async function refresh() {
  try {
    const resp = await chrome.runtime.sendMessage({ type: "GET_STATUS" })
    render(!!resp?.status?.wsConnected)
  } catch (_) {
    render(false)
  }
}

document.getElementById("open-btn").addEventListener("click", () => {
  chrome.tabs.create({ url: "https://member.bilibili.com/platform/home" })
})

refresh()
setInterval(refresh, 2000)
