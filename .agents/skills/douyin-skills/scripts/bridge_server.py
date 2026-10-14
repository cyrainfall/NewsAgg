"""Douyin Extension Bridge Server

浏览器扩展连接到这里（WebSocket 长连接），CLI 通过同一端口发送命令
（role=cli，短连接），Bridge 把命令转发给扩展并把结果原路返回。

启动方式：
    python scripts/bridge_server.py

端口：9334（可通过 --port 覆盖）

9334 是本技能专用端口。xiaohongshu-skills 用的是 9333，两者分开可以让两个
浏览器扩展各自连接自己的 bridge，避免命令被路由到错误的站点。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

import websockets
from websockets.server import ServerConnection

logger = logging.getLogger("douyin-bridge")


class BridgeServer:
    def __init__(self) -> None:
        self._extension_ws: ServerConnection | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}

    async def handle(self, ws: ServerConnection) -> None:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
        except (TimeoutError, Exception) as e:
            logger.warning("握手超时或失败: %s", e)
            return

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        role = msg.get("role")
        if role == "extension":
            await self._handle_extension(ws)
        elif role == "cli":
            await self._handle_cli(ws, msg)
        else:
            logger.warning("未知 role: %s", role)

    # ─── Extension 端（长连接） ───────────────────────────────────────

    async def _handle_extension(self, ws: ServerConnection) -> None:
        logger.info("Extension 已连接")
        self._extension_ws = ws
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_id = msg.get("id")
                if msg_id and msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    if not future.done():
                        future.set_result(msg)
        finally:
            self._extension_ws = None
            logger.info("Extension 已断开")
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Extension 断开连接"))
            self._pending.clear()

    # ─── CLI 端（短连接，发一条命令，收一条回复） ─────────────────────

    async def _handle_cli(self, ws: ServerConnection, msg: dict) -> None:
        # 特殊命令：查询 server/extension 状态，无需转发给扩展
        if msg.get("method") == "ping_server":
            await ws.send(
                json.dumps({"result": {"extension_connected": self._extension_ws is not None}})
            )
            return

        if not self._extension_ws:
            await ws.send(
                json.dumps(
                    {"error": "Extension 未连接，请确认浏览器已安装并启用 Douyin Bridge 扩展"}
                )
            )
            return

        msg_id = str(__import__("uuid").uuid4())
        msg["id"] = msg_id

        loop = asyncio.get_event_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[msg_id] = future

        await self._extension_ws.send(json.dumps(msg))

        try:
            # 单条命令上限 300s：要覆盖「等待页面加载 180s」「等待视频转码」等长任务
            result = await asyncio.wait_for(future, timeout=300.0)
            await ws.send(json.dumps(result))
        except TimeoutError:
            self._pending.pop(msg_id, None)
            await ws.send(json.dumps({"error": "命令执行超时（300s）"}))
        except ConnectionError as e:
            await ws.send(json.dumps({"error": str(e)}))


async def main(port: int) -> None:
    server = BridgeServer()
    async with websockets.serve(server.handle, "localhost", port):
        logger.info("Bridge server 已启动: ws://localhost:%d", port)
        logger.info("等待浏览器扩展连接...")
        await asyncio.Future()  # 永久运行


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Douyin Extension Bridge Server")
    parser.add_argument("--port", type=int, default=9334, help="监听端口（默认 9334）")
    args = parser.parse_args()

    asyncio.run(main(args.port))
