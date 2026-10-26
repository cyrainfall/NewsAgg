"""纯标准库的 AES-CBC / AES-GCM 解密原语。

为什么自己实现而不用 `cryptography`：

本仓库的浏览器技能要读取本机 Chromium 系浏览器的 Cookie，这些 Cookie 在磁盘上
是加密的（Chrome 80 之后统一为 v10/v11 前缀）。解密需要 AES：

- macOS / Linux 的 v10 用的是 **AES-128-CBC**（密钥由 Keychain/libsecret 取到的
  口令 + PBKDF2 派生）；
- Windows 的 v10/v11 用的是 **AES-256-GCM**（密钥由 DPAPI 解 Local State 里的
  `os_crypt.encrypted_key` 得到）。

装 `cryptography` 当然更省事，但它是 C 扩展：换一台机器、换一个 Python 版本就要
重编，而这套脚本的卖点是「clone 下来就能跑」。所以这里用纯 Python 实现两种模式。
性能不是问题 —— 只有几百条 Cookie、每条几十字节。

正确性由 `tests/test_aes.py` 对照 NIST/FIPS-197 官方测试向量保证，不靠感觉。
"""

from __future__ import annotations

import struct

# ── AES 核心 ────────────────────────────────────────────────────────────
#
# 教材式的逐字节实现：S 盒 + 列混合 + 轮密钥加。慢，但每一步都能对着
# FIPS-197 的标准图示核对，出问题容易定位。

_SBOX = bytes(
    [
        0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
        0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
        0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
        0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
        0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
        0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
        0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
        0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
        0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
        0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
        0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
        0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
        0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
        0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
        0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
        0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
    ]
)

_INV_SBOX = bytes(256)
_inv = bytearray(256)
for _i, _v in enumerate(_SBOX):
    _inv[_v] = _i
_INV_SBOX = bytes(_inv)
del _i, _v, _inv

_RCON = bytes(
    [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36, 0x6C, 0xD8, 0xAB, 0x4D]
)


def _xtime(a: int) -> int:
    """GF(2^8) 上乘以 2（AES 规定的本原多项式 0x11B）。"""
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


def _gmul(a: int, b: int) -> int:
    """GF(2^8) 乘法，用于逆列混合的 9/11/13/14 系数。"""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        b >>= 1
        a = _xtime(a)
    return p


