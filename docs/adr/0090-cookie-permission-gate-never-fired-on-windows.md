# cookie 权限闸在 Windows 上从没响过：三层叠起来的「一直通过」

## Context

2026-10-31 做 P0 优化时，为了让测试套件能在 Windows 上跑绿，去看了两条一直失败的用例
（`test_rejects_group_or_other_readable_file` / `test_rejects_file_readable_by_group_only`）。
我以为它们是「POSIX 权限位在 NTFS 上测不了」的平台限制，准备加个 skip 了事。

实测之后发现不是：**那道闸在 Windows 上从来没拦过任何东西**，而它守的是账号凭证。

顺带说明它为什么一直没被发现：ADR-0080 记过「Windows 上 chmod 无效，于是 `from_cookie_file`
必然拒绝，api 后端在本机走不通」。这条记录是**当时的实况**，但它掩盖了后来的改动——
实现换了判据（改成按平台分叉、Windows 查 DACL），而两条对应 Windows 的用例从来没写。
失败的两条用例在别人的机器（POSIX）上是绿的，在这台机器上是红的，于是被当成「已知平台差异」
记进残余风险 84，一记就是几天。

## 发现：三层，每一层都足以让它「一直通过」

### 一、`icacls /findsid` 的裸 SID 被当成名字

```text
icacls <file> /findsid S-1-1-0        → 1332「未找到任何具有匹配 SID 的文件」
icacls <file> /findsid *S-1-1-0       → 0（找到了）
```

给文件显式 `Everyone:(R)` 之后，裸 SID 仍然返回 1332。原代码把 `1332` 当作「没找到＝安全」，
于是**每一次调用都走进「安全」分支**。星号是 icacls 的「这是 SID 不是名字」标记。

### 二、补上星号之后，退出码**恒为 0**

`*SID` 形式下，只要命令跑成功就返回 0——**哪怕一个文件都没匹配到**。原判据是
`returncode == 0 → 泄露`，所以只修第一层会把**每一个**文件都判成泄露（包括普通临时文件）。
真正的信号在输出里：命中时 icacls 打印 `<绝对路径> Everyone:(R)`，没命中只打印
「未找到任何…文件」。判据改成「输出里有没有该文件路径」——用路径而不是那句中文，
因为 icacls 的消息随系统语言变。

### 三、`CookieError` 在模块级函数里根本没导入

```text
NameError: name 'CookieError' is not defined
```

`_check_cookie_file_exposure()` 是模块级函数，而 `CookieError` 只在 `from_cookie_file()`
内部**局部导入**。所以闸一旦真的判断要拒，它抛的是 `NameError` 而不是 `CookieError`——
调用方看到的仍然是一次异常，但「为什么被拒」这个信息没了。这一层被第一层完全盖住：
既然从不拒绝，就永远走不到 raise。

## 决定

三层一起修（`http.py`）：`/findsid *SID` + 按输出里的路径判定 + 局部导入 `CookieError`。
并补一条**只在 Windows 跑**的用例：给临时文件 `Everyone:(R)`，必须被拒；去掉授权后必须能加载。
那两条 POSIX 用例加 `skipUnless(os.name != "nt", ...)`，跳过理由写清是「NTFS 没有 mode 位，
Windows 走 DACL」，指向新用例，而不是含糊的「平台差异」。

## 后果

- **真实 cookie 文件的 ACL 是干净的**（`SYSTEM / Administrators / cyx / 一个应用 SID / Low
  Mandatory Label`，没有 Everyone / Users / Authenticated Users），所以闸修好之后它照常通过，
  api 后端继续可用。这是刻意的验证：修闸不该顺手把功能打死。
- 如果哪天那份文件被 `chmod 644` 式的手滑放开，或者被放进一个 Everyone 可读的目录，
  现在**会**被拒，并且错误信息给出收权命令（`icacls ... /inheritance:r /grant:r ...`）。
  在此之前，同一次手滑不会有任何提示。

## 这是本仓库第三个「空转的闸」

前两个：ADR-0057（`unittest discover` 发现 0 个测试时退出码 0，绿色是假的）、
ADR-0081（「每日 1 篇」的闸上溯错目录，账本恒空）。三个的形状完全相同：

> **判据写下来了，但没有东西验证它真的会响；而它不响的时候，退出码和输出都像正常。**

区别只在代价：前两个浪费产能，这一个把账号凭证的防线换成了装饰。所以本 ADR 的落点不是
「修好 icacls」，而是：**每一道闸都要有一条「让它必须响」的用例**——写闸的时候顺手写下
它该拦的那一次，比事后读代码发现它从没拦过便宜得多。
