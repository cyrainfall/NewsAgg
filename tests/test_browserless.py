"""底座 `.agents/lib/browserless` 的三条不可退让的性质。

为什么值得一个测试：这个包被五个技能的默认路径依赖，而它做的是**密码学**和
**读别人的凭据**这两件错了不会报错、只会静默出错的事。一次静默的密钥派生写错，
后果是所有技能都「跑得通但拿到乱码」，或者「跑得通但把 cookie 打进了日志」。
所以这里钉住的不是覆盖率，是三个具体的安全/正确性边界：

1. **AES 实现必须对得上权威向量**。自研密码学最危险的失败模式不是崩，是「算出一个
   看起来像密文的东西」。本仓没有 `cryptography` 可依赖（底座要坚持零第三方依赖），
   所以只能用 FIPS-197 / SP800-38A 的官方向量做外部校验 —— 这也是当初发现
   GHASH 位序写反的唯一手段。
2. **Cookie 的值绝不能出现在 `repr()` / `describe()` 里**。这两个是最容易被顺手
   打进日志或 JSON 输出的地方。一旦泄漏一次，就是「整套登录态进了聊天记录」。
3. **失败必须分类，不能混成一个异常**。`CookieError`（读不到 → 该退浏览器）与
   `AuthRequired`（没登录 → 该让用户去登录）在处置上是相反的。混在一起，上层
   就只能猜，而猜错的方向正是本仓反复吃亏的地方（ADR-0059/0062 一族）。

跑法：`python3 -m unittest discover -s tests -v`
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / ".agents" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from browserless import crypto  # noqa: E402
from browserless.chromium import Cookie, CookieJar, CookieError  # noqa: E402
from browserless.http import ApiError, AuthRequired  # noqa: E402


class TestAesVectors(unittest.TestCase):
    """标准库 AES 必须与 NIST 向量逐字节一致。"""

    def test_fips197_c1_aes128(self):
        """FIPS-197 附录 C.1：单分组加解密。"""
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plain = bytes.fromhex("00112233445566778899aabbccddeeff")
        cipher = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
        self.assertEqual(crypto.encrypt_block(key, plain), cipher)
        self.assertEqual(crypto.decrypt_block(key, cipher), plain)

    def test_fips197_c3_aes256(self):
        """FIPS-197 附录 C.3：256 位密钥。"""
        key = bytes.fromhex(
            "000102030405060708090a0b0c0d0e0f"
            "101112131415161718191a1b1c1d1e1f"
        )
        plain = bytes.fromhex("00112233445566778899aabbccddeeff")
        cipher = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")
        self.assertEqual(crypto.encrypt_block(key, plain), cipher)
        self.assertEqual(crypto.decrypt_block(key, cipher), plain)

    def test_sp800_38a_cbc_aes128(self):
        """SP800-38A F.2.1：CBC 多分组，验证链式正确。

        这一条单独存在是因为单分组对只能证明 `encrypt_block` 对，证明不了 CBC
        的 XOR 链没接错 —— 而 Chromium 的 v10 cookie 正是 CBC。
        """
        key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plain = bytes.fromhex(
            "6bc1bee22e409f96e93d7e117393172a"
            "ae2d8a571e03ac9c9eb76fac45af8e51"
        )
        cipher = bytes.fromhex(
            "7649abac8119b246cee98e9b12e9197d"
            "5086cb9b507219ee95db113a917678b2"
        )
        # AES 没有加密方向的多分组公开 API，用逐块 + 手接 XOR 验证解密方向。
        self.assertEqual(crypto.aes_cbc_decrypt(key, iv, cipher), plain)

    def test_gcm_tag_rejects_wrong_key(self):
        """GCM 必须验 tag；密钥错了要抛，不能返回垃圾明文。

        这条比「能解密」更重要：v20 cookie 用的是 GCM，若实现不校验 tag，
        换台机器读出来的就是乱码而不是一个明确的失败。
        """
        key = bytes.fromhex("feffe9928665731c6d6a8f9467308308")
        iv = bytes.fromhex("cafebabefacedbaddecaf888")
        ct = bytes(16)
        tag = bytes(16)
        with self.assertRaises(ValueError):
            crypto.aes_gcm_decrypt(key, iv, ct, tag)

    def test_gcm_key_length_validation(self):
        """AES-128 与 AES-256 的 GCM 都合法，24 字节（AES-192 无 GCM 标准用法）非法。

        这条来自一个真实 bug：初版只接受 32 字节，把合法的 AES-128 GCM 也拒了。
        非法长度必须抛 ValueError，不能悄悄按某个长度截断。
        """
        for bad in (0, 8, 24, 33):
            with self.assertRaises(ValueError):
                crypto.aes_gcm_decrypt(bytes(bad), bytes(12), b"", bytes(16))


class TestCookieNeverLeaksValue(unittest.TestCase):
    """Cookie 值不得出现在任何字符串化结果里。"""

    SECRET = "SUPER_SECRET_SESSDATA_VALUE_9f3a"

    def _jar(self) -> CookieJar:
        c = Cookie(
            domain=".bilibili.com",
            name="SESSDATA",
            value=self.SECRET,
            path="/",
            secure=True,
            http_only=True,
            expires_utc=0,
            same_site=-1,
        )
        return CookieJar(cookies=[c], browser="chrome", profile="Default")

    def test_repr_hides_value(self):
        text = repr(self._jar())
        self.assertNotIn(self.SECRET, text)
        self.assertIn("bilibili.com", text)

    def test_describe_hides_value(self):
        """describe() 是唯一允许进 JSON 的形式，因此它最危险。"""
        import json

        payload = json.dumps(self._jar().describe(), ensure_ascii=False)
        self.assertNotIn(self.SECRET, payload)

    def test_header_does_contain_value(self):
        """反过来也要钉住：header() 必须真的带值，否则请求根本发不出去。

        保护与功能是一对：只测「不泄漏」会得到把所有值都删掉的实现。
        """
        self.assertIn(self.SECRET, self._jar().header("bilibili.com"))


class TestErrorTaxonomy(unittest.TestCase):
    """三种失败必须可区分 —— 它们的处置方向是相反的。"""

    def test_auth_required_is_api_error(self):
        """AuthRequired 是 ApiError 的子类，方便上层统一兜底。"""
        self.assertTrue(issubclass(AuthRequired, ApiError))

    def test_cookie_error_is_not_auth_required(self):
        """读不到 cookie ≠ 没登录。

        混为一谈的后果：Cookie 读不到时提示用户「请重新登录」，而用户明明登录着，
        于是他做什么都没用。正确处置是退回浏览器路径。
        """
        self.assertFalse(issubclass(CookieError, AuthRequired))
        self.assertFalse(issubclass(CookieError, ApiError))

    def test_api_error_carries_code(self):
        err = ApiError("账号未登录", code=-101, status=200)
        self.assertEqual(err.code, -101)
        self.assertEqual(err.status, 200)
        self.assertIn("账号未登录", str(err))


if __name__ == "__main__":
    unittest.main()