def _expand_key(key: bytes) -> list[bytes]:
    """密钥扩展。返回按 16 字节分块的轮密钥列表（含第 0 轮）。"""
    nk = len(key) // 4
    if nk not in (4, 6, 8):
        raise ValueError(f"AES 密钥长度必须是 16/24/32 字节，收到 {len(key)}")
    nr = nk + 6  # 轮数：128→10, 192→12, 256→14
    w = [list(key[4 * i : 4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        temp = list(w[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]  # RotWord
            temp = [_SBOX[b] for b in temp]  # SubWord
            temp[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            temp = [_SBOX[b] for b in temp]
        w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
    return [bytes(b for word in w[4 * r : 4 * r + 4] for b in word) for r in range(nr + 1)]


def _add_round_key(state: list[int], rk: bytes) -> None:
    for i in range(16):
        state[i] ^= rk[i]


def _shift_rows(s: list[int]) -> None:
    # 状态按列优先存放：s[c*4 + r] = 列 c 的行 r
    s[1], s[5], s[9], s[13] = s[5], s[9], s[13], s[1]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[15], s[3], s[7], s[11]


def _inv_shift_rows(s: list[int]) -> None:
    s[1], s[5], s[9], s[13] = s[13], s[1], s[5], s[9]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[7], s[11], s[15], s[3]


def _mix_columns(s: list[int]) -> None:
    for c in range(4):
        i = c * 4
        a0, a1, a2, a3 = s[i], s[i + 1], s[i + 2], s[i + 3]
        s[i] = _xtime(a0) ^ (_xtime(a1) ^ a1) ^ a2 ^ a3
        s[i + 1] = a0 ^ _xtime(a1) ^ (_xtime(a2) ^ a2) ^ a3
        s[i + 2] = a0 ^ a1 ^ _xtime(a2) ^ (_xtime(a3) ^ a3)
        s[i + 3] = (_xtime(a0) ^ a0) ^ a1 ^ a2 ^ _xtime(a3)


def _inv_mix_columns(s: list[int]) -> None:
    for c in range(4):
        i = c * 4
        a0, a1, a2, a3 = s[i], s[i + 1], s[i + 2], s[i + 3]
        s[i] = _gmul(a0, 14) ^ _gmul(a1, 11) ^ _gmul(a2, 13) ^ _gmul(a3, 9)
        s[i + 1] = _gmul(a0, 9) ^ _gmul(a1, 14) ^ _gmul(a2, 11) ^ _gmul(a3, 13)
        s[i + 2] = _gmul(a0, 13) ^ _gmul(a1, 9) ^ _gmul(a2, 14) ^ _gmul(a3, 11)
        s[i + 3] = _gmul(a0, 11) ^ _gmul(a1, 13) ^ _gmul(a2, 9) ^ _gmul(a3, 14)


def encrypt_block(key: bytes, block: bytes) -> bytes:
    """AES 单块加密（16 字节进、16 字节出）。GCM 的 CTR 模式要用它。"""
    if len(block) != 16:
        raise ValueError("block 必须正好 16 字节")
    rk = _expand_key(key)
    s = list(block)
    _add_round_key(s, rk[0])
    for r in range(1, len(rk) - 1):
        s = [_SBOX[b] for b in s]
        _shift_rows(s)
        _mix_columns(s)
        _add_round_key(s, rk[r])
    s = [_SBOX[b] for b in s]
    _shift_rows(s)
    _add_round_key(s, rk[-1])
    return bytes(s)


def decrypt_block(key: bytes, block: bytes) -> bytes:
    """AES 单块解密。CBC 模式要用它。"""
    if len(block) != 16:
        raise ValueError("block 必须正好 16 字节")
    rk = _expand_key(key)
    s = list(block)
    _add_round_key(s, rk[-1])
    for r in range(len(rk) - 2, 0, -1):
        _inv_shift_rows(s)
        s = [_INV_SBOX[b] for b in s]
        _add_round_key(s, rk[r])
        _inv_mix_columns(s)
    _inv_shift_rows(s)
    s = [_INV_SBOX[b] for b in s]
    _add_round_key(s, rk[0])
    return bytes(s)


# ── CBC ────────────────────────────────────────────────────────────────


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-CBC 解密，**不去 PKCS#7 填充**（Chromium 的填充由调用方判断）。

    Chromium 的 v10 cookie 是 AES-128-CBC + 空格(0x20)填充，不是标准 PKCS#7，
    所以这里保持原样返回，让 `chromium.py` 自己 rstrip。
    """
    if len(data) % 16:
        raise ValueError(f"CBC 密文长度必须是 16 的倍数，收到 {len(data)}")
    if len(iv) != 16:
        raise ValueError("IV 必须 16 字节")
    prev = iv
    out = bytearray()
    for off in range(0, len(data), 16):
        chunk = data[off : off + 16]
        plain = decrypt_block(key, chunk)
        out += bytes(a ^ b for a, b in zip(plain, prev))
        prev = chunk
    return bytes(out)


# ── GCM（Windows v10/v11 用） ───────────────────────────────────────────


def _ghash_mul(x: int, h: int) -> int:
    """GF(2^128) 乘法，GCM 规定的位序（MSB-first，多项式 x^128+x^7+x^2+x+1）。

    置位/移位方向对照 GCM 规范 Algorithm 1（V 的 bit 0 是**最低位**，右移）：
    已用规范 Figure 2 的样例值校验（H=66e9…，X=0388… → 5e2e…）。
    """
    r = 0xE1 << 120
    z = 0
    v = h
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ r
        else:
            v >>= 1
    return z


def _ghash(h: int, aad: bytes, ct: bytes) -> int:
    def _blocks(data: bytes):
        for off in range(0, len(data), 16):
            yield data[off : off + 16]

    y = 0
    padded_aad = aad + b"\x00" * ((16 - len(aad) % 16) % 16)
    padded_ct = ct + b"\x00" * ((16 - len(ct) % 16) % 16)
    for blk in list(_blocks(padded_aad)) + list(_blocks(padded_ct)):
        y = _ghash_mul(y ^ int.from_bytes(blk, "big"), h)
    lens = struct.pack(">QQ", len(aad) * 8, len(ct) * 8)
    y = _ghash_mul(y ^ int.from_bytes(lens, "big"), h)
    return y


def _ctr(key: bytes, iv12: bytes, data: bytes, counter_start: int = 2) -> bytes:
    out = bytearray()
    ctr = counter_start
    for off in range(0, len(data), 16):
        ks = encrypt_block(key, iv12 + struct.pack(">I", ctr))
        chunk = data[off : off + 16]
        out += bytes(a ^ b for a, b in zip(chunk, ks[: len(chunk)]))
        ctr = (ctr + 1) & 0xFFFFFFFF
    return bytes(out)


def aes_gcm_decrypt(key: bytes, iv: bytes, data: bytes, tag: bytes) -> bytes:
    """AES-GCM 解密（无 AAD，Chromium 正是这样用的）并校验 tag。

    tag 校验失败抛 `ValueError` —— 不要静默返回乱码，否则会把
    「密钥不对」误诊成「Cookie 格式变了」。
    """
    if len(key) not in (16, 24, 32):
        raise ValueError(f"GCM 密钥长度必须是 16/24/32 字节，收到 {len(key)}")
    if len(iv) != 12:
        raise ValueError("GCM nonce 必须是 12 字节")
    h = int.from_bytes(encrypt_block(key, b"\x00" * 16), "big")
    plain = _ctr(key, iv, data)
    expected = _ghash(h, b"", data) ^ int.from_bytes(
        encrypt_block(key, iv + struct.pack(">I", 1)), "big"
    )
    if expected.to_bytes(16, "big") != tag:
        raise ValueError("GCM tag 校验失败：密钥或密文不匹配")
    return plain
