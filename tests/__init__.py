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
