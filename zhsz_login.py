#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
综合素质评价系统 - 登录模块（CAS 统一认证 + RSA 加密 + 图形验证码）

流程:
1. GET /cas/login?service=...  → 拿 JSESSIONID 与 execution 流程令牌
2. GET /cas/kaptcha.jpg        → 验证码图片（与 session 绑定）
3. POST /cas/login             → un/pw 为 RSA(PKCS1v1.5) 密文, captcha 明文
4. CAS 302 回 /eedu_base/ticketLogin.do → 建立业务会话
"""
import base64
import re
import time

import requests
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

BASE = "https://zhsz.nmgov.edu.cn"
CAS_LOGIN = BASE + "/cas/login?service=" + \
    "https%3A%2F%2Fzhsz.nmgov.edu.cn%2Feedu_base%2FticketLogin.do"
KAPTCHA = BASE + "/cas/kaptcha.jpg"

# 页面内硬编码的 RSA 公钥
PUB_KEY = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCXORShBfOxGJc2V9Da/V95S+ioLGAiRDGrZ3Po"
    "Qwmo8Z4U/N8XzmbIR2gzq5eiw69DRcaHUMhGDLv2JG9hpoRNW5Jh8imDWVGPg5qY83Djr1QhPErZ"
    "z9tsc644JfAYaiEpNn3aeWsW2pXzqHl8/49r+bOQGrg9q3/npZWDCdeIiQIDAQAB"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class LoginError(Exception):
    pass


class CaptchaError(LoginError):
    """验证码识别错误，可重试。"""


class CredentialError(LoginError):
    """用户名或密码错误，重试无意义。"""


class LockedError(LoginError):
    """重试次数超限，需要等待。"""


_OCR_A = None
_OCR_B = None


def _ocr_engines():
    global _OCR_A, _OCR_B
    if _OCR_A is None:
        import ddddocr
        _OCR_A = ddddocr.DdddOcr(det=False, ocr=True, show_ad=False)  # 专为验证码
        _OCR_B = ddddocr.DdddOcr(show_ad=False)                      # 通用模型
    return _OCR_A, _OCR_B


def solve_captcha(path: str):
    """双模型识别，返回 (文本, 是否一致)。不一致说明不可信，应换一张图。"""
    import io

    from PIL import Image

    a, b = _ocr_engines()
    raw = open(path, "rb").read()
    t1 = a.classification(raw).strip()

    img = Image.open(io.BytesIO(raw)).convert("L")
    big = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    t2 = b.classification(buf.getvalue()).strip()

    if t1 == t2 and t1:
        return t1, True
    return (t2 or t1), False


def rsa_encrypt(text: str) -> str:
    """JSEncrypt 等价实现：RSA/ECB/PKCS1Padding，输出 base64。"""
    der = base64.b64decode(PUB_KEY)
    key = RSA.import_key(der)
    cipher = PKCS1_v1_5.new(key)
    return base64.b64encode(cipher.encrypt(text.encode("utf-8"))).decode()


class Client:
    """一个账号对应一个 Client（独立 cookie 会话）。"""

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)

    # ---------- 登录 ----------
    def fetch_login_page(self):
        r = self.s.get(CAS_LOGIN, timeout=30)
        r.raise_for_status()
        m = re.search(r'name="execution"\s+value="([^"]+)"', r.text)
        if not m:
            raise LoginError("登录页未找到 execution 令牌")
        self.execution = m.group(1)
        return r.text

    def fetch_captcha(self, path="/tmp/zhsz_captcha.jpg") -> str:
        r = self.s.get(KAPTCHA, timeout=30)
        r.raise_for_status()
        if not r.headers.get("Content-Type", "").startswith("image"):
            raise LoginError("验证码接口未返回图片: %s" % r.headers.get("Content-Type"))
        with open(path, "wb") as f:
            f.write(r.content)
        return path

    def login(self, username: str, password: str, captcha: str):
        data = {
            "execution": self.execution,
            "_eventId": "submit",
            "geolocation": "",
            "un": rsa_encrypt(username),
            "pw": rsa_encrypt(password),
            "captcha": captcha,
        }
        r = self.s.post(CAS_LOGIN, data=data, timeout=30, allow_redirects=True)
        r.raise_for_status()

        # 成功: 已离开 CAS 登录页，被重定向到业务侧（ticketLogin / r_index）
        if "/cas/login" not in r.url:
            return True

        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text)

        if "超出最大重试次数" in text:
            raise LockedError("IP 被临时限制: 超出最大重试次数，请十分钟后再试")
        if "验证码输入错误" in text or "验证码不正确" in text or "验证码错误" in text:
            raise CaptchaError("验证码错误")
        if "用户名或者密码错误" in text or "用户名或密码错误" in text:
            raise CredentialError("用户名或密码错误")
        if "用户不存在" in text:
            raise CredentialError("用户不存在")
        if "账号已被禁用" in text or "账号被停用" in text:
            raise CredentialError("账号已被禁用")
        raise LoginError("登录结果无法识别，最终 URL=%s" % r.url)

    def login_auto(self, username: str, password: str, tries: int = 2,
                   captcha_path: str = "/tmp/zhsz_captcha.jpg",
                   max_refresh: int = 12, lock_wait: int = 660) -> bool:
        """登录：验证码双模型一致才提交；不一致则换图（不消耗重试次数）。

        tries: 最多提交次数（默认 2，避免触发风控限流）
        lock_wait: 触发限流后的等待秒数
        """
        submissions = 0
        for _ in range(max_refresh):
            self.s.cookies.clear()
            self.fetch_login_page()

            code, sure = None, False
            for _ in range(6):
                img = self.fetch_captcha(captcha_path)
                code, sure = solve_captcha(img)
                if sure and 3 <= len(code) <= 6:
                    break
            if not sure or not code:
                continue

            try:
                self.login(username, password, code)
                return True
            except CaptchaError:
                submissions += 1
                if submissions >= tries:
                    break
                time.sleep(0.6)
            except LockedError:
                print("    [限流] 等待 %d 秒后重试..." % lock_wait)
                time.sleep(lock_wait)
                submissions = 0
        raise LoginError("登录失败（已提交 %d 次验证码）" % submissions)

    # ---------- CAS REST 认证（优先使用，无需验证码） ----------
    def login_rest(self, username: str, password: str) -> bool:
        """通过 CAS REST 协议登录：POST /cas/v1/tickets 取 TGT，再换 ST 建立业务会话。

        仅用于持有真实密码的账号，不做任何口令猜测。
        """
        r = self.s.post(BASE + "/cas/v1/tickets",
                        data={"username": username, "password": password}, timeout=30)
        if r.status_code == 401:
            raise CredentialError("用户名或密码错误")
        if r.status_code != 201:
            raise LoginError("REST 认证异常: HTTP %d" % r.status_code)

        tgt = r.headers.get("Location", "").split("/cas/v1/tickets/")[-1]
        if not tgt:
            raise LoginError("REST 未返回 TGT")

        svc = BASE + "/eedu_base/ticketLogin.do"
        r2 = self.s.post(BASE + "/cas/v1/tickets/" + tgt,
                         data={"service": svc}, timeout=30)
        st = r2.text.strip()
        if not st:
            raise LoginError("REST 未返回 service ticket (HTTP %d)" % r2.status_code)

        self.s.get(svc, params={"ticket": st}, timeout=30, allow_redirects=True)
        self.tgt = tgt
        return True

    def logout(self):
        try:
            self.s.get(BASE + "/eedu_base/c_logout.do", timeout=20)
        except Exception:
            pass

    def password_expired(self) -> bool:
        """业务接口返回 code=1 且提示密码过期，说明必须先改密码。"""
        r = self.post("/eedu_high/r_getCommentTermGrid.do", data={})
        try:
            j = r.json()
        except Exception:
            return False
        return isinstance(j, dict) and j.get("code") == 1 and "过期" in str(j.get("message"))

    def change_password(self, new_password: str, old_password: str = ""):
        """修改当前登录账号的密码（页面 r_changeUserSelfPwd.do）。"""
        data = {
            "userId": "",
            "password": rsa_encrypt(new_password),
            "rpassword": rsa_encrypt(new_password),
        }
        r = self.post("/eedu_base/r_changeUserSelfPwd.do", data=data)
        try:
            j = r.json()
        except Exception:
            raise LoginError("改密返回非 JSON: %s" % r.text[:100])
        if j.get("code") != 0:
            raise LoginError("改密失败: %s" % j.get("message"))
        return j.get("message", "ok")

    # ---------- 业务接口 ----------
    def get(self, path, **kw):
        return self.s.get(BASE + path, timeout=30, **kw)

    def post(self, path, **kw):
        return self.s.post(BASE + path, timeout=30, **kw)

    def cookie_header(self) -> str:
        return "; ".join("%s=%s" % (k, v) for k, v in self.s.cookies.get_dict().items())

    # ---------- 成长规划 ----------
    def get_grow_plan(self):
        r = self.post("/eedu_high/r_getHiGrowPlan.do", data={})
        r.raise_for_status()
        return r.json()

    def save_grow_plan(self, payload):
        import json
        r = self.post(
            "/eedu_high/r_saveHischGrowPlan.do",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法: python3 zhsz_login.py <用户名> <密码>")
        sys.exit(1)

    u, p = sys.argv[1], sys.argv[2]
    cli = Client()
    t0 = time.time()
    cli.login_rest(u, p)
    print("REST 登录成功 (%.1fs)，密码过期=%s" % (time.time() - t0, cli.password_expired()))
    d = cli.get_grow_plan()
    if "t" in d:
        print("成长规划接口可用，当前座右铭:", d["t"]["myPercieve2"][0].get("column4"))
    else:
        print("业务接口提示:", d)
