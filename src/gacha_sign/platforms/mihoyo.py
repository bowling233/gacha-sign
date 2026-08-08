"""米游社（绝区零 / 原神等）签到平台实现。

凭证：cookie（从米游社 APP 抓包获取，需含 cookie_token_v2 / ltoken_v2 等 v2 字段）。

三种签到动作：
- game_checkin: 游戏每日签到（领游戏内奖励），通过 act.mihoyo.com 的 luna 接口
- bbs_checkin: 社区签到（领米游币），通过 bbs-api.miyoushe.com，需 DS 签名
- bbs_tasks: 社区任务（浏览/点赞/分享帖子，领米游币）

参考：MihoyoBBSTools（DS 签名算法、act_id、社区任务接口）。
"""

from __future__ import annotations

import hashlib
import json
import random
import string
import time
import uuid

from ..base import (
    Account,
    AuthExpiredError,
    CaptchaNeededError,
    CheckinResult,
    CheckinStatus,
    PlatformBase,
)
from ..http import HttpClient, ApiResponse

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
MIHYOYO_VERSION = "2.109.0"
CLIENT_TYPE_APP = "2"
CLIENT_TYPE_WEB = "5"

WEB_API = "https://api-takumi.mihoyo.com"
BBS_API = "https://bbs-api.miyoushe.com"

# DS 签名 salts（来自 MihoyoBBSTools）
BBS_SALT = "47f15f1b66bee46b816115d8e8e6ebb6"
BBS_SALT_X6 = "t0qEgfub6cvueAPgR5m9aQWWVciEer7v"

# 社区接口
URL_BBS_TASKS_LIST = f"{BBS_API}/apihub/wapi/getUserMissionsState"
URL_BBS_SIGNIN = f"{BBS_API}/apihub/app/api/signIn"
URL_BBS_POST_LIST = f"{BBS_API}/post/api/getForumPostList"
URL_BBS_POST_DETAIL = f"{BBS_API}/post/api/getPostFull"
URL_BBS_LIKE = f"{BBS_API}/apihub/sapi/upvotePost"
URL_BBS_SHARE = f"{BBS_API}/apihub/api/getShareConf"

# 社区签到 gids 和帖子列表 forumId（已验证：zzz + genshin）
FORUM_GIDS: dict[str, str] = {"zzz": "8", "genshin": "2"}
FORUM_PAGE_IDS: dict[str, str] = {"zzz": "57", "genshin": "26"}

# 游戏签到配置
_GAMES: dict[str, dict] = {
    "zzz": {
        "biz": "nap_cn", "act_id": "e202406242138391", "name": "绝区零",
        "api_base": "https://act-nap-api.mihoyo.com", "api_prefix": "/event/luna/zzz",
        "signgame": "zzz",
    },
    "genshin": {
        "biz": "hk4e_cn", "act_id": "e202311201442471", "name": "原神",
        "api_base": WEB_API, "api_prefix": "/event/luna/hk4e",
        "signgame": "hk4e",
    },
}

GAME_ROLES_URL = f"{WEB_API}/binding/api/getUserGameRolesByCookie"


# ---------------------------------------------------------------------------
# DS 签名
# ---------------------------------------------------------------------------
def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _make_ds(salt: str, body: str = "", query: str = "") -> str:
    """生成米游社 DS2 签名（用于社区签到，含 body/query）。"""
    t = str(int(time.time()))
    r = str(random.randint(100001, 200000))
    c = _md5(f"salt={salt}&t={t}&r={r}&b={body}&q={query}")
    return f"{t},{r},{c}"


def _make_ds_simple(salt: str) -> str:
    """生成米游社 DS 签名（用于社区任务，不含 body/query）。"""
    t = str(int(time.time()))
    r = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    c = _md5(f"salt={salt}&t={t}&r={r}")
    return f"{t},{r},{c}"


