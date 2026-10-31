"""NewsAgg 的确定性单元测试（标准库 unittest，无第三方依赖）。

放这个空包文件不是为了组织代码，是为了让 `python3 -m unittest discover` 在
**仓库根目录**也能发现测试：没有 `__init__.py` 时，从根跑 discover 会静默地
「发现 0 个测试」并打印 `OK`——那正是本项目反复记录的失败形状（`catch {}`、
`kept_new` 为空、失效源不呈现：**代码假装自己做完了**）。加一个文件换掉这个
假阳性，比记住必须写 `-s tests` 更可靠。

跑法（两种等价）：
    python3 -m unittest discover
    python3 -m unittest discover -s tests -v
"""
import os

# 测试不许打网络。
#
# 简报的第 ⑤ 段会**真的**跑一次 agent-reach 实测（`check`，每个渠道最多 30 秒）；
# 而 `test_brief_accidental` 要跑四遍简报——2026-10-31 实测：全量套件从 8 秒变成
# 274 秒，其中 267 秒全花在这一个模块上，原因是一句与测试目标无关的网络探测。
#
# 这个开关只影响「信源健康探测」（`source_health.read_health` 直接返回 skipped，
# 第 ⑤ 段不渲染它），不影响任何被测逻辑。想验真实探测时显式 `-e` 反设即可。
os.environ.setdefault("NEWSAGG_SKIP_SOURCE_HEALTH", "1")
