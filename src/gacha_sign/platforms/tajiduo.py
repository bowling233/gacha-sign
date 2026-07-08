"""塔吉多（异环 Neverness to everness）签到平台实现。

凭证：refreshToken（轮换存储到 credentials.json）+ deviceId + uid。
通过 laohu SDK 密码登录获取 token，再通过 usercenter/api/login
换取 accessToken/refreshToken。token 刷新用 usercenter/api/refreshToken。
密码登录需 MD5 签名 + AES-128-ECB 字段加密；日常签到仅用 authorization 头。

主要参考：reference/astrbot_plugin_nte/nte.py（端点、常量、签名、加密算法）。
"""

from __future__ import annotations

import re
import time
from typing import Any

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
# 常量（来自 reference/astrbot_plugin_nte/nte.py）
# ---------------------------------------------------------------------------
APP_ID = "10550"                           # laohu SDK appId
USER_CENTER_APP_ID = "10551"               # 塔吉多用户中心 appId
SECRET = "89155cc4e8634ec5b1b6364013b23e3e"  # laohu 签名 secret & AES key 源
DEFAULT_GAME_ID = "1289"                    # 异环默认 gameId

# 设备伪装参数
DEVICETYPE = "LGE-AN10"
DEVICENAME = "LGE-AN10"
DEVICEMODEL = "LGE-AN10"
DEVICESYS = "12"
VERSIONCODE = "1"
AREACODEID = "1"
TYPE = "16"
SDKVERSION = "4.129.0"
BID = "com.pwrd.htassistant"
CHANNELID = "1"
# usercenter/login + refreshToken 对 appversion 校验严格，当前可用值是 1.1.0
APPVERSION = "1.1.0"
OKHTTP_UA = "okhttp/4.12.0"

# 接口
LAOHU_BASE = "https://user.laohu.com"
TAJIDUO_BASE = "https://bbs-api.tajiduo.com"
URL_PASSWORD_LOGIN = f"{LAOHU_BASE}/m/newApi/login"
URL_USER_CENTER_LOGIN = f"{TAJIDUO_BASE}/usercenter/api/login"
URL_REFRESH_TOKEN = f"{TAJIDUO_BASE}/usercenter/api/refreshToken"
URL_GET_GAME_ROLES = f"{TAJIDUO_BASE}/usercenter/api/v2/getGameRoles"
URL_GAME_SIGNIN = f"{TAJIDUO_BASE}/apihub/awapi/sign"
URL_GAME_SIGN_STATE = f"{TAJIDUO_BASE}/apihub/awapi/signin/state"
URL_GAME_SIGN_REWARDS = f"{TAJIDUO_BASE}/apihub/awapi/sign/rewards"

# 登录请求基础头
LAOHU_HEADERS = {"platform": "android", "Content-Type": "application/x-www-form-urlencoded"}

# 异常签到提示（视为已签到）
_ALREADY_SIGNED_RE = re.compile(r"已.*签到|签到.*过|重复签到|already.*sign", re.IGNORECASE)


