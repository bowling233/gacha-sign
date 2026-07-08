"""米游社（绝区零 ZZZ）游戏签到平台实现。

凭证：cookie（从米游社 APP 抓包获取，需含 cookie_token_v2 / ltoken_v2 等 v2 字段）。
游戏签到通过 act.mihoyo.com 的 WebView 接口完成，不需要 DS 签名。

header 格式基于米游社 APP v2.110.0 的真实抓包。
"""

from __future__ import annotations

import uuid

from ..base import (
    Account,
    AuthExpiredError,
    CaptchaNeededError,
    CheckinResult,
    CheckinStatus,
    PlatformBase,
)
from ..http import HttpClient

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
MIHYOYO_VERSION = "2.110.0"
CLIENT_TYPE_WEB = "5"

ZZZ_GAME_BIZ = "nap_cn"
ZZZ_ACT_ID = "e202406242138391"

WEB_API = "https://api-takumi.mihoyo.com"
ZZZ_WEB_API = "https://act-nap-api.mihoyo.com"

URL_GAME_ROLES_BY_COOKIE = f"{WEB_API}/binding/api/getUserGameRolesByCookie"
URL_ZZZ_REWARDS = f"{ZZZ_WEB_API}/event/luna/zzz/home?lang=zh-cn"
URL_ZZZ_INFO = f"{ZZZ_WEB_API}/event/luna/zzz/info?lang=zh-cn"
URL_ZZZ_SIGN = f"{ZZZ_WEB_API}/event/luna/zzz/sign"


class MihoyoPlatform(PlatformBase):
    """米游社游戏签到（绝区零 ZZZ）。"""

    name = "mihoyo"

    def __init__(self, account: Account, http: HttpClient, options=None):
        super().__init__(account, http, options)
        # 用户配置（config.yaml）
        self.cookie: str = self.account.get("cookie", "") or ""
        self.games: list[str] = list(self.account.get("games", []) or ["zzz"])
        # 运行时凭据（credentials.json）
        self.device_id: str = self.account.cred_get("device_id", "") or ""

    # ---- header ----
    def _ensure_device_id(self) -> None:
        if not self.device_id:
            self.device_id = str(uuid.uuid4())
            self.account.cred_set("device_id", self.device_id)

    def _game_headers(self) -> dict[str, str]:
        """游戏签到请求头（WebView 风格，模拟 APP 内嵌 WebView）。

        抓包确认：游戏签到页面是 act.mihoyo.com 的内嵌 WebView，
        用 web UA + Origin + X-Requested-With，不需要 DS 签名。
        """
        return {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://act.mihoyo.com",
            "Referer": "https://act.mihoyo.com/",
            "x-rpc-app_version": MIHYOYO_VERSION,
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 12; V2314A Build/W528JS; wv) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
                "Chrome/103.0.5060.129 Mobile Safari/537.36"
            ),
            "x-rpc-client_type": CLIENT_TYPE_WEB,
            "Cookie": self.cookie,
            "x-rpc-device_id": self.device_id,
            "X-Requested-With": "com.mihoyo.hyperion",
            "x-rpc-signgame": "zzz",
        }

    # ---- 凭证校验 ----
    def verify_credential(self) -> bool:
        if not self.cookie:
            return False
        self._ensure_device_id()
        # 用 getUserGameRolesByCookie 验证（返回角色列表即有效）
        resp = self._http.get(
            URL_GAME_ROLES_BY_COOKIE, headers=self._game_headers(),
            params={"game_biz": ZZZ_GAME_BIZ},
        )
        return resp.code == 0

    # ---- 游戏签到 ----
    def game_signin(self) -> CheckinResult:
        if "zzz" not in self.games:
            return self._skip("game_signin", "未配置绝区零游戏签到")
        role = self._get_zzz_role()
        if not role:
            return self._fail("game_signin", "未找到绑定的绝区零角色")
        uid, region = role
        headers = self._game_headers()
        # 查询是否已签到
        info = self._http.get(
            URL_ZZZ_INFO, headers=headers,
            params={"act_id": ZZZ_ACT_ID, "region": region, "uid": uid, "lang": "zh-cn"},
        )
        reward_text = self._today_reward(info)
        if info.code == 0:
            data = info.data or {}
            if isinstance(data, dict) and data.get("is_sign"):
                return self._ok("game_signin", "今日已签到", reward_text, CheckinStatus.ALREADY_SIGNED)
        # 执行签到
        resp = self._http.post_json(
            URL_ZZZ_SIGN, headers=headers,
            json_body={"act_id": ZZZ_ACT_ID, "region": region, "uid": uid, "lang": "zh-cn"},
        )
        code = resp.code
        if code == 0:
            data = resp.data or {}
            if isinstance(data, dict) and data.get("success") == 1:
                raise CaptchaNeededError("游戏签到触发验证码(success=1)")
            return self._ok("game_signin", "游戏签到成功", reward_text)
        if code == -5003:
            return self._ok("game_signin", "今日已签到", reward_text, CheckinStatus.ALREADY_SIGNED)
        if code == -100:
            raise AuthExpiredError("游戏签到凭证失效(retcode=-100)")
        return self._fail("game_signin", f"游戏签到失败 retcode={code} {resp.message}")

    def _get_zzz_role(self) -> tuple[str, str] | None:
        """通过 cookie 获取绑定的 ZZZ 游戏角色 (uid, region)。"""
        resp = self._http.get(
            URL_GAME_ROLES_BY_COOKIE, headers=self._game_headers(),
            params={"game_biz": ZZZ_GAME_BIZ},
        )
        if resp.code == 0 and isinstance(resp.data, dict):
            roles = resp.data.get("list", [])
        elif resp.code == 0 and isinstance(resp.data, list):
            roles = resp.data
        else:
            return None
        for role in roles:
            if role.get("game_biz") == ZZZ_GAME_BIZ or "game_uid" in role:
                return str(role.get("game_uid", "")), str(role.get("region", ""))
        return None

    def _today_reward(self, info_resp) -> str:
        """从 info 响应推断今日奖励。"""
        try:
            data = info_resp.data or {}
            day = int(data.get("total_sign_day", 0)) if isinstance(data, dict) else 0
            if day <= 0:
                return ""
            rewards = self._http.get(
                URL_ZZZ_REWARDS, headers=self._game_headers(),
                params={"act_id": ZZZ_ACT_ID, "lang": "zh-cn"},
            )
            if rewards.code == 0 and isinstance(rewards.data, dict):
                award_list = rewards.data.get("awards") or []
                if 0 < day <= len(award_list):
                    a = award_list[day - 1]
                    return f"「{a.get('name')}」x{a.get('cnt')}"
        except Exception:  # noqa: BLE001
            pass
        return ""

    # ---- 结果构造辅助 ----
    def _ok(self, action: str, message: str, reward: str = "", status: CheckinStatus = CheckinStatus.SUCCESS) -> CheckinResult:
        return CheckinResult(self.name, self.account.name, action, status, message, reward)

    def _fail(self, action: str, message: str) -> CheckinResult:
        return CheckinResult(self.name, self.account.name, action, CheckinStatus.FAILED, message)

    def _skip(self, action: str, message: str) -> CheckinResult:
        return CheckinResult(self.name, self.account.name, action, CheckinStatus.SKIPPED, message)
