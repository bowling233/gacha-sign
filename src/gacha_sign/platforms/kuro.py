"""库街区（鸣潮 Wuthering Waves）游戏签到平台实现。

凭证：token（从库街区 APP 抓包获取的 JWT），不与设备绑定。
游戏签到用 WebView 风格 header（Origin=web-static.kurobbs.com + devCode），
否则被 WAF 拦截返回 code=102。角色查询用 okhttp 风格 header。

鸣潮 gameId=3、serverId=76402e5b20be2c39f095a152090afddc。
header 格式基于 MuMu 模拟器中库街区 APP v3.1.3 的真实抓包。
"""

from __future__ import annotations

from datetime import datetime

from ..base import (
    Account,
    AuthExpiredError,
    CheckinResult,
    CheckinStatus,
    PlatformBase,
)
from ..http import HttpClient

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
API_BASE = "https://api.kurobbs.com"

WUWA_GAME_ID = "3"
WUWA_SERVER_ID = "76402e5b20be2c39f095a152090afddc"

CODE_SUCCESS = 200
CODE_ALREADY_SIGNED = 1511
CODE_USER_INFO_ERROR = 1513
CODE_LOGIN_EXPIRED = 220

URL_USER_MINE = f"{API_BASE}/user/mineV2"
URL_ROLE_LIST = f"{API_BASE}/user/role/findRoleList"
URL_GAME_SIGN = f"{API_BASE}/encourage/signIn/v2"
URL_GAME_SIGN_INIT = f"{API_BASE}/encourage/signIn/initSignInV2"
URL_GAME_REPLENISH = f"{API_BASE}/encourage/signIn/repleSigInV2"
URL_GAME_SIGN_RECORD = f"{API_BASE}/encourage/signIn/queryRecordV2"

WEBVIEW_UA = (
    "Mozilla/5.0 (Linux; Android 12; V2314A Build/W528JS; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/103.0.5060.129 Mobile Safari/537.36"
)


