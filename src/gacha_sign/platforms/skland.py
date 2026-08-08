"""森空岛（明日方舟 / 终末地）签到平台实现。

凭证：手机号 + 密码（鹰角通行证全自动登录）。
登录链路：密码登录拿 hypergryph token → OAuth grant code → skland cred。
skland API 请求需 HMAC-SHA256 签名（key=credToken），头需数美设备指纹 dId。

主要参考：
- reference/Skland-Sign-In/skland_api.py（dId 生成、签名、签到流程）
- reference/skyland-auto-sign/src/skyland.py（端点、登录方式）
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from .. import crypto
from ..base import (
    Account,
    AuthExpiredError,
    CheckinResult,
    CheckinStatus,
    PlatformBase,
)
from ..http import HttpClient, ApiResponse

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 12; SM-A5560 Build/V417IR; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/101.0.4951.61 Safari/537.36; SKLand/1.52.1"
)
APP_CODE = "4ca99fa6b56cc2ba"

# 鹰角通行证（Hypergryph passport）接口
PS_BASE = "https://as.hypergryph.com"
URL_PASSWORD_LOGIN = f"{PS_BASE}/user/auth/v1/token_by_phone_password"
URL_PHONE_CODE_SEND = f"{PS_BASE}/general/v1/send_phone_code"
URL_TOKEN_BY_PHONE_CODE = f"{PS_BASE}/user/auth/v2/token_by_phone_code"
URL_OAUTH_GRANT = f"{PS_BASE}/user/oauth2/v2/grant"

# 森空岛接口
SK_BASE = "https://zonai.skland.com"
URL_GENERATE_CRED = f"{SK_BASE}/web/v1/user/auth/generate_cred_by_code"
URL_BINDING_LIST = f"{SK_BASE}/api/v1/game/player/binding"
URL_SIGN_ARKNIGHTS = f"{SK_BASE}/api/v1/game/attendance"
URL_SIGN_ENDFIELD = f"{SK_BASE}/web/v1/game/endfield/attendance"

# 数美（ShuMei）设备指纹
SM_ORG = "UWXspnCCJN4sfYlNfqps"
SM_DEVICE_URL = "https://fp-it.portal101.cn/deviceprofile/v4"
RSA_PUBLIC_KEY = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCmxMNr7n8ZeT0tE1R9j/mP"
    "ixoinPkeM+k4VGIn/s0k7N5rJAfnZ0eMER+QhwFvshzo0LNmeUkpR8uIlU/G"
    "EVr8mN28sKmwd2gpygqj0ePnBmOW4v0ZVwbSYK+izkhVFk2V/doLoMbWy6b+"
    "UnA8mkjvg0iYWRByfRsK2gdl7llqCwIDAQAB"
)

# 数美指纹字段 → DES 加密规则（字段名混淆映射）
_DES_RULE: dict[str, dict] = {
    "appId": {"key": "uy7mzc4h", "name": "xx"},
    "canvas": {"key": "snrn887t", "name": "yk"},
    "clientSize": {"key": "cpmjjgsu", "name": "zx"},
    "organization": {"key": "78moqjfc", "name": "dp"},
    "os": {"key": "je6vk6t4", "name": "pj"},
    "platform": {"key": "pakxhcd2", "name": "gm"},
    "plugins": {"key": "v51m3pzl", "name": "kq"},
    "pmf": {"key": "2mdeslu3", "name": "vw"},
    "referer": {"key": "y7bmrjlc", "name": "ab"},
    "res": {"key": "whxqm2a7", "name": "hf"},
    "rtype": {"key": "x8o2h2bl", "name": "lo"},
    "sdkver": {"key": "9q3dcxp2", "name": "sc"},
    "status": {"key": "2jbrxxw4", "name": "an"},
    "subVersion": {"key": "eo3i2puh", "name": "ns"},
    "svm": {"key": "fzj3kaeh", "name": "qr"},
    "time": {"key": "q2t3odsk", "name": "nb"},
    "timezone": {"key": "1uv05lj5", "name": "as"},
    "tn": {"key": "x9nzj1bp", "name": "py"},
    "trees": {"key": "acfs0xo4", "name": "pi"},
    "ua": {"key": "k92crp1t", "name": "bj"},
    "url": {"key": "y95hjkoo", "name": "cf"},
    "vpw": {"key": "r9924ab5", "name": "ca"},
    "smid": {"key": "vbe1o0m3", "name": "mo"},
    "box": {"key": None, "name": "jf"},
    "protocol": {"key": None, "name": "protocol"},
    "version": {"key": None, "name": "version"},
}

_BROWSER_ENV = {
    "plugins": "MicrosoftEdgePDFPluginPortableDocumentFormatinternal-pdf-viewer1",
    "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
    "canvas": "259ffe69",
    "timezone": -480,
    "platform": "Win32",
    "url": "https://www.skland.com/",
    "referer": "",
    "res": "1920_1080_24_1.25",
    "clientSize": "0_0_1080_1920_1920_1080_1920_1080",
    "status": "0011",
}

_ALREADY_SIGNED_RE = re.compile(r"已签到|请勿重复|重复签到|签到过|今日已", re.IGNORECASE)


class SklandPlatform(PlatformBase):
    """森空岛签到（明日方舟 / 终末地）。"""

    name = "skland"
    display_name = "森空岛"

    def __init__(self, account: Account, http: HttpClient, options=None):
        super().__init__(account, http, options)
        # 用户配置（config.yaml）
        self.phone: str = self.account.get("phone", "") or ""
        self.password: str = self.account.get("password", "") or ""
        # 运行时凭据
        self._did: str = self.account.cred_get("did", "") or ""
        self._hg_token: str = ""        # 鹰角通行证 token（每次登录获取）
        self._cred: str = ""            # skland cred
        self._cred_token: str = ""      # 签名用 token

    # ------------------------------------------------------------------
    # 数美设备指纹 dId
    # ------------------------------------------------------------------
    def _get_did(self) -> str:
        """生成数美设备指纹 dId（缓存到 credentials.json）。"""
        if self._did:
            return self._did
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.serialization import load_der_public_key

        uid = str(uuid.uuid4())
        pri_id = hashlib.md5(uid.encode()).digest()[:8].hex()

        # RSA 加密 UUID
        pub = load_der_public_key(base64.b64decode(RSA_PUBLIC_KEY))
        ep = base64.b64encode(pub.encrypt(uid.encode(), padding.PKCS1v15())).decode()

        # 组装指纹数据
        now_ms = int(time.time() * 1000)
        target: dict[str, Any] = {
            "protocol": 102, "organization": SM_ORG, "appId": "default",
            "os": "web", "version": "3.0.0", "sdkver": "3.0.0",
            "box": "", "rtype": "all", "subVersion": "1.0.0", "time": 0,
            "smid": self._gen_smid(),
        }
        target.update(_BROWSER_ENV)
        target["vpw"] = str(uuid.uuid4())
        target["trees"] = str(uuid.uuid4())
        target["svm"] = now_ms
        target["pmf"] = now_ms
        target["tn"] = hashlib.md5(self._tn_input(target).encode()).hexdigest()

        # DES 加密各字段 → gzip → AES
        encrypted_fields: dict[str, Any] = {}
        for k, v in target.items():
            rule = _DES_RULE.get(k)
            if not rule:
                encrypted_fields[k] = v
            elif rule["key"] is None:
                encrypted_fields[rule["name"]] = v
            else:
                enc = crypto.des_ecb_nullpad(rule["key"].encode(), str(v).encode())
                encrypted_fields[rule["name"]] = base64.b64encode(enc).decode()
        compressed = gzip.compress(
            json.dumps(encrypted_fields, separators=(",", ":")).encode(), compresslevel=2
        )
        aes_data = self._aes_cbc_hex(compressed, pri_id.encode())

        resp = self._http.post_json(
            SM_DEVICE_URL,
            json_body={
                "appId": "default", "compress": 2, "data": aes_data,
                "encode": 5, "ep": ep, "organization": SM_ORG, "os": "web",
            },
            headers={"Content-Type": "application/json"},
        )
        data = resp.raw_json if isinstance(resp.raw_json, dict) else {}
        if data.get("code") != 1100:
            raise RuntimeError(f"数美 dId 获取失败: {data}")
        self._did = f"B{data['detail']['deviceId']}"
        self.account.cred_set("did", self._did)
        return self._did

    @staticmethod
    def _gen_smid() -> str:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        v = f"{ts}{hashlib.md5(uuid.uuid4().hex.encode()).hexdigest()}00"
        suffix = hashlib.md5(f"smsk_web_{v}".encode()).digest()[:7].hex()
        return f"{v}{suffix}0"

    @staticmethod
    def _tn_input(data: dict) -> str:
        parts: list[str] = []
        for k in sorted(data.keys()):
            v = data[k]
            if isinstance(v, int):
                parts.append(str(v * 10000))
            elif isinstance(v, dict):
                parts.append(SklandPlatform._tn_input(v))
            else:
                parts.append(str(v) if v else "")
        return "".join(parts)

    @staticmethod
    def _aes_cbc_hex(data: bytes, key: bytes) -> str:
        from cryptography.hazmat.primitives import padding as sym_pad
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        b64 = base64.b64encode(data)
        pad_len = 16 - (len(b64) % 16)
        if pad_len < 16:
            b64 += b"\x00" * pad_len
        padder = sym_pad.PKCS7(128).padder()
        padded = padder.update(b64) + padder.finalize()
        cipher = Cipher(algorithms.AES(key), modes.CBC(b"0102030405060708"))
        enc = cipher.encryptor()
        return (enc.update(padded) + enc.finalize()).hex()

    # ------------------------------------------------------------------
    # 登录链路
    # ------------------------------------------------------------------
    def _base_headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
            "Connection": "close",
            "X-Requested-With": "com.hypergryph.skland",
            "dId": self._get_did(),
        }

    def _login_by_password(self) -> str:
        """鹰角通行证密码登录，返回 hypergryph token。"""
        resp = self._http.post_json(
            URL_PASSWORD_LOGIN,
            json_body={"phone": self.phone, "password": self.password},
            headers=self._base_headers(),
        )
        data = resp.raw_json if isinstance(resp.raw_json, dict) else {}
        if data.get("status") != 0:
            raise AuthExpiredError(f"森空岛密码登录失败: {data.get('msg') or data}")
        return data["data"]["token"]

    def _get_grant_code(self, token: str) -> str:
        resp = self._http.post_json(
            URL_OAUTH_GRANT,
            json_body={"appCode": APP_CODE, "token": token, "type": 0},
            headers=self._base_headers(),
        )
        data = resp.raw_json if isinstance(resp.raw_json, dict) else {}
        if data.get("status") != 0:
            raise AuthExpiredError(f"获取授权码失败: {data.get('msg') or data}")
        return data["data"]["code"]

    def _get_cred(self, grant_code: str) -> None:
        resp = self._http.post_json(
            URL_GENERATE_CRED,
            json_body={"code": grant_code, "kind": 1},
            headers=self._base_headers(),
        )
        data = resp.raw_json if isinstance(resp.raw_json, dict) else {}
        if data.get("code") != 0:
            raise AuthExpiredError(f"获取 cred 失败: {data.get('message') or data}")
        self._cred = data["data"]["cred"]
        self._cred_token = data["data"]["token"]

    def _ensure_cred(self) -> None:
        """完整登录链路：密码 → token → grant → cred。"""
        token = self._login_by_password()
        code = self._get_grant_code(token)
        self._get_cred(code)

    # ------------------------------------------------------------------
    # 请求签名
    # ------------------------------------------------------------------
    def _sign(self, path: str, body_or_query: str) -> tuple[str, dict]:
        ts = str(int(time.time()) - 2)
        header_ca = {"platform": "3", "timestamp": ts, "dId": self._did, "vName": "1.0.0"}
        s = path + body_or_query + ts + json.dumps(header_ca, separators=(",", ":"))
        mac = hmac.new(self._cred_token.encode(), s.encode(), hashlib.sha256).hexdigest()
        return hashlib.md5(mac.encode()).hexdigest(), header_ca

    def _signed_headers(self, url: str, method: str, body: str = "") -> dict[str, str]:
        parsed = urlparse(url)
        boq = parsed.query if method.upper() == "GET" else body
        sign, ca = self._sign(parsed.path, boq)
        h = self._base_headers()
        h["cred"] = self._cred
        h["sign"] = sign
        h.update({k: str(v) for k, v in ca.items()})
        return h

    # ------------------------------------------------------------------
    # 凭证校验
    # ------------------------------------------------------------------
    def verify_credential(self) -> bool:
        if not (self.phone and self.password):
            return False
        try:
            self._ensure_cred()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # 游戏签到
    # ------------------------------------------------------------------
    def game_signin(self) -> list[CheckinResult]:
        self._ensure_cred()
        # 获取绑定角色列表
        h = self._signed_headers(URL_BINDING_LIST, "GET")
        resp = self._http.get(URL_BINDING_LIST, headers=h)
        data = resp.raw_json if isinstance(resp.raw_json, dict) else {}
        if data.get("code") != 0:
            return [self._fail("game_signin", f"获取绑定列表失败: {data.get('message')}", "")]
        bindings = data.get("data", {}).get("list", [])
        if not bindings:
            return [self._fail("game_signin", "未绑定任何游戏角色", "")]

        results: list[CheckinResult] = []
        for item in bindings:
            app = item.get("appCode", "")
            if app == "arknights":
                for b in item.get("bindingList", []):
                    success, msg, reward = self._sign_arknights(b)
                    nick = b.get("nickName", "")
                    game = f"明日方舟/{nick}" if nick else "明日方舟"
                    results.append(self._result(success, msg, reward, game))
            elif app == "endfield":
                for b in item.get("bindingList", []):
                    for success, msg, reward, nick in self._sign_endfield(b):
                        game = f"终末地/{nick}" if nick else "终末地"
                        results.append(self._result(success, msg, reward, game))
        return results or [self._fail("game_signin", "无可签到的游戏", "")]

    def _result(self, success: bool, msg: str, reward: str, game: str) -> CheckinResult:
        if success:
            return self._ok("game_signin", msg, reward, game=game)
        return self._fail("game_signin", msg, game=game)

    def _sign_arknights(self, binding: dict) -> tuple[bool, str, str]:
        body = json.dumps(
            {"gameId": binding.get("gameId"), "uid": binding.get("uid")},
            separators=(",", ":"),
        )
        h = self._signed_headers(URL_SIGN_ARKNIGHTS, "POST", body)
        resp = self._http.post_json(
            URL_SIGN_ARKNIGHTS,
            json_body={"gameId": binding.get("gameId"), "uid": binding.get("uid")},
            headers=h,
        )
        return self._parse_sign_resp(resp)

    def _sign_endfield(self, binding: dict) -> list[tuple[bool, str, str, str]]:
        results: list[tuple[bool, str, str, str]] = []
        roles = binding.get("roles", [])
        for role in roles:
            h = self._signed_headers(URL_SIGN_ENDFIELD, "POST", "")
            h["Content-Type"] = "application/json"
            h["sk-game-role"] = f"3_{role.get('roleId','')}_{role.get('serverId','')}"
            h["referer"] = "https://game.skland.com/"
            h["origin"] = "https://game.skland.com/"
            resp = self._http.post_json(URL_SIGN_ENDFIELD, json_body=None, headers=h)
            success, msg, reward = self._parse_sign_resp(resp)
            results.append((success, msg, reward, role.get("nickname", "")))
        return results or [(False, "无角色", "", "")]

    def _parse_sign_resp(self, resp: ApiResponse) -> tuple[bool, str, str]:
        data = resp.raw_json if isinstance(resp.raw_json, dict) else {}
        msg = data.get("message", "")
        if data.get("code") == 0:
            awards = data.get("data", {}).get("awards", [])
            names = "+".join(
                f"{a.get('resource',{}).get('name','')}x{a.get('count',1)}" for a in awards
            )
            return True, "签到成功", names
        if _ALREADY_SIGNED_RE.search(msg):
            return True, "今日已签到", ""
        return False, msg or str(data)[:120], ""

    # ------------------------------------------------------------------
    # 结果辅助
    # ------------------------------------------------------------------
    def _ok(self, action: str, message: str, reward: str = "", game: str = "") -> CheckinResult:
        return CheckinResult(self.name, self.account.name, action, CheckinStatus.SUCCESS, message, reward, game, self.display_name)

    def _fail(self, action: str, message: str, game: str = "") -> CheckinResult:
        return CheckinResult(self.name, self.account.name, action, CheckinStatus.FAILED, message, "", game, self.display_name)
