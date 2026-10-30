#!/usr/bin/env python3
"""头条创作中心的纯 HTTP 客户端（不打开浏览器）。

## 这个模块存在的原因

发布链路原来只有一条路：让 Playwright 起一个 Chromium、点发布页的 DOM。这条路的
成本与风险都被记过账 —— 它要 ~40s 才起来、要占一份内存（ADR-0023 的预检就是为它
加的）、并发跑两次会撞 ProcessSingleton（ADR-0030）、而且「点了一下」不等于
「平台收到了」。去掉浏览器之后剩下的三件事本来就不需要 DOM：登录预检、反查发布
结果、拉草稿列表，都是普通 JSON over HTTPS。

## 边界：这里只做协议，不做决策

本模块**故意**不含熔断、每日上限、成稿校验、账本写入。那些是发布层（`publish.py`）
的门序，属于策略而非协议。把策略混进协议层会让它无法被单独测试，也会让「换个
后端」变成「换一套安全规则」——而安全规则不该因为传输方式改变而改变。

## 已验证 vs 未验证（重要，别混）

| 能力 | 状态 | 证据 |
|---|---|---|
| `login_status()` | **已实测** | `code=0` + `data.is_login=true`，浏览器关闭 |
| `list_articles()` | **已实测** | 15 条，与 node CLI 反查 10/10 逐条对齐（gid+title） |
| `list_drafts()` | **已实测** | `draft_list[]` 5 条 |
| `count_drafts()` | **已实测** | `{"count": 5}` |
| `article_detail()` | **已实测（2026-10-30）** | 拿回线上文章标题与 2472 字正文 HTML |
| `delete_article()` | **协议已验，未删过真东西** | 端点/参数/校验顺序全部实测，见 ADR-0087 |
| `upload_image()` | **未实测** | 会往账号图库写东西 |
| `save_draft()` | **未实测（默认拒绝执行）** | 实测恒返回 7050，见下 |
| `publish_article()` | **未实测（默认拒绝执行）** | 不可逆 |
| `edit_article()` | **未实测（默认拒绝执行）** | 走 publish 同一端点，同样 7050 |

## 7050 到底卡在哪（2026-10-30 补测，推翻了旧结论的一半）

旧文档把写路径的失败归给「签名闸未验证」。**这个推断已被推翻**：把 `save`
改成非法值 99 再打过去，服务端返回的是

    code=4029「标题长度应该在 2-30 字之间」

也就是说请求**已经穿过签名层、进了字段校验**，`save` 都被解析了。所以：

- **不是签名问题。** `/mp/agw/article/publish` 确实在 `bdms` 的
  `paths.include` 里（`/mp/agw/article/wtt`、`/mp/agw/draft/save_ugc_draft` 也在），
  但实测这条闸**没有拦下**不带签名的纯 HTTP 请求。
- **是表单字段问题。** 换成合法 `save`、合法标题、真实编辑器 HTML（直接从线上
  文章借来的 2472 字）、真实封面之后，仍然 `7050「保存失败」`。缺的东西比
  标题更靠后，且不是「有没有正文」这种粗粒度判断。

结论停在：**写路径（存草稿/发布/改写）不能用纯 HTTP，但原因不是签名，是表单
序列化还没对齐。** 要推进只能抓一次真实发布请求的完整 body（浏览器端拦截），
这是下一件事，不是现在能猜出来的。

`delete` 不受此影响：`/mp/agw/creator_center/delete` **不在**签名清单里，实测
返回的是业务错误（1002 无效题材类型 / 1009 无效 ID），说明请求被正常解析。

写操作被 `allow_unverified=True` 这道显式开关挡住，默认抛 `UnverifiedWrite`。
理由见下面 `publish_article` 的文档字符串。

## 关于签名（这里曾经是本任务最大的疑点）

发布页的 bundle 里注册了 `window.byted_acrawler.init({aid:1231, intercept:!0,
enablePathList:["/mp/agw/article/publish"]})`，并加载 `//sf1-cdn-tos.toutiaostatic.com/
obj/rc-web-sdk/acrawler.js`（71KB，vm 字节码，静态读不出算法）。看起来发布 POST
必须有签名头。

但**实测推翻了这个推断的一部分**：一个不含任何业务字段的 POST 打过去，返回的是
`code=7050「保存失败」`（业务层拒绝），**不是** `code=2222「可信浏览器校验」`。
也就是说这次请求穿过了签名闸，进到了业务逻辑。所以大概率的结论是：签名要么不是
必需的，要么只在上报/风控路径上。**但这只能靠一次真实写入来确认，而本次任务明确
禁止真实写入** —— 所以结论停在「不确定」，不往上写成「可以发」。

实测记录（2026-10-25，空 body，不含任何真实标题/正文，不可能发布成功）：

    POST /mp/agw/article/publish?source=mp&type=article&aid=1231&mp_publish_ab_val=0
    -> 200 {"code":7050,"data":{"pgc_id":"0"},"err_no":7050,
            "message":"保存失败","reason":"保存失败"}

## 字段来源

下面所有字段名与端点都不是猜的，逐个来自发布页真实 bundle：

- 端点与 query：`publish.b8c90341ac.js` 模块 22702（`doPublish`）
- 表单默认值全集：同 bundle 模块 12306（`x.y`），偏移 ~197354
- `save` 取值：同 bundle `timing` builder（`PUBLISH=1 / DRAFT=0 / PREVIEW=0`）
- 声明枚举：`7979.f3fa7c18e0.js` ~563743，`{none:0, refer:1, self:2, AI:3,
  from_net:4, self_idea:5, fictional_story:6, ...}`
- 封面图上传：同 bundle 模块 9850 `Be.getCover` → `/spice/image?upload_source=
  20020003&aid=1231&device_platform=web&need_cover_url=1`，FormData 字段 `imageUrl`

无法从静态 JS 确认的是**嵌套字段的序列化形式**（`coverList` 是 JSON 字符串还是
`coverList[0][uri]=...` 这种展开）。`Garr.network.post(obj)` 的实现做了
`k=v&encodeURIComponent`，对一个对象值只会得到 `[object Object]`，所以真实提交
一定在某个中间层先把嵌套结构 stringify 了——但那一层被压缩得看不出来。这正是
`save_draft`/`publish_article` 停在「未验证」的原因。

## 用法

    from toutiao_api import Client
    c = Client.from_browser()          # 读本机 Chrome 的 cookie，不需要浏览器在跑
    if c.login_status()["logged_in"]:
        for a in c.list_articles(limit=20):
            print(a["title"], a["gid"], a["status_desc"])

命令行（便于人工核对，输出 JSON）：

    # 只读
    python3 toutiao_api.py login
    python3 toutiao_api.py list --limit 20
    python3 toutiao_api.py drafts
    python3 toutiao_api.py draft-count
    python3 toutiao_api.py detail --gid <19位>            # 标题 / 正文长度 / 封面
    python3 toutiao_api.py detail --gid <19位> --out x.json --content

    # 写（默认拒绝，都要 --allow-unverified-write）
    python3 toutiao_api.py delete --gid <19位> --expect-title '平台上现在的标题'
    python3 toutiao_api.py edit   --gid <19位> --expect-title '...' --md outputs/x.md

`--expect-title` 不是可选装饰：删除与改写都不可逆，gid 抄错一位就是另一篇文章。
先读回平台当前标题、全等才动手，是这两条命令唯一防得住「手滑」的地方。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# ── 底座导入 ────────────────────────────────────────────────────────────
# .agents/lib 是仓库内的共享底座（四五个技能共用一份），不在 site-packages 里，
# 所以按仓库相对位置注入 sys.path。用 pathlib 而不是拼字符串，Windows 也能跑。
_REPO_ROOT = Path(__file__).resolve().parents[4]
_LIB_DIR = _REPO_ROOT / ".agents" / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from browserless.chromium import CookieError  # noqa: E402
from browserless.http import (  # noqa: E402
    ApiError,
    AuthRequired,
    HttpError,
    Session,
    build_query,
)

MP = "https://mp.toutiao.com"
REFERER = "https://mp.toutiao.com/"
PUBLISH_PAGE = "https://mp.toutiao.com/profile_v4/graphic/publish"

# 头条号后台的固定 app id。发布页所有 /mp/agw/ 请求都带它。
AID = 1231

LOGIN_STATUS_URL = f"{MP}/mp/agw/media/user_login_status_api"
ARTICLE_LIST_URL = f"{MP}/mp/agw/article/list"
DRAFT_LIST_URL = f"{MP}/mp/agw/creator_center/draft_list"
DRAFT_COUNT_URL = f"{MP}/mp/agw/creator_center/draft_count"
PUBLISH_URL = f"{MP}/mp/agw/article/publish"
# 发布页「修改」态取稿。`GET`，无副作用，来自 bundle 模块 22702 的 `lJ`。
ARTICLE_EDIT_URL = f"{MP}/mp/agw/article/edit"
# 内容管理的删除。`POST`，query 与 body 各一份参数，来自同一份 bundle 的 `Delete`。
ARTICLE_DELETE_URL = f"{MP}/mp/agw/creator_center/delete"
IMAGE_UPLOAD_URL = f"{MP}/spice/image"

# 声明 → `infoSource.source_type`。来自 7979 bundle 的枚举，不是我编的。
# 已用平台真实数据反证过一次：最新那篇文章的 `info_source` 是 `{"source_type":5}`，
# 而它发布时传的是 `--declaration 个人观点`，对应下面的 self_idea。
DECLARATIONS: dict[str, int] = {
    "无": 0,            # none
    "引用站内": 1,       # refer
    "自行拍摄": 2,       # self
    "引用AI": 3,         # AI
    "取材网络": 4,       # from_net
    "个人观点": 5,       # self_idea
    "虚构演绎": 6,       # fictional_story
}

# 反查时用的状态词。顺序有意义：命中第一个就返回，与 publish.py 原来的
# `("rejected", "reviewing", "published")` 保持一致（先判坏消息）。
_REJECT_HINTS = ("未通过", "审核不通过", "已删除", "仅自己可见")
_REVIEW_HINTS = ("审核中", "机器审核", "复审")


class ToutiaoError(RuntimeError):
    """本模块所有错误的基类。"""


class LoginRequired(ToutiaoError):
    """平台明确表示未登录。处置方式是「去浏览器扫码」，不是重试。"""


class UnverifiedWrite(ToutiaoError):
    """写操作尚未验证，默认拒绝执行。

    单独一个类型，因为它不该被当成「失败重试」。它是一个**代码层的安全闸**：
    在有人用真实文章确认过参数序列化之前，这个函数宁可抛异常也不发请求。
    """


def _norm_gid(gid: Any) -> str:
    """gid 必须是 19 位数字字符串。

    这里不做「宽松解析」。`7700590575333949967` 一旦被当成 float 就是
    `7700590575333950000`，末尾几位悄悄变了 —— 那会指向**另一篇文章**，而
    `delete` 和 `edit` 都会照着这个错号动手。宁可在这里就炸。
    """
    s = str(gid or "").strip()
    if not s.isdigit() or len(s) < 10:
        raise ToutiaoError(f"gid 看着不对：{gid!r}（应为 19 位数字字符串，不是数字类型）")
    return s


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _unwrap_detail(payload: Any, gid: str) -> dict:
    """`/mp/agw/article/edit` 的响应形状与其它端点**不一样**。

    列表、登录这些接口都是 `{"code":0,"data":{...}}`，但这个端点直接把文章对象
    平铺在顶层（`title` / `content` / `media` / `pgc_feed_covers` …），没有外壳。
    实测 2026-10-30。所以这里不能照抄别的端点的解析方式，两种形状都得认：

    - 有 `data` 且 `data.title` 存在 → 用 `data`
    - 否则把顶层当文章对象

    只认一种形状的代价是「静默拿到一个空 dict」，然后 `edit` 会以为原标题是空串，
    接着被自己的标题闸拒掉 —— 看起来像闸太严，其实是解析错了。
    """
    if not isinstance(payload, dict) or not payload:
        raise ToutiaoError(f"文章 {gid} 的详情是空的：{payload!r}")
    data = payload.get("data")
    if isinstance(data, dict) and ("title" in data or "content" in data):
        return data
    if "title" in payload or "content" in payload:
        return payload
    # 错误信封：`{"code":..., "message":..., "err_no":...}`。把平台的原文带出去，
    # 不然调用方只会看到「形状不认识」，而真正的原因是「文章正在审核中」这类状态。
    if "code" in payload:
        raise ToutiaoError(
            f"文章 {gid} 取不到详情：code={payload.get('code')} "
            f"{payload.get('message') or payload.get('reason') or ''}".strip()
        )
    raise ToutiaoError(
        f"文章 {gid} 的详情形状不认识：顶层有 {list(payload)[:8]}，既没有 data 也没有 title"
    )


def _cover_from_detail(detail: dict) -> tuple[str, str]:
    """从详情里取出**原封面**，避免改稿时重复上传一张图。

    上传是不可逆的（账号图库里会永久多一张，见 ADR-0032 对封面那条的处理），
    所以「不改封面」必须真的不发上传请求。

    实测形状（2026-10-30）：

        pgc_feed_covers: [{origin_uri: "tos-cn-i-...", url: "https://p11-sign...",
                           thumb_url: "...", thumb_width, thumb_height}]

    `origin_uri` 是图库里的稳定标识，`url` 是带签名的展示地址。表单两个都要，
    取不到就返回空，由调用方决定是报错还是要求显式传封面。
    """
    for key in ("pgc_feed_covers", "cover_list", "covers"):
        covers = detail.get(key)
        if isinstance(covers, list) and covers and isinstance(covers[0], dict):
            c = covers[0]
            uri = str(c.get("origin_uri") or c.get("uri") or c.get("Uri")
                      or c.get("image_uri") or "")
            url = str(c.get("url") or c.get("Url") or c.get("image_url") or "")
            if uri or url:
                return uri, url
    cache = detail.get("content_cache")
    if isinstance(cache, dict):
        raw = cache.get("cover_infos")
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except ValueError:
                return "", ""
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                c = parsed[0]
                return (
                    str(c.get("Uri") or c.get("uri") or ""),
                    str(c.get("Url") or c.get("url") or ""),
                )
    return "", ""


# ── 客户端 ──────────────────────────────────────────────────────────────


class Client:
    """头条创作中心客户端。所有方法要么返回解析好的 dict/list，要么抛异常。

    不缓存、不落盘、不打印任何 cookie 值 —— cookie 只在 `Session` 内部用于
    拼 `Cookie:` 头，一旦有人往日志里写就是在泄漏会话凭证。
    """

    def __init__(self, session: Session):
        self.session = session

    # ── 构造 ────────────────────────────────────────────────────────

    @classmethod
    def from_browser(cls, browser: str = "chrome") -> "Client":
        """从本机浏览器的 cookie 库读会话。**不需要浏览器在运行。**

        `CookieError` 表示技术上读不到（没装、被加密、没有该域 cookie），
        `LoginRequired` 表示读到了但平台说不认识 —— 两者要分开告诉用户。
        """
        try:
            session = Session.from_browser(
                browser,
                ["toutiao.com"],
                referer=REFERER,
                origin=MP,
            )
        except CookieError as exc:
            raise ToutiaoError(f"读不到 {browser} 的 toutiao.com cookie：{exc}") from exc
        return cls(session)

    @classmethod
    def from_cookie_file(cls, path: str | Path) -> "Client":
        """从**注入的 cookie 文件**建会话。服务器上唯一可用的入口。

        ## 为什么需要这个入口

        `from_browser()` 依赖本机 Chrome 的 cookie 库。服务器上没装 Chrome
        （实测 `HOME=/tmp/fakehome-empty` 下 `chromium.load("chrome", ...)`
        直接抛 `CookieError: 找不到浏览器 chrome 的 profile`），所以服务器上
        走不了这条路。

        而 Playwright 自己的 profile（`.home/.toutiao-ops/accounts/default/
        browser-data`）**解不开**——实测它的 `Local State` 里 `os_crypt` 是
        空 dict，48 条 toutiao cookie 的 `encrypted_value` 用任何口令都解出
        非 ASCII 垃圾。browserless 与 toutiao-ops 是两套独立的登录态。

        → 服务器上必须**注入**。本机导出 → 服务器加载。

        ## 文件格式

        JSON：`{"cookies": [{"domain","name","value","path","secure","http_only"}]}`
        （`Cookie.to_dict()` 的形状）。生成方法见 `scripts/export-cookies.py`。

        ## 权限

        文件里是**账号凭证**。`load_cookie_file` 会检查权限位：组/其他可读
        就拒绝加载。理由是这条链路的产物会被人 `scp` 上服务器，
        而一个 0644 的 cookie 文件等于把账号交出去。
        """
        session = Session.from_cookie_file(path, referer=REFERER, origin=MP)
        return cls(session)
    # ── 内部请求 ────────────────────────────────────────────────────

    def _get_json(self, url: str, *, what: str) -> dict:
        """GET 一个 JSON 端点，并把三类失败翻译成本模块的异常。"""
        try:
            payload = self.session.get_json(url)
        except AuthRequired as exc:
            raise LoginRequired(f"{what} 被拒绝：未登录") from exc
        except ApiError as exc:
            # 业务层返回 4xx 时也可能是「未登录」，但更多是接口变了。
            # 不猜，把原文带出去。
            raise ToutiaoError(f"{what} 失败：{exc}") from exc
        except HttpError as exc:
            raise ToutiaoError(f"{what} 网络失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise ToutiaoError(f"{what} 返回的不是 JSON 对象：{type(payload).__name__}")
        return payload

    @staticmethod
    def _check_code(payload: dict, *, what: str) -> dict:
        code = payload.get("code")
        if code == 0:
            return payload
        # 2222 = 可信浏览器校验（风控），3022 = 账号未完成注册。
        # 这两个值来自发布页 JS 的真实分支判断，不是猜的。
        hint = ""
        if code == 2222:
            hint = "（平台要求可信浏览器校验，需回到浏览器完成人机验证）"
        elif code == 3022:
            hint = "（账号未完成注册，去「个人中心 → 我的权益」认证）"
        raise ToutiaoError(
            f"{what} 返回 code={code}{hint}：{payload.get('message') or payload.get('reason') or ''}"
        )

    # ── 1. 登录预检 ─────────────────────────────────────────────────

    def login_status(self) -> dict:
        """登录态。这是发布链路「门 2」的无浏览器替代。

        原来门 2 跑 `node index.js auth check`，那会起一个 Playwright 浏览器、
        goto mp.toutiao.com、等 URL 跳转 —— 为了回答一个布尔问题花掉 30~60 秒，
        而且在内存紧张时正是它最容易崩的时候（ADR-0023 记录过）。

        返回：
            {"logged_in": bool, "raw_code": int, "checks": {...}}
        未登录时**不抛异常**，返回 `logged_in: False` —— 因为「没登录」是门 2 的
        正常判定结果（要熔断、要提示去扫码），不是程序错误。

        ## `media` / `user` 不是身份信息（实测）

        一开始以为 `data.media` 里有账号昵称、`data.user` 里有 user_id，实际拉下来
        `media` 只有 6 个布尔：`avatar_info_ok / can_renew_account / is_media /
        media_info_ok / media_status_ok / verified_info_ok`；`user` 只有 3 个布尔：
        `is_virtual_mobile / mobile_bind_ok / user_status_ok`。**没有任何身份字段。**

        所以这个方法不假装能报出「当前是哪个号」—— 那需要另一个端点，而门 2 只需要
        一个布尔。名字留给 `--account` 参数（CLI 侧的 profile 名），不是平台返回的。
        """
        payload = self._get_json(LOGIN_STATUS_URL, what="登录态查询")
        data = payload.get("data") or {}
        media = data.get("media") or {}
        user = data.get("user") or {}
        checks = {
            # 这几个布尔比单独的 is_login 更能说明问题：登录了但 media_status_ok
            # 为假，说明号被限制了，发布一样会失败。
            "is_media": bool(media.get("is_media")),
            "media_status_ok": bool(media.get("media_status_ok")),
            "user_status_ok": bool(user.get("user_status_ok")),
            "mobile_bind_ok": bool(user.get("mobile_bind_ok")),
        }
        return {
            "logged_in": bool(data.get("is_login")) and payload.get("code") == 0,
            "raw_code": payload.get("code"),
            "checks": checks,
        }

    # ── 2. 文章列表（反查）─────────────────────────────────────────

    def list_articles(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """作品列表，归一化成一层扁平 dict。

        ## 为什么用 `limit/offset` 而不是发布页选稿弹窗那套 `size/page`

        同一个 `/mp/agw/article/list` 有两副面孔，取决于参数：

        - `?status=all&limit=20&offset=0` → `data.articles[]`，**含草稿**
          （`is_draft`），字段最全（`is_passed`/`is_unpass`/`pgc_status`）
        - `?search_word=&size=10&page=0&status=all&...` → `data.content[]`，
          是「引用站内文章」选择器的数据源，**不含草稿**，字段少一截

        反查要的是「这篇到底发出去没有」，所以用前者：草稿可见 + 审核态可见。

        ## `status` 参数是假的

        实测 `status=1` / `status=2` / `status=all` 返回的 15 条完全一样 —— 服务端
        忽略它。所以这里不做服务端过滤，一律拉回来在本地按 `is_draft` 判。

        ## gid 必须是字符串

        `group_id`/`pgc_id`/`id`/`item_id` 是同一串 19 位数字的字符串。node CLI
        那一侧同一个响应里还有 `groupID`（JS number），实测已丢精度
        （7700590575333949967 → 7700590575333950000）。账本里的 gid 要拿去和
        循环 C 的表现数据 join，丢一位就永远对不上（ADR-0053），所以这里只认
        字符串字段，并且在归一化时强制 `str()`。
        """
        url = f"{ARTICLE_LIST_URL}?" + build_query(
            {"status": "all", "limit": limit, "offset": offset}
        )
        payload = self._get_json(url, what="作品列表")
        data = self._check_code(payload, what="作品列表").get("data") or {}
        raw = data.get("articles")
        if not isinstance(raw, list):
            raise ToutiaoError(
                f"作品列表结构变了：data.articles 期望 list，实得 {type(raw).__name__}"
            )
        return [self._normalize_article(item) for item in raw if isinstance(item, dict)]

    @staticmethod
    def _normalize_article(item: dict) -> dict:
        gid = str(item.get("group_id") or item.get("pgc_id") or item.get("id") or "")
        return {
            "title": str(item.get("title") or "").strip(),
            "gid": gid,
            "url": str(item.get("article_url") or ""),
            "create_time": item.get("create_time"),
            "status": item.get("status"),
            "status_desc": str(item.get("status_desc") or ""),
            "is_draft": bool(item.get("is_draft")),
            "is_passed": bool(item.get("is_passed")),
            "is_unpass": bool(item.get("is_unpass")),
            "pgc_status": item.get("pgc_status"),
            "word_count": item.get("content_word_cnt"),
            "abstract": str(item.get("abstract") or ""),
        }

    def find_articles_by_title(self, title: str, limit: int = 20) -> list[dict]:
        """按标题精确匹配（strip 后全等），返回所有命中。

        为什么不用搜索接口：`search_word` 是分词检索，会带出「相似」的文章，
        而反查需要的是「同一标题出现几次」（ADR-0047 的微头条泄漏判据依赖计数）。
        多拉 20 条本地比一下，比信任一个模糊搜索便宜。
        """
        want = title.strip()
        if not want:
            return []
        return [a for a in self.list_articles(limit=limit) if a["title"] == want]

    # ── 3. 草稿列表 ─────────────────────────────────────────────────

    def list_drafts(self, limit: int = 20) -> list[dict]:
        """草稿箱。`draft_type == 2` 是图文（枚举来自 index bundle 模块 34957）。"""
        payload = self._get_json(DRAFT_LIST_URL, what="草稿列表")
        # 这个端点成功时不返回 code=0 之外的东西，_check_code 顺手兼容了
        # 只给 `draft_list` 的旧形态。
        if payload.get("code") not in (0, None):
            self._check_code(payload, what="草稿列表")
        raw = payload.get("draft_list")
        if not isinstance(raw, list):
            raise ToutiaoError(
                f"草稿列表结构变了：draft_list 期望 list，实得 {type(raw).__name__}"
            )
        out = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            cover = item.get("cover_image") or {}
            out.append({
                "gid": str(item.get("gid") or ""),
                "draft_type": item.get("draft_type"),
                "is_graphic": item.get("draft_type") == 2,
                "abstract": str(item.get("abstract") or ""),
                "cover_uri": cover.get("image_uri") or "",
                "cover_url": cover.get("image_url") or "",
            })
        return out[:limit] if limit else out

    def count_drafts(self) -> int:
        payload = self._get_json(DRAFT_COUNT_URL, what="草稿计数")
        self._check_code(payload, what="草稿计数")
        return int(payload.get("count") or 0)

    # ── 4. 图片上传 ─────────────────────────────────────────────────

    def upload_image(self, path: str | Path, *, allow_unverified: bool = False) -> dict:
        """上传一张图到头条图库，返回 `{uri, cover_url}`。

        ## 端点与字段（来自发布页 bundle 模块 9850 `Be.getCover`）

            POST /spice/image?upload_source=20020003&aid=1231
                            &device_platform=web&need_cover_url=1
            表单字段 imageUrl（`new FormData().append("imageUrl", <文件>)`）

        成功判据：`code == 0` 且 `data.cover_url` 存在；`data.origin_image_uri` 是
        后续填进 `coverList` 的 uri。gif 会被拒（JS 里有显式分支）。

        ## 底座做不了 multipart

        `browserless.http.Session.request` 对 dict 是 urlencode、对 bytes 是原样
        发送。所以这里手工拼 multipart body，再以 `data=<bytes>` 传进去并显式给
        `Content-Type: multipart/form-data; boundary=...`。不引入 requests
        （技能不能给共用底座加依赖）。

        ## 默认拒绝

        上传会往账号图库里写东西（虽然可删），本次任务的红线是「不产生任何线上
        内容」，所以默认 `allow_unverified=False` 直接抛 `UnverifiedWrite`。
        这个函数本身没有实测过 —— 诚实标注，不假装跑通了。
        """
        if not allow_unverified:
            raise UnverifiedWrite(
                "upload_image() 尚未用真实请求验证过（会往账号图库写入），"
                "默认拒绝。确认要执行请传 allow_unverified=True。"
            )
        p = Path(path)
        if not p.is_file():
            raise ToutiaoError(f"图片不存在：{p}")
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if mime == "image/gif":
            raise ToutiaoError("头条封面不收 gif（发布页 JS 有显式拒绝分支）")

        boundary = "----NewsAggBoundary" + uuid.uuid4().hex
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="imageUrl"; filename="{p.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        body = head + p.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
        url = f"{IMAGE_UPLOAD_URL}?" + build_query({
            "upload_source": 20020003,
            "aid": AID,
            "device_platform": "web",
            "need_cover_url": 1,
        })
        try:
            payload = self.session.post_json(
                url,
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                timeout=120,
            )
        except (ApiError, HttpError) as exc:
            raise ToutiaoError(f"图片上传失败：{exc}") from exc
        payload = self._check_code(payload, what="图片上传")
        data = payload.get("data") or {}
        cover_url = data.get("cover_url") or ""
        if not cover_url:
            raise ToutiaoError(f"图片上传返回 code=0 但没有 cover_url：{payload}")
        return {
            "uri": data.get("origin_image_uri") or "",
            "cover_url": cover_url,
            "mime": data.get("image_mime_type") or mime,
        }

    # ── 5/6. 发布 / 存草稿 ──────────────────────────────────────────

    @staticmethod
    def build_publish_form(
        *,
        title: str,
        content_html: str,
        cover_uri: str = "",
        cover_url: str = "",
        declaration: str = "个人观点",
        cover_width: int = 0,
        cover_height: int = 0,
        pgc_id: str = "",
        article_type: int = 0,
        content_source: int = 100000000402,
    ) -> dict:
        """按发布页 bundle 的字段名拼表单（**只拼，不发**）。

        ## 这个函数的状态：写路径未经验证，且实测字段名与真实请求不符

        2026-10-27 做过一次只读抓包调查（真实 publish body 在 route 层
        abort，零写入到达平台）。结论：下面这个函数**不能改成能用的状态**，
        理由有三条，任何一条都足以否掉纯 HTTP 写路径：

        1. **字段名大部分是错的。** 真实 publish body（save=1）解码后 31 个
           字段，这里 35 个键只有 9 个名字对得上（`source / title / content /
           save / entrance / timer_status / timer_time / praise /
           disable_praise`）。真实请求里有而这里没有的最要紧的是 `extra`
           （装 content_source / content_word_cnt / gd_ext / info_source /
           is_multi_title / sub_titles / manual_selected_city），还有
           `title_id` / `draft_form_data` / `pgc_feed_covers` /
           `search_creation_info` / `mp_editor_stat`。
        2. **平台在写路径上有闸。** 真实请求带 `_signature` / `msToken` /
           `a_bogus` 与 `x-secsdk-csrf-token`，由 `byted_acrawler` 对 publish
           端点单独加固（`enablePathList:["/mp/agw/article/publish"]`）——
           读路径没有这层。本模块对这几个参数一个都不处理。
        3. **正文不是平台原生形态。** 平台要的是 ProseMirror 序列化后的 HTML
           （段落带 `data-track`、H1 带 `class="pgc-h-forward-slash"`）；
           这里的 `content_html` 来自 `publish.py:_markdown_to_html`，
           没有 `data-track`、标题层级用 h2/h3。

        曾经这里写着「字段名可信，嵌套序列化不可信」——**那句话是错的**，
        实测字段名也不可信。

        写清楚「错在哪」而不是把字段改对，是有意的：改对也还是不能开。
        没有一次可撤回的验证之前，把写路径改成「看起来对」比保持「明确
        写着没验证过」更危险——后者每次都会撞 `UnverifiedWrite`，前者会
        开始在风控阈值上试运气。完整证据见
        `docs/adr/0073-pure-http-write-path-is-not-viable.md`。

        字段全集的历史来源：`publish.b8c90341ac.js` 模块 12306 的默认值对象。
        那个对象是**客户端表单状态默认值**，不是请求字段表——发布请求是
        若干 builder（timing / common / title format / editor）拼出来的，
        照抄状态默认值等于把「界面上的当前值」当成「请求里要发的东西」。

        曾经把 `coverList` / `slaveItems` 当成真实字段名。真身分别是
        `pgc_feed_covers`（JSON 字符串，元素键集与下面的 `cover_list`
        元素完全一致——那是这个函数唯一抄对的部分）与 `draft_form_data`；
        `slaveItems` 则是**服务端返回字段**，从来不是请求字段。
        `position` / `infoSource` / `subCoverList` 的真身分别在
        `extra.manual_selected_city` / `extra.info_source` /
        `extra.is_multi_title` + `extra.sub_titles`。
        """
        cover_list = []
        if cover_uri or cover_url:
            cover_list.append({
                "extra": {
                    "from_content": "0",
                    "from_content_idx": "0",
                    "from_content_uri": "",
                },
                # 实测：真实文章的 pgc_feed_covers 里 origin_uri 与 thumb_url 都是
                # `tos-cn-i-6w9my0ksvp/<hash>` 这种 uri 形态，url 才是签名 CDN 链接。
                "origin_uri": cover_uri,
                "thumb_url": cover_uri,
                "url": cover_url,
                "thumb_width": cover_width,
                "thumb_height": cover_height,
            })
        source_type = DECLARATIONS.get(declaration)
        if source_type is None:
            raise ToutiaoError(
                f"未知声明「{declaration}」，可选：{'、'.join(DECLARATIONS)}"
            )
        return {
            # ── timing builder ──
            "save": 0,
            "entrance": "",
            "timer_status": 0,
            "timer_time": "",
            # ── common builder ──
            "article_type": article_type,
            "pgc_id": pgc_id or "0",
            "source": 29,
            # ── title format ──
            "title": title,
            "titleId": "",
            # ── editor ──
            "content": content_html,
            "textCount": 0,
            # ── 12306 默认值里与发布结果相关的那些 ──
            "externalLinkChecked": False,
            "externalLink": "",
            "claimOrigin": 0,
            "copyRightChecked": 1,
            "addThirdTitle": 0,
            "hasSubTitle": 0,
            "subTitle": "",
            "subCoverList": json.dumps([], ensure_ascii=False),
            "subCoverType": "SINGLE",
            "praise": 0,
            "disablePraise": 1,
            "errPattern": "",
            "position": json.dumps({}, ensure_ascii=False),
            "coverList": json.dumps(cover_list, ensure_ascii=False),
            "coverType": "SINGLE" if cover_list else "NONE",
            "articleAdType": "NONE",
            "isFansArticle": 0,
            "visibility_level": "",
            "timingStatus": 0,
            "timingTime": "",
            "slaveItems": json.dumps(
                [{"title": "", "itemId": "", "type": 1, "source": 1, "covers": []}],
                ensure_ascii=False,
            ),
            "isMultiTitle": 0,
            "isStoryArticle": 0,
            "infoSource": json.dumps({"source_type": source_type}, ensure_ascii=False),
        }

    def _post_publish(self, form: dict, *, save: int, what: str) -> dict:
        form = dict(form)
        form["save"] = save
        if save == 0:
            form["entrance"] = ""
        else:
            form["entrance"] = "main"
        url = f"{PUBLISH_URL}?" + build_query({
            "source": "mp",
            "type": "article",
            "aid": AID,
            "mp_publish_ab_val": 0,
        })
        try:
            payload = self.session.post_json(url, data=form, timeout=120)
        except (ApiError, HttpError) as exc:
            raise ToutiaoError(f"{what}失败：{exc}") from exc
        return self._check_code(payload, what=what)

    def save_draft(self, *, allow_unverified: bool = False, **form_kwargs: Any) -> dict:
        """存草稿（`save=0`）。**默认拒绝执行。**

        `handlePublish` 的 PREVIEW/DRAFT 分支比 publish 安全得多：失败最坏也就是
        在草稿箱里多一条，用户能删。但它走的**是同一个** `/mp/agw/article/publish`
        端点（区别只在 `save`），所以「字段名对但序列化错」的后果一样是 7050 或
        一条形态诡异的草稿。

        返回 `{"pgc_id": str, "raw": dict}`。
        """
        if not allow_unverified:
            raise UnverifiedWrite(
                "save_draft() 尚未用真实请求验证过（会创建线上草稿），默认拒绝。"
                "确认要执行请传 allow_unverified=True。"
            )
        form = self.build_publish_form(**form_kwargs)
        payload = self._post_publish(form, save=0, what="存草稿")
        data = payload.get("data") or {}
        return {"pgc_id": str(data.get("pgc_id") or data.get("pgcId") or ""), "raw": payload}

    def publish_article(self, *, allow_unverified: bool = False, **form_kwargs: Any) -> dict:
        """真发（`save=1`）。**默认拒绝执行，且本任务从未执行过。**

        区分两件事：

        1. **字段名**：可信。逐个来自 bundle。
        2. **能不能发出去**：不可信。空 body 探针拿到 7050 而不是 2222，说明请求
           穿过了签名闸；但「穿过闸」不等于「带上真实内容也能过」—— 反爬通常按
           内容/频率分层，一次空请求不足以证明。

        这是不可逆动作（发出去了删不掉，且平台禁止重复内容）。所以：默认抛
        `UnverifiedWrite`；调用方必须显式传 `allow_unverified=True`，并且
        `publish.py` 还要求命令行再给一个 `--allow-unverified-write`。
        两道闸是故意的 —— 一个人为疏忽不该能把一篇没核对过的稿子发出去。
        """
        if not allow_unverified:
            raise UnverifiedWrite(
                "publish_article() 会真实发布（不可逆），字段序列化未经线上验证，"
                "默认拒绝。真发请用 --backend browser。"
            )
        form = self.build_publish_form(**form_kwargs)
        payload = self._post_publish(form, save=1, what="发布")
        data = payload.get("data") or {}
        pgc_id = str(data.get("pgc_id") or data.get("pgcId") or "")
        if not pgc_id or pgc_id == "0":
            raise ToutiaoError(f"发布返回 code=0 但没有有效 pgc_id：{payload}")
        return {"pgc_id": pgc_id, "raw": payload}

    # ── 详情 / 修改 / 删除 ──────────────────────────────────────────

    def article_detail(self, gid: str) -> dict:
        """一篇文章的投稿形态：`title` / `content`（HTML）/ 封面 / 状态。**只读**。

        端点来自发布页 bundle 模块 22702：

            U2("/mp/agw/article/edit", {pgc_id: e, wxstyle: 0, format: "json"})

        `GET`，没有任何写副作用 —— 浏览器里点「修改」时它就是这么取稿的。所以
        `detail` 可以进任何校验环节，不需要 `allow_unverified`。

        ## 为什么不用 `/mp/agw/article/list`

        列表只回摘要，不回正文。改稿要先有正文：一是为了拍快照（改坏了能对回
        原文），二是为了在写之前确认「我改的还是我以为的那一篇」。
        """
        gid = _norm_gid(gid)
        url = f"{ARTICLE_EDIT_URL}?" + build_query(
            {"pgc_id": gid, "wxstyle": 0, "format": "json"}
        )
        try:
            payload = self.session.get_json(url, timeout=60)
        except (ApiError, HttpError) as exc:
            raise ToutiaoError(f"读取文章 {gid} 失败：{exc}") from exc
        return _unwrap_detail(payload, gid)

    def delete_article(
        self,
        gid: str,
        *,
        expect_title: str,
        allow_unverified: bool = False,
        item_type: int = 0,
    ) -> dict:
        """删一篇作品。**默认拒绝执行，且必须先过标题闸。**

        ## 为什么必须给 `expect_title`

        gid 是 19 位数字，人抄错一位就是**另一篇文章**。删除不可逆、无回收站，
        所以这里不接受「只给 gid 的删除」：先读回平台当前标题，与 `expect_title`
        全等才动手。这不是多余的一步，它是这条命令唯一防得住的东西 —— 一旦传错
        gid，后面的所有校验都白做。

        ## 端点

            POST /mp/agw/creator_center/delete?type=&gid=&slave_id=&mp_id=...
            body: 同一个对象（bundle 里 `Xl()` 拼 query、`JSON.stringify(e)` 拼 body）

        `type` 在页面的菜单模型里，列表接口不返回它；实测图文用 0 可删，见 ADR-0087
        的验证记录。草稿走同一个端点（`is_draft` 为真时同样命中），所以不另开命令。

        ## 返回

        `{"gid", "title", "deleted": bool, "verified": bool}` —— `verified` 是
        **反查**结果，不是接口返回值。接口说成功不算数（ADR-0024）。
        """
        if not allow_unverified:
            raise UnverifiedWrite(
                "delete_article() 是不可逆操作，且写路径未经线上验证，默认拒绝。"
                "确认要删请传 allow_unverified=True，并在命令行加 --allow-unverified-write。"
            )
        gid = _norm_gid(gid)
        detail = self.article_detail(gid)
        live_title = str(detail.get("title") or "").strip()
        want = str(expect_title or "").strip()
        if live_title != want:
            raise ToutiaoError(
                f"标题闸没过，拒绝删除。\n"
                f"  平台上的 {gid} 是：《{live_title}》\n"
                f"  你要删的是：《{want}》\n"
                f"要么 gid 抄错了，要么你看的不是这一篇。改对了再来。"
            )

        url = f"{ARTICLE_DELETE_URL}?" + build_query(
            {"type": item_type, "gid": gid, "aid": AID}
        )
        try:
            payload = self.session.post_json(
                url, json_body={"type": item_type, "gid": gid}, timeout=60
            )
        except (ApiError, HttpError) as exc:
            raise ToutiaoError(f"删除 {gid} 失败：{exc}") from exc
        self._check_code(payload, what=f"删除 {gid}")

        # 反查：接口说成功不算数。列表里还找得到同名文章就是没删掉。
        still = [a for a in self.find_articles_by_title(live_title, limit=40)
                 if a["gid"] == gid]
        return {
            "gid": gid,
            "title": live_title,
            "deleted": not still,
            "verified": not still,
            "raw": payload,
        }

    def edit_article(
        self,
        gid: str,
        *,
        title: str,
        content_html: str,
        expect_title: str,
        cover_uri: str = "",
        cover_url: str = "",
        declaration: str = "个人观点",
        allow_unverified: bool = False,
    ) -> dict:
        """改一篇已发布/草稿的标题与正文。**默认拒绝执行，且必须先过标题闸。**

        ## 它和 publish 是同一个端点

        发布页在编辑态走的是同一个 `/mp/agw/article/publish`，区别只在表单里带了
        `pgc_id`。所以这里复用 `build_publish_form(pgc_id=...)`，不另造一套字段 ——
        两套字段就是两个会各自漂移的真相。

        ## 两条前置检查，都不是形式

        1. **标题闸**：平台当前标题必须等于 `expect_title`。改稿比删稿更阴险——
           删错了你知道没了，改错了它会安静地替换成一版你没读过的稿子。
        2. **乐观并发**：检查与写入之间平台可能已被别处改过（本人另一台设备、
           审核回滚）。标题闸在写之前重读一次，就是为了把「我以为的」和「现在
           真实的」对齐。

        ## 封面

        不传 `cover_uri` 时**沿用原封面**，不重新上传：上传会在账号图片库里新增
        一张图且删不掉（ADR-0032 的同一个理由）。要换封面才传。

        ## 返回

        `{"gid", "title", "content_sha256", "verified"}`。`verified` 是写完再读回
        平台、比对标题与正文哈希的结果；不一致不抛异常，但会明确写成 `False`。
        """
        if not allow_unverified:
            raise UnverifiedWrite(
                "edit_article() 会覆盖线上正文（不可逆），且写路径未经线上验证，默认拒绝。"
                "确认要改请传 allow_unverified=True，并在命令行加 --allow-unverified-write。"
            )
        gid = _norm_gid(gid)
        detail = self.article_detail(gid)
        live_title = str(detail.get("title") or "").strip()
        if live_title != str(expect_title or "").strip():
            raise ToutiaoError(
                f"标题闸没过，拒绝改写。\n"
                f"  平台上的 {gid} 是：《{live_title}》\n"
                f"  你按着改的是：《{expect_title}》\n"
            )

        if not (cover_uri or cover_url):
            cover_uri, cover_url = _cover_from_detail(detail)

        # 平台对已发布文章有修改次数上限（实测字段 `remain_modify_count`，当天剩 5）。
        # 每次改写都会消耗一次，所以先看还剩几次 —— 撞到 0 再发现就白改一遍。
        remain = detail.get("remain_modify_count")
        if isinstance(remain, int) and remain <= 0:
            raise ToutiaoError(
                f"这篇文章的修改次数已经用完了（remain_modify_count={remain}）。"
                f"平台不给改，任何改动都不会生效。"
            )

        form = self.build_publish_form(
            title=title,
            content_html=content_html,
            cover_uri=cover_uri,
            cover_url=cover_url,
            declaration=declaration,
            pgc_id=gid,
        )
        # save=1：改已发布文章要让它保持「已发布」；草稿由平台按原状态处理。
        try:
            payload = self._post_publish(form, save=1, what=f"改写 {gid}")
        except ToutiaoError as exc:
            if "7050" in str(exc):
                raise ToutiaoError(
                    f"改写 {gid} 被平台拒了：{exc}\n"
                    f"这不是签名问题（实测 save=99 会返回 4029 标题校验，说明请求已被解析），"
                    f"是 publish 表单还缺字段。在补齐之前，改稿请走浏览器后端，"
                    f"或者用平台后台手动编辑。详见 ADR-0087。"
                ) from exc
            raise
        self._check_code(payload, what=f"改写 {gid}")

        after = self.article_detail(gid)
        after_title = str(after.get("title") or "").strip()
        after_html = str(after.get("content") or "")
        verified = after_title == title.strip() and (
            _sha256(after_html) == _sha256(content_html)
        )
        return {
            "gid": gid,
            "title": after_title,
            "content_sha256": _sha256(after_html),
            "verified": verified,
            "raw": payload,
        }


# ── 与 publish.py 的桥接 ────────────────────────────────────────────────


def as_verify_payload(articles: list[dict]) -> dict:
    """把归一化后的文章列表伪装成 node CLI 的响应形状。

    ## 为什么值得写这个「伪装」

    `publish.py` 的反查判据（`count_title_kinds` / `find_gid`、ADR-0047/0053）是
    踩过坑的产物，而且和循环 C 的 `performance.py:classify_kind` 共用同一套
    「什么算一篇文章」的定义。换后端时如果顺手换一套判据，等于把那些教训重写一遍
    —— 而且两处判据可能悄悄漂移。

    所以 api 后端**不重写判据**，只把数据翻译成它认识的形状：

        {"items": [{"data": [{"assembleCell": {"itemCell": {"articleBase": {...}}}}]}]}

    `articleBase` 里给它真正会读的 5 个字段：`title` / `gidStr` / `articleURL` /
    `displayURL` / `content`。`content` 一律留空：`/mp/agw/article/list` 只返回
    图文（genre=Pgc），微头条根本不在这个列表里，所以它不可能是微头条。

    ## 一个必须写清楚的限制

    由此得出：**api 后端数不出微头条泄漏**。`verify_kinds.weitoutiao` 恒为 0，
    不是「没有泄漏」，而是「这个数据源看不见」。所以 api 后端只适合做只读核对，
    真发的反查仍应走 node（它拦的是 `/pgc/ma/content/...`，那一份含微头条）。
    `publish.py` 会把 `verify_backend` 记进账本，让这个差别在事后可查。
    """
    cells = []
    for a in articles:
        url = a.get("url") or ""
        cells.append({
            "assembleCell": {
                "itemCell": {
                    "articleBase": {
                        "title": a.get("title") or "",
                        "gidStr": str(a.get("gid") or ""),
                        "articleURL": url,
                        "displayURL": url,
                        "content": "",
                    }
                }
            },
            "dataType": 1,
        })
    return {"items": [{"data": cells}]}


def status_suffix(article: dict) -> str:
    """把平台状态翻译成 `publish.py` 原来用的后缀（`reviewing`/`rejected`/`published`）。

    顺序与旧的子串探测一致：先坏消息（rejected）→ 再审（reviewing）→ 最后 published。
    这样两边算出来的 verdict 字符串是同一套写法，账本不会因为换了后端而出现
    第三个词。
    """
    desc = str(article.get("status_desc") or "")
    if article.get("is_unpass") or any(h in desc for h in _REJECT_HINTS):
        return "rejected"
    if any(h in desc for h in _REVIEW_HINTS):
        return "reviewing"
    if article.get("is_passed") or "已发布" in desc:
        return "published"
    return ""


# ── 命令行（人工核对用）──────────────────────────────────────────────────


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="头条创作中心纯 HTTP 客户端（只读命令；写命令默认拒绝）"
    )
    ap.add_argument("--browser", default="chrome", help="从哪个浏览器读 cookie（默认 chrome）")
    ap.add_argument("--cookie-file", default=None,
                    help="从注入的 cookie 文件读会话（服务器上用；与 --browser 二选一）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="登录预检（浏览器关闭下可用）")
    p_list = sub.add_parser("list", help="作品列表（浏览器关闭下可用）")
    p_list.add_argument("--limit", type=int, default=20)
    sub.add_parser("drafts", help="草稿列表")
    sub.add_parser("draft-count", help="草稿数量")

    p_detail = sub.add_parser("detail", help="读一篇文章的标题与正文（只读）")
    p_detail.add_argument("--gid", required=True)
    p_detail.add_argument("--out", default=None,
                          help="把完整详情（含正文 HTML）写成 JSON 文件，用于拍快照")
    p_detail.add_argument("--content", action="store_true",
                          help="把正文 HTML 打到 stdout（默认只报长度）")

    p_delete = sub.add_parser("delete", help="删除一篇作品（写，默认拒绝）")
    p_delete.add_argument("--gid", required=True)
    p_delete.add_argument("--expect-title", required=True,
                          help="平台上的当前标题；不一致就拒绝动手")
    p_delete.add_argument("--allow-unverified-write", action="store_true",
                          help="确认承受写路径未验证的风险")
    p_delete.add_argument("--type", type=int, default=0,
                          help="删除接口的 type 参数（实测图文=0）")

    p_edit = sub.add_parser("edit", help="改写已发布文章的标题与正文（写，默认拒绝）")
    p_edit.add_argument("--gid", required=True)
    p_edit.add_argument("--expect-title", required=True,
                        help="平台上的当前标题；不一致就拒绝动手")
    p_edit.add_argument("--md", required=True, help="新的成稿 markdown（首行 # 标题可省）")
    p_edit.add_argument("--title", default=None,
                        help="新标题；不给就用 md 首行的 # 标题")
    p_edit.add_argument("--cover", default=None,
                        help="新封面；不给就沿用原封面且不重新上传")
    p_edit.add_argument("--declaration", default="个人观点")
    p_edit.add_argument("--snapshot-dir", default=".newsagg/snapshots",
                        help="改稿前把线上原文存到这里（默认 .newsagg/snapshots）")
    p_edit.add_argument("--allow-unverified-write", action="store_true",
                        help="确认承受写路径未验证的风险")
    args = ap.parse_args(argv)

    try:
        if args.cookie_file:
            try:
                client = Client.from_cookie_file(args.cookie_file)
            except CookieError as exc:
                print(json.dumps({"ok": False, "error": str(exc)},
                                 ensure_ascii=False, indent=2))
                return 1
        else:
            client = Client.from_browser(args.browser)
        if args.cmd == "login":
            st = client.login_status()
            # 只报布尔，绝不输出任何 cookie / session 字段
            out = {
                "ok": st["logged_in"],
                "logged_in": st["logged_in"],
                "checks": st["checks"],
            }
        elif args.cmd == "list":
            arts = client.list_articles(limit=args.limit)
            out = {
                "ok": True,
                "count": len(arts),
                "articles": [
                    {"title": a["title"], "gid": a["gid"], "status_desc": a["status_desc"],
                     "is_draft": a["is_draft"], "url": a["url"]}
                    for a in arts
                ],
            }
        elif args.cmd == "drafts":
            drafts = client.list_drafts()
            out = {"ok": True, "count": len(drafts), "drafts": drafts}
        elif args.cmd == "detail":
            detail = client.article_detail(args.gid)
            html = str(detail.get("content") or "")
            out = {
                "ok": True,
                "gid": _norm_gid(args.gid),
                "title": str(detail.get("title") or ""),
                "is_draft": bool(detail.get("is_draft")),
                "content_chars": len(html),
                "cover": _cover_from_detail(detail),
                "remain_modify_count": detail.get("remain_modify_count"),
            }
            if args.out:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(
                    json.dumps(detail, ensure_ascii=False, indent=1), encoding="utf-8"
                )
                out["saved_to"] = str(Path(args.out))
            if args.content:
                out["content"] = html
        elif args.cmd == "delete":
            out = {"ok": True, **client.delete_article(
                args.gid,
                expect_title=args.expect_title,
                allow_unverified=args.allow_unverified_write,
                item_type=args.type,
            )}
            out.pop("raw", None)
        else:  # edit
            md_path = Path(args.md)
            if not md_path.exists():
                print(json.dumps({"ok": False, "error": f"成稿不存在：{md_path}"},
                                 ensure_ascii=False, indent=2))
                return 1
            body, md_title = _split_title(md_path.read_text(encoding="utf-8"))
            title = (args.title or md_title or "").strip()
            if not title:
                print(json.dumps(
                    {"ok": False, "error": "没有标题：md 首行不是 `# 标题`，也没给 --title"},
                    ensure_ascii=False, indent=2))
                return 1
            # 改之前先落一份线上原文，改坏了能对回去
            before = client.article_detail(args.gid)
            snap_dir = Path(args.snapshot_dir)
            snap_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%dT%H%M%S")
            snap = snap_dir / f"{_norm_gid(args.gid)}-{stamp}.json"
            snap.write_text(
                json.dumps({"gid": args.gid, "title": before.get("title"),
                            "content": before.get("content"),
                            "is_draft": before.get("is_draft"),
                            "cover": _cover_from_detail(before)},
                           ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            cover_uri = cover_url = ""
            if args.cover:
                img = client.upload_image(args.cover, allow_unverified=True)
                cover_uri = str(img.get("uri") or "")
                cover_url = str(img.get("cover_url") or "")
            res = client.edit_article(
                args.gid,
                title=title,
                content_html=_markdown_to_html(body),
                expect_title=args.expect_title,
                cover_uri=cover_uri,
                cover_url=cover_url,
                declaration=args.declaration,
                allow_unverified=args.allow_unverified_write,
            )
            res.pop("raw", None)
            out = {"ok": True, "snapshot": str(snap), **res}
    except (ToutiaoError, LoginRequired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _split_title(text: str) -> tuple[str, str]:
    """把成稿拆成 `(正文, 标题)`。首行是 `# xxx` 就当标题，并从正文里去掉。

    去掉它是必须的：头条编辑器会把正文原样渲染，标题留在正文里就是
    「标题在正文第一行重复一遍」（ADR-0086 那次线上事故的形状）。
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("# "):
            return "\n".join(lines[i + 1:]).lstrip("\n"), line.strip()[2:].strip()
        if line.strip():
            break
    return text, ""


def _markdown_to_html(text: str) -> str:
    """复用发布层那一份转换，不另写一份。

    两份实现就是两个会各自漂移的真相：今天改了一边、明天忘了另一边，
    线上就会出现「同一篇文章用两条路发出来长得不一样」。函数名带下划线是
    发布层内部的约定，但这里跨模块调用它比复制一份更安全。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from publish import _markdown_to_html as impl  # noqa: PLC0415

    return impl(text)


if __name__ == "__main__":
    sys.exit(_main())