class KuroPlatform(PlatformBase):
    """库街区游戏签到（鸣潮）。

    token 通过 APP 抓包获取（ADB + mitmproxy），不与设备绑定。
    基础接口用 okhttp header，游戏签到用 WebView header。
    """

    name = "kuro"

    def __init__(self, account: Account, http: HttpClient, options=None):
        super().__init__(account, http, options)
        # 用户配置（config.yaml）
        self.token: str = self.account.get("token", "") or ""
        self.auto_replenish: bool = bool(self.account.get("auto_replenish", True))
        # 运行时凭据（credentials.json）
        self.role_id: str = self.account.cred_get("role_id", "") or ""
        self.user_id: str = self.account.cred_get("user_id", "") or ""

    # ---- header ----
    def _okhttp_headers(self) -> dict[str, str]:
        """okhttp 风格请求头（source=android），用于基础接口。"""
        return {
            "source": "android",
            "version": "3.1.3",
            "token": self.token,
            "Cookie": f"user_token={self.token}",
            "Content-Type": "application/x-www-form-urlencoded",
            "user-agent": "okhttp/3.11.0",
        }

    def _webview_headers(self) -> dict[str, str]:
        """WebView 风格请求头，用于游戏签到接口（WAF 校验必需）。"""
        return {
            "source": "android",
            "version": "3.1.3",
            "token": self.token,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://web-static.kurobbs.com",
            "Referer": "https://web-static.kurobbs.com/",
            "X-Requested-With": "com.kurogame.kjq",
            "User-Agent": WEBVIEW_UA,
            "devCode": f"0.0.0.0, {WEBVIEW_UA} KuroGameBox/3.1.3",
            "Accept": "application/json, text/plain, */*",
        }

    # ---- 凭证校验 ----
    def verify_credential(self) -> bool:
        if not self.token:
            return False
        data = {"viewUserId": self.user_id} if self.user_id else {"type": 1}
        resp = self._http.post_form(URL_USER_MINE, data=data, headers=self._okhttp_headers())
        if resp.code == CODE_SUCCESS and isinstance(resp.data, dict):
            mine = resp.data.get("mine", resp.data)
            uid = mine.get("userId")
            if uid:
                self.user_id = str(uid)
                self.account.cred_set("user_id", self.user_id)
            return True
        if resp.code == CODE_LOGIN_EXPIRED:
            raise AuthExpiredError("库街区 token 已过期，请重新抓包获取")
        return False

    def _ensure_role(self) -> bool:
        """获取鸣潮角色 ID（首次运行自动回填）。"""
        if self.role_id:
            return True
        resp = self._http.post_form(
            URL_ROLE_LIST, data={"gameId": WUWA_GAME_ID}, headers=self._okhttp_headers()
        )
        if resp.code == CODE_SUCCESS and isinstance(resp.data, list) and resp.data:
            self.role_id = str(resp.data[0].get("roleId", ""))
            if self.role_id:
                self.account.cred_set("role_id", self.role_id)
                return True
        return False

    # ---- 游戏签到 ----
    def game_signin(self) -> CheckinResult:
        if not self._ensure_role():
            return self._fail("game_signin", "未找到鸣潮角色")
        headers = self._webview_headers()
        data = {
            "gameId": WUWA_GAME_ID,
            "serverId": WUWA_SERVER_ID,
            "roleId": self.role_id,
            "userId": self.user_id,
            "reqMonth": datetime.now().strftime("%m"),
        }
        resp = self._http.post_form(URL_GAME_SIGN, data=data, headers=headers)
        code = resp.code
        if code == CODE_SUCCESS:
            reward = self._query_reward(headers)
            msg = "鸣潮游戏签到成功"
            if reward:
                msg += f"，奖励:{reward}"
            result = self._ok("game_signin", msg, reward)
            if self.auto_replenish:
                self._try_replenish(data, headers)
            return result
        if code == CODE_ALREADY_SIGNED:
            reward = self._query_reward(headers)
            return self._ok("game_signin", "鸣潮今日已签到", reward, CheckinStatus.ALREADY_SIGNED)
        if code == CODE_LOGIN_EXPIRED:
            raise AuthExpiredError("游戏签到失败：token 已过期")
        if code == CODE_USER_INFO_ERROR:
            return self._fail("game_signin", "游戏签到失败：用户信息异常(code=1513)")
        return self._fail("game_signin", f"游戏签到失败 code={code} {resp.message}")

    def _query_reward(self, headers: dict) -> str:
        """查询今日签到奖励名。"""
        data = {
            "gameId": WUWA_GAME_ID,
            "serverId": WUWA_SERVER_ID,
            "roleId": self.role_id,
            "userId": self.user_id,
        }
        resp = self._http.post_form(URL_GAME_SIGN_RECORD, data=data, headers=headers)
        if resp.code == CODE_SUCCESS and isinstance(resp.data, list) and resp.data:
            name = resp.data[0].get("goodsName") if isinstance(resp.data[0], dict) else ""
            return str(name or "")
        return ""

    def _try_replenish(self, data: dict, headers: dict) -> None:
        """尝试补签漏签的天数。"""
        try:
            init = self._http.post_form(URL_GAME_SIGN_INIT, data=data, headers=headers)
            if init.code == CODE_SUCCESS and isinstance(init.data, dict):
                omission = int(init.data.get("omissionNnm", 0))
                if omission > 0:
                    self._http.post_form(URL_GAME_REPLENISH, data=data, headers=headers)
        except Exception:  # noqa: BLE001
            pass

    # ---- 结果构造辅助 ----
    def _ok(self, action: str, message: str, reward: str = "", status: CheckinStatus = CheckinStatus.SUCCESS) -> CheckinResult:
        return CheckinResult(self.name, self.account.name, action, status, message, reward)

    def _fail(self, action: str, message: str) -> CheckinResult:
        return CheckinResult(self.name, self.account.name, action, CheckinStatus.FAILED, message)