class MihoyoPlatform(PlatformBase):
    """米游社签到（游戏签到 + 社区签到 + 社区任务）。"""

    name = "mihoyo"
    display_name = "米游社"

    def __init__(self, account: Account, http: HttpClient, options=None):
        super().__init__(account, http, options)
        self.cookie: str = self.account.get("cookie", "") or ""
        self.stoken: str = self.account.get("stoken", "") or ""
        self.games: list[str] = list(self.account.get("games", []) or ["zzz"])
        self.device_id: str = self.account.cred_get("device_id", "") or ""

    def _ensure_device_id(self) -> None:
        if not self.device_id:
            self.device_id = str(uuid.uuid4())
            self.account.cred_set("device_id", self.device_id)

    # ---- 游戏签到 headers ----
    def _game_headers(self, signgame: str = "") -> dict[str, str]:
        h = {
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
            "DS": _make_ds_simple(BBS_SALT),
        }
        if signgame:
            h["x-rpc-signgame"] = signgame
        return h

    # ---- BBS headers ----
    def _bbs_cookie(self) -> str:
        """构建社区 API 所需的 stoken cookie。"""
        if not self.stoken:
            return self.cookie  # fallback to full cookie
        # 从 cookie 中提取 account_id 和 mid
        aid = ""
        mid = ""
        for field in self.cookie.split(";"):
            f = field.strip()
            if f.startswith("account_id="):
                aid = f.split("=", 1)[1]
            elif f.startswith("account_mid_v2="):
                mid = f.split("=", 1)[1]
        return f"stuid={aid};stoken={self.stoken};mid={mid}"

    def _bbs_headers(self) -> dict[str, str]:
        """社区接口请求头（okhttp 风格 + DS 签名 + stoken cookie）。
        用于浏览/点赞/分享等操作。"""
        return {
            "DS": _make_ds_simple(BBS_SALT),
            "cookie": self._bbs_cookie(),
            "x-rpc-client_type": CLIENT_TYPE_APP,
            "x-rpc-app_version": MIHYOYO_VERSION,
            "x-rpc-device_id": self.device_id,
            "Referer": "https://app.mihoyo.com",
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": "okhttp/4.9.3",
        }

    def _bbs_task_headers(self) -> dict[str, str]:
        """社区任务查询头（WebView 风格 + 完整 cookie，无 DS）。
        用于查询任务状态。"""
        return {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://webstatic.mihoyo.com",
            "Referer": "https://webstatic.mihoyo.com",
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 12; Unspecified Device) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
                f"Chrome/103.0.5060.129 Mobile Safari/537.36 miHoYoBBS/{MIHYOYO_VERSION}"
            ),
            "X-Requested-With": "com.mihoyo.hyperion",
            "Cookie": self.cookie,
        }

    # ---- 凭证校验 ----
    def verify_credential(self) -> bool:
        if not self.cookie:
            return False
        self._ensure_device_id()
        resp = self._http.get(
            GAME_ROLES_URL, headers=self._game_headers(),
            params={"game_biz": _GAMES.get(self.games[0], _GAMES["zzz"])["biz"]},
        )
        return resp.code == 0

    # ---- 游戏签到 ----
    def game_signin(self) -> list[CheckinResult]:
        self._ensure_device_id()
        results: list[CheckinResult] = []
        for game_key in self.games:
            cfg = _GAMES.get(game_key)
            if not cfg:
                continue
            results.append(self._game_checkin_one(cfg))
        return results or [self._fail("game_signin", "未配置任何游戏", "")]

    def _game_checkin_one(self, cfg: dict) -> CheckinResult:
        game_name = cfg["name"]
        # 获取角色
        role = self._get_role(cfg["biz"])
        if not role:
            return self._fail("game_signin", f"未找到{game_name}角色", game_name)
        uid, region = role
        headers = self._game_headers(cfg.get("signgame", ""))
        base = cfg["api_base"]
        prefix = cfg["api_prefix"]
        act_id = cfg["act_id"]
        # 查询签到状态
        info = self._http.get(
            f"{base}{prefix}/info", headers=headers,
            params={"act_id": act_id, "region": region, "uid": uid, "lang": "zh-cn"},
        )
        reward = self._query_reward(base, prefix, act_id, headers, info)
        if info.code == 0 and isinstance(info.data, dict) and info.data.get("is_sign"):
            return self._ok("game_signin", "今日已签到", reward, CheckinStatus.ALREADY_SIGNED, game_name)
        # 执行签到
        resp = self._http.post_json(
            f"{base}{prefix}/sign", headers=headers,
            json_body={"act_id": act_id, "region": region, "uid": uid, "lang": "zh-cn"},
        )
        if resp.code == 0:
            data = resp.data or {}
            if isinstance(data, dict) and data.get("success") == 1:
                raise CaptchaNeededError(f"{game_name}签到触发验证码")
            return self._ok("game_signin", "游戏签到成功", reward, game=game_name)
        if resp.code == -5003:
            return self._ok("game_signin", "今日已签到", reward, CheckinStatus.ALREADY_SIGNED, game_name)
        if resp.code == -100:
            raise AuthExpiredError(f"{game_name}签到凭证失效(retcode=-100)")
        return self._fail("game_signin", f"签到失败 retcode={resp.code} {resp.message}", game_name)

    def _get_role(self, game_biz: str) -> tuple[str, str] | None:
        resp = self._http.get(
            GAME_ROLES_URL, headers=self._game_headers(),
            params={"game_biz": game_biz},
        )
        if resp.code != 0:
            return None
        roles = resp.data.get("list", []) if isinstance(resp.data, dict) else (resp.data or [])
        for r in roles:
            if r.get("game_biz") == game_biz or "game_uid" in r:
                return str(r.get("game_uid", "")), str(r.get("region", ""))
        return None

    def _query_reward(self, base: str, prefix: str, act_id: str, headers: dict, info_resp: ApiResponse) -> str:
        try:
            data = info_resp.data or {}
            day = int(data.get("total_sign_day", 0)) if isinstance(data, dict) else 0
            if day <= 0:
                return ""
            rewards = self._http.get(f"{base}{prefix}/home", headers=headers, params={"act_id": act_id, "lang": "zh-cn"})
            if rewards.code == 0 and isinstance(rewards.data, dict):
                awards = rewards.data.get("awards") or []
                if 0 < day <= len(awards):
                    a = awards[day - 1]
                    return f"「{a.get('name')}」x{a.get('cnt')}"
        except Exception:  # noqa: BLE001
            pass
        return ""

    # ---- 社区签到 ----
    def bbs_checkin(self) -> list[CheckinResult]:
        self._ensure_device_id()
        # 查询任务状态判断是否需要签到
        tasks = self._get_task_state()
        if tasks is None:
            return [self._fail("bbs_checkin", "获取社区任务状态失败", "社区签到")]
        if tasks.get("sign_done"):
            return [self._ok("bbs_checkin", "社区今日已签到", game="社区签到", status=CheckinStatus.ALREADY_SIGNED)]
        # 执行签到（对每个配置的游戏论坛）
        msgs: list[str] = []
        for game_key in self.games:
            forum_id = FORUM_GIDS.get(game_key, FORUM_GIDS.get("zzz", "8"))
            body = json.dumps({"gids": forum_id}, separators=(",", ":"))
            headers = self._bbs_headers()
            headers["DS"] = _make_ds(BBS_SALT_X6, body=body)
            headers["cookie"] = self._bbs_cookie()
            resp = self._http.post_json(URL_BBS_SIGNIN, headers=headers, json_body={"gids": forum_id})
            if resp.code == 0:
                msgs.append("社区签到成功")
            elif resp.code == 1034:
                return [CheckinResult(self.name, self.account.name, "bbs_checkin",
                    CheckinStatus.CAPTCHA_NEEDED, "社区签到触发验证码", "", "社区签到", self.display_name)]
            elif resp.code == -100:
                raise AuthExpiredError("社区签到凭证失效(retcode=-100)")
            else:
                msgs.append(f"社区签到失败: {resp.message}")
        return [self._ok("bbs_checkin", "；".join(msgs), game="社区签到")] if msgs else \
               [self._fail("bbs_checkin", "社区签到无结果", "社区签到")]

    # ---- 社区任务 ----
    def bbs_tasks(self) -> list[CheckinResult]:
        self._ensure_device_id()
        tasks = self._get_task_state()
        if tasks is None:
            return [self._fail("bbs_tasks", "获取社区任务状态失败", "社区任务")]
        read_need = tasks.get("read_need", 0)
        like_need = tasks.get("like_need", 0)
        share_done = tasks.get("share_done", False)
        if read_need == 0 and like_need == 0 and share_done:
            return [self._ok("bbs_tasks", "社区任务已全部完成", game="社区任务", status=CheckinStatus.ALREADY_SIGNED)]

        posts = self._get_post_list()
        if not posts:
            return [self._fail("bbs_tasks", "获取帖子列表失败", "社区任务")]

        headers = self._bbs_headers()
        msgs: list[str] = []

        # 浏览帖子
        viewed = 0
        for post_id in posts[:max(read_need, 3)]:
            if viewed >= max(read_need, 3):
                break
            r = self._http.get(URL_BBS_POST_DETAIL, headers=headers, params={"post_id": post_id})
            if r.code == 0:
                viewed += 1
        msgs.append(f"浏览{viewed}")

        # 点赞帖子
        liked = 0
        for post_id in posts[:max(like_need, 5)]:
            if liked >= max(like_need, 5):
                break
            r = self._http.post_json(URL_BBS_LIKE, headers=headers,
                                     json_body={"post_id": post_id, "is_cancel": False})
            if r.code == 0:
                liked += 1
        msgs.append(f"点赞{liked}")

        # 分享帖子
        shared = 0
        if not share_done and posts:
            r = self._http.get(URL_BBS_SHARE, headers=headers,
                               params={"entity_id": posts[0], "entity_type": 1})
            if r.code == 0:
                shared = 1
        msgs.append(f"分享{shared}")

        return [self._ok("bbs_tasks", "；".join(msgs), game="社区任务")]

    def _get_task_state(self) -> dict | None:
        """查询社区任务状态，返回 {sign_done, read_need, like_need, share_done}。"""
        headers = self._bbs_task_headers()
        resp = self._http.get(URL_BBS_TASKS_LIST, headers=headers, params={"point_sn": "myb"})
        if resp.code != 0:
            return None
        data = resp.data or {}
        states = data.get("states", []) if isinstance(data, dict) else []
        # task_id: 58=sign, 59=read, 60=like, 61=share
        state_map = {s.get("mission_id"): s for s in states if isinstance(s, dict)}
        sign = state_map.get(58, {})
        read = state_map.get(59, {})
        like = state_map.get(60, {})
        share = state_map.get(61, {})
        return {
            "sign_done": sign.get("is_get_award", False),
            "read_need": max(3 - read.get("happened_times", 0), 0) if read else 3,
            "like_need": max(5 - like.get("happened_times", 0), 0) if like else 5,
            "share_done": share.get("is_get_award", False),
        }

    def _get_post_list(self) -> list[str]:
        """获取帖子 ID 列表。"""
        forum_id = FORUM_PAGE_IDS.get(self.games[0] if self.games else "zzz", "57")
        headers = self._bbs_headers()
        resp = self._http.get(
            URL_BBS_POST_LIST, headers=headers,
            params={"forum_id": forum_id, "is_good": "false", "is_hot": "false", "page_size": 20, "sort_type": 1},
        )
        if resp.code != 0 or not isinstance(resp.data, dict):
            return []
        posts = resp.data.get("list", [])
        return [str(p.get("post", {}).get("post_id", "")) for p in posts
                if isinstance(p, dict) and p.get("post", {}).get("post_id")]

    # ---- 结果构造辅助 ----
    def _ok(self, action: str, message: str, reward: str = "", status: CheckinStatus = CheckinStatus.SUCCESS, game: str = "") -> CheckinResult:
        return CheckinResult(self.name, self.account.name, action, status, message, reward, game, self.display_name)

    def _fail(self, action: str, message: str, game: str = "") -> CheckinResult:
        return CheckinResult(self.name, self.account.name, action, CheckinStatus.FAILED, message, "", game, self.display_name)
