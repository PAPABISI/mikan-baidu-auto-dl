# -*- coding: utf-8 -*-
"""
Edge cookie 解密模块（纯标准库实现，无第三方依赖）
- DPAPI: crypt32.CryptUnprotectData —— 解出 Local State 里的 AES 主密钥
- AES-256-GCM: bcrypt.dll (Windows CNG) —— 解密 v10 前缀的 cookie 值
仅限本机当前用户解密自己的浏览器数据。
"""
import base64
import ctypes
import json
import os
from ctypes import wintypes

crypt32 = ctypes.windll.crypt32
bcrypt = ctypes.windll.bcrypt


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def dpapi_unprotect(data: bytes) -> bytes:
    """Windows DPAPI 解密（绑定当前用户）。"""
    blob_in = _blob(data)
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def get_edge_master_key(user_data_dir: str) -> bytes:
    """从 Edge Local State 提取并解密 AES-256 主密钥。"""
    ls = json.load(open(os.path.join(user_data_dir, "Local State"), encoding="utf-8"))
    enc = base64.b64decode(ls["os_crypt"]["encrypted_key"])
    assert enc[:5] == b"DPAPI", "未知的密钥保护方式"
    return dpapi_unprotect(enc[5:])


# ---------- AES-GCM via CNG ----------
BCRYPT_CHAIN_MODE_GCM = wintypes.LPCWSTR("ChainingModeGCM")


class BCRYPT_AUTH_CIPHER_MODE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.ULONG),
        ("dwInfoVersion", wintypes.ULONG),
        ("pbNonce", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbNonce", wintypes.ULONG),
        ("pbAuthData", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbAuthData", wintypes.ULONG),
        ("pbTag", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbTag", wintypes.ULONG),
        ("pbMacContext", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbMacContext", wintypes.ULONG),
        ("cbAAD", wintypes.ULONG),
        ("cbData", ctypes.c_ulonglong),
        ("dwFlags", wintypes.ULONG),
    ]


def _arr(data: bytes):
    return (ctypes.c_ubyte * len(data)).from_buffer_copy(data) if data else None


class AesGcm:
    def __init__(self, key: bytes):
        self.alg = wintypes.LPCVOID()
        if bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(self.alg), "AES", None, 0):
            raise ctypes.WinError()
        if bcrypt.BCryptSetProperty(self.alg, "ChainingMode", BCRYPT_CHAIN_MODE_GCM, 30, 0):
            raise ctypes.WinError()
        self.hkey = wintypes.LPCVOID()
        kb = _arr(key)
        if bcrypt.BCryptGenerateSymmetricKey(self.alg, ctypes.byref(self.hkey), None, 0, kb, len(key), 0):
            raise ctypes.WinError()

    def decrypt(self, nonce: bytes, ct: bytes, tag: bytes) -> bytes:
        nonce_b, tag_b = _arr(nonce), _arr(tag)
        ct_b = _arr(ct)
        out = (ctypes.c_ubyte * len(ct))()
        info = BCRYPT_AUTH_CIPHER_MODE_INFO(
            ctypes.sizeof(BCRYPT_AUTH_CIPHER_MODE_INFO), 1,
            nonce_b, len(nonce), None, 0, tag_b, len(tag),
            None, 0, 0, 0, 0,
        )
        need = wintypes.ULONG(0)
        r = bcrypt.BCryptDecrypt(self.hkey, ct_b, len(ct), ctypes.byref(info), None, 0,
                                 out, len(ct), ctypes.byref(need), 0)
        if r:
            raise ctypes.WinError(r)
        return bytes(bytearray(out[: need.value]))

    def close(self):
        bcrypt.BCryptDestroyKey(self.hkey)
        bcrypt.BCryptCloseAlgorithmProvider(self.alg, 0)


def decrypt_cookie_value(master_key: bytes, encrypted_value: bytes) -> str:
    """解密 Chromium v10 格式 cookie 值。v20 (app-bound) 会抛异常。"""
    if encrypted_value[:3] == b"v10":
        gcm = AesGcm(master_key)
        try:
            pt = gcm.decrypt(encrypted_value[3:15], encrypted_value[15:-16], encrypted_value[-16:])
        finally:
            gcm.close()
        return pt.decode("utf-8", "replace")
    raise ValueError(f"不支持的加密前缀: {encrypted_value[:4]!r}")


if __name__ == "__main__":
    # 自测 1：能否取出 Edge 主密钥
    edge_dir = r"C:\Users\u00uk\AppData\Local\Microsoft\Edge\User Data"
    key = get_edge_master_key(edge_dir)
    print("主密钥长度:", len(key), "字节 (期望 32)")

    # 自测 2：AES-GCM 往返（用已知测试向量）
    # NIST 风格自造向量：key=32字节, nonce=12字节
    import os as _os
    gcm = AesGcm(key)
    pt = b"workbuddy-self-test"
    nonce = _os.urandom(12)
    # 用 BCryptEncrypt 加密
    tag = (ctypes.c_ubyte * 16)()
    nonce_b = _arr(nonce)
    pt_b = _arr(pt)
    out = (ctypes.c_ubyte * len(pt))()
    info = BCRYPT_AUTH_CIPHER_MODE_INFO(
        ctypes.sizeof(BCRYPT_AUTH_CIPHER_MODE_INFO), 1,
        nonce_b, 12, None, 0, tag, 16, None, 0, 0, 0, 0,
    )
    need = wintypes.ULONG(0)
    r = bcrypt.BCryptEncrypt(gcm.hkey, pt_b, len(pt), ctypes.byref(info), None, 0,
                             out, len(pt), ctypes.byref(need), 0)
    assert r == 0, hex(r & 0xFFFFFFFF)
    ct = bytes(bytearray(out[: need.value]))
    tag_b = bytes(bytearray(tag))
    back = gcm.decrypt(nonce, ct, tag_b)
    gcm.close()
    print("AES-GCM 往返:", "OK" if back == pt else f"FAIL {back!r}")