class TajiduoPlatform(PlatformBase):
    """塔吉多签到（针对异环）。"""

    name = "tajiduo"

    def __init__(self, account: Account, http: HttpClient, options=None):
        super().__init__(account, http, options)
        # 用户配置（config.yaml）
        self.phone: str = self.account.get("phone", "") or ""
        self.password: str = self.account.get("password", "") or ""
        self.game_id: str = str(self.account.get("game_id", "") or DEFAULT_GAME_ID)
        # 运行时凭据（credentials.json）
        self.refresh_token: str = self.account.cred_get("refresh_token", "") or ""
        self.device_id: str = self.account.cred_get("device_id", "") or ""
        self.uid: str = str(self.account.cred_get("uid", "") or "")
        self.role_ids: list[str] = list(self.account.cred_get("role_ids", []) or [])
        self._access_token: str = ""

    # ---- 工具 ----
    def _ensure_device_id(self) -> None:
        if not self.device_id:
            self.device_id = crypto.random_device_id()
            self.account.cred_set("device_id", self.device_id)

    def _native_headers(self, access_token: str = "") -> dict[str, str]:
        """塔吉多原生鉴权请求头。"""
        return {
            "platform": "android",
            "Content-Type": "application/x-www-form-urlencoded",
            "authorization": access_token or self._access_token,
            "uid": self.uid or "10000000",
            "deviceid": self.device_id,
            "appversion": APPVERSION,
            "User-Agent": OKHTTP_UA,
        }

    @staticmethod
    def _is_ok(resp: ApiResponse) -> bool:
        return resp.code == 0 or (resp.code is None and resp.is_http_ok)

    @staticmethod
    def _is_ok_dict(d: dict) -> bool:
        return d.get("code") == 0 or d.get("ok") is True

    @staticmethod
    def _is_already_signed(msg: str) -> bool:
        return bool(_ALREADY_SIGNED_RE.search(msg or ""))

    # ---- token 刷新 ----
    def _refresh_access_token(self) -> str:
        """用 refreshToken 刷新 accessToken，并轮换持久化 refreshToken。"""
        self._ensure_device_id()
        headers = {
            "platform": "android",
            "Content-Type": "application/x-www-form-urlencoded",
            "authorization": self.refresh_token,
            "deviceid": self.device_id,
            "appversion": APPVERSION,
            "uid": "10000000",
            "User-Agent": OKHTTP_UA,
        }
        resp = self._http.post_form(URL_REFRESH_TOKEN, headers=headers)
        if resp.status_code == 402:
            raise AuthExpiredError("refreshToken 已失效，请重新登录")
        if not self._is_ok(resp):
            raise AuthExpiredError(f"刷新token失败：{resp.message or resp.text[:120]}")
        data = resp.raw_json.get("data") if isinstance(resp.raw_json, dict) else None
        if not isinstance(data, dict):
            raise AuthExpiredError("刷新token返回缺少data")
        access_token = data.get("accessToken")
        new_refresh = data.get("refreshToken")
        if not access_token or not new_refresh:
            raise AuthExpiredError("刷新token返回缺少 accessToken/refreshToken")
        self._access_token = access_token
        # 轮换并持久化到 credentials.json
        self.refresh_token = new_refresh
        self.account.cred_set("refresh_token", new_refresh)
        if data.get("uid"):
            self.uid = str(data["uid"])
            self.account.cred_set("uid", self.uid)
        return access_token

    # ---- 凭证校验 ----
    def verify_credential(self) -> bool:
        # 有 refreshToken → 直接刷新验证
        if self.refresh_token:
            self._ensure_device_id()
            try:
                self._refresh_access_token()
                return True
            except AuthExpiredError:
                self.refresh_token = ""  # 失效，尝试重新登录
        # 无 refreshToken 或已失效 → 尝试密码登录
        if self.phone and self.password:
            try:
                self.login_by_password(self.phone, self.password)
                return True
            except Exception:  # noqa: BLE001
                return False
        return False

    # ---- 角色解析 ----
    def _get_role_ids(self) -> list[str]:
        """获取异环角色 ID 列表。"""
        resp = self._http.get(
            URL_GET_GAME_ROLES,
            headers=self._native_headers(),
            params={"gameId": self.game_id},
        )
        if not self._is_ok(resp):
            return []
        data = resp.raw_json.get("data") if isinstance(resp.raw_json, dict) else None
        roles = data.get("roles", []) if isinstance(data, dict) else []
        ids = [str(r.get("roleId")) for r in roles if r.get("roleId")]
        if ids:
            self.role_ids = ids
            self.account.cred_set("role_ids", ids)
        return ids

    def _ensure_role_ids(self) -> list[str]:
        if self.role_ids:
            return self.role_ids
        return self._get_role_ids()

    # ---- 游戏签到 ----
    def game_signin(self) -> CheckinResult:
        role_ids = self._ensure_role_ids()
        if not role_ids:
            return self._fail("game_signin", "未找到异环角色")
        candidates = self._candidate_game_ids()
        headers = {
            "platform": "android",
            "Content-Type": "application/x-www-form-urlencoded",
            "authorization": self._access_token,
            "appversion": APPVERSION,
            "User-Agent": OKHTTP_UA,
        }
        msgs: list[str] = []
        overall_ok = False
        for role_id in role_ids:
            ok, msg = self._sign_one_role(role_id, candidates, headers)
            if ok:
                overall_ok = True
            msgs.append(f"角色{role_id}: {msg}")
        if overall_ok:
            return self._ok("game_signin", "；".join(msgs))
        return self._fail("game_signin", "；".join(msgs))

    def _sign_one_role(self, role_id: str, candidates: list[str], headers: dict) -> tuple[bool, str]:
        """对单个角色尝试签到（多 gameId 候选回退）。"""
        errors: list[str] = []
        for gid in candidates:
            resp = self._http.post_form(
                URL_GAME_SIGNIN, data={"roleId": role_id, "gameId": gid}, headers=headers
            )
            if self._is_ok(resp):
                reward = self._today_reward(role_id, gid)
                return True, f"签到成功(gameId={gid})" + (f"，今日道具:{reward}" if reward else "")
            msg = resp.message or str(resp.raw_json)[:120]
            if self._is_already_signed(msg):
                state = self._sign_state(gid)
                reward = ""
                if state and self._today_reward_from_state(role_id, gid, state):
                    reward = self._today_reward_from_state(role_id, gid, state)
                if state and state.get("todaySign"):
                    return True, f"今日已签到(gameId={gid})" + (f"，今日道具:{reward}" if reward else "")
                errors.append(f"gameId={gid} 提示已签到但状态未签")
                continue
            errors.append(f"gameId={gid}: {msg}")
        return False, "；".join(errors)

    def _candidate_game_ids(self) -> list[str]:
        """候选 gameId 列表（去重）。"""
        seen: list[str] = []
        for gid in [self.game_id, DEFAULT_GAME_ID, "1289", "1257"]:
            g = str(gid).strip()
            if g and g not in seen:
                seen.append(g)
        return seen

    def _sign_state(self, game_id: str) -> dict:
        try:
            resp = self._http.get(
                URL_GAME_SIGN_STATE, headers={"Authorization": self._access_token},
                params={"gameId": game_id},
            )
            if self._is_ok(resp):
                data = resp.raw_json.get("data") if isinstance(resp.raw_json, dict) else {}
                return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            pass
        return {}

    def _today_reward(self, role_id: str, game_id: str) -> str:
        state = self._sign_state(game_id)
        return self._today_reward_from_state(role_id, game_id, state)

    def _today_reward_from_state(self, role_id: str, game_id: str, state: dict) -> str:
        try:
            days = int(state.get("days", 0))
            if days <= 0:
                return ""
            params = {"gameId": game_id}
            if role_id:
                params["roleId"] = role_id
            resp = self._http.get(URL_GAME_SIGN_REWARDS, headers={"Authorization": self._access_token}, params=params)
            if self._is_ok(resp):
                data = resp.raw_json.get("data") if isinstance(resp.raw_json, dict) else None
                items = data if isinstance(data, list) else (
                    data.get("items") or data.get("rewards") or data.get("list")
                    if isinstance(data, dict) else []
                )
                if isinstance(items, list) and 0 < days <= len(items):
                    item = items[days - 1] if items else {}
                    name = item.get("name") or item.get("itemName") or ""
                    return str(name)
        except Exception:  # noqa: BLE001
            pass
        return ""

    # ---- 密码登录（verify_credential 中自动调用）----
    def _laohu_sign(self, params: dict[str, Any]) -> str:
        """laohu 请求签名：按 key 排序拼接 value + secret 的 MD5。"""
        sorted_keys = sorted(params.keys())
        values = "".join(str(params[k]) for k in sorted_keys)
        return crypto.md5_hex(values + SECRET)

    def _user_center_login(self, laohu_token: str, user_id: str) -> None:
        """用 laohu token 换取塔吉多 accessToken/refreshToken 并持久化。"""
        headers = {
            "platform": "android", "Content-Type": "application/x-www-form-urlencoded",
            "deviceid": self.device_id, "authorization": "", "appversion": APPVERSION,
            "uid": "10000000", "User-Agent": OKHTTP_UA,
        }
        data = {"token": laohu_token, "userIdentity": str(user_id), "appId": USER_CENTER_APP_ID}
        resp = self._http.post_form(URL_USER_CENTER_LOGIN, data=data, headers=headers)
        if not self._is_ok(resp):
            raise RuntimeError(f"用户中心登录失败：{resp.message or resp.text[:120]}")
        d = resp.raw_json.get("data") if isinstance(resp.raw_json, dict) else {}
        access_token = d.get("accessToken")
        refresh_token = d.get("refreshToken")
        if not access_token or not refresh_token:
            raise RuntimeError("用户中心登录返回缺少 accessToken/refreshToken")
        self._access_token = access_token
        self.refresh_token = refresh_token
        self.account.cred_set("refresh_token", refresh_token)
        if d.get("uid"):
            self.uid = str(d["uid"])
            self.account.cred_set("uid", self.uid)

    def _password_login_raw(self, phone: str, password: str, encrypt: bool) -> dict:
        """密码登录单次尝试（明文或加密）。"""
        username = crypto.aes_ecb_base64(phone, SECRET) if encrypt else phone
        pwd = crypto.aes_ecb_base64(password, SECRET) if encrypt else password
        data = {
            "deviceType": DEVICETYPE, "type": TYPE,
            "deviceId": self.device_id, "deviceName": DEVICENAME,
            "versionCode": VERSIONCODE, "t": str(int(time.time())),
            "areaCodeId": AREACODEID, "appId": APP_ID, "deviceSys": DEVICESYS,
            "username": username, "password": pwd,
            "deviceModel": DEVICEMODEL, "sdkVersion": SDKVERSION,
            "bid": BID, "channelId": CHANNELID,
        }
        data["sign"] = self._laohu_sign(data)
        resp = self._http.post_form(URL_PASSWORD_LOGIN, data=data, headers=LAOHU_HEADERS)
        return resp.raw_json if isinstance(resp.raw_json, dict) else {}

    def login_by_password(self, phone: str, password: str) -> None:
        """账号密码登录。先明文试，BAD_REQUEST 则 AES 加密重试。"""
        self._ensure_device_id()
        resp = self._password_login_raw(phone, password, encrypt=False)
        if not self._is_ok_dict(resp):
            msg = str(resp.get("message") or resp.get("msg") or "")
            if "BAD_REQUEST" in msg:
                resp = self._password_login_raw(phone, password, encrypt=True)
            if not self._is_ok_dict(resp):
                raise RuntimeError(f"密码登录失败：{resp.get('message') or resp.get('msg') or resp}")
        result = resp.get("result") or {}
        token = result.get("token")
        user_id = result.get("userId")
        if not token or user_id is None:
            raise RuntimeError("密码登录返回缺少 token/userId")
        self._user_center_login(token, str(user_id))

    # ---- 结果构造辅助 ----
    def _ok(self, action: str, message: str, reward: str = "", status: CheckinStatus = CheckinStatus.SUCCESS) -> CheckinResult:
        return CheckinResult(self.name, self.account.name, action, status, message, reward)

    def _fail(self, action: str, message: str) -> CheckinResult:
        return CheckinResult(self.name, self.account.name, action, CheckinStatus.FAILED, message)
