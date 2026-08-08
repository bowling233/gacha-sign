"""公共密码学工具。

集中放置各平台复用的签名、加密、设备ID 生成函数：
- :func:`md5_hex`          —— MD5 摘要（塔吉多请求签名）
- :func:`aes_ecb_base64`   —— AES-128-ECB/PKCS7 加密（塔吉多登录字段加密）
- :func:`des_ecb_nullpad`  —— DES-ECB 空字节填充加密（森空岛数美指纹）
- :func:`random_device_id` —— 生成随机设备标识
"""

from __future__ import annotations

import base64
import hashlib
import uuid


def md5_hex(text: str) -> str:
    """计算字符串的 MD5 十六进制摘要。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def aes_ecb_base64(plaintext: str, secret: str) -> str:
    """AES-128-ECB / PKCS7 加密，返回 base64 字符串。

    key 取 ``secret`` 末 16 字节（与塔吉多 laohu SDK 一致）。
    """
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = secret[-16:].encode("utf-8")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("utf-8")


def random_device_id() -> str:
    """生成随机设备 ID（32 位 hex，无连字符）。"""
    return uuid.uuid4().hex


def des_ecb_nullpad(key: bytes, data: bytes) -> bytes:
    """单 DES-ECB 加密，空字节填充（对齐到 8 的倍数）。

    ``cryptography`` 未直接提供单 DES，用 TripleDES(K‖K‖K) 退化实现。
    用于森空岛数美（ShuMei）设备指纹的字段加密。
    """
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    from cryptography.hazmat.primitives.ciphers import Cipher, modes

    key8 = key[:8].ljust(8, b"\x00")
    pad_len = 8 - (len(data) % 8)  # 始终填充 1~8 字节
    padded = data + b"\x00" * pad_len
    cipher = Cipher(TripleDES(key8 * 3), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(padded) + enc.finalize()
