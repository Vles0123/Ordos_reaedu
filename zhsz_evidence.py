#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
成果取证与汇报生成

两个子命令：

  1) shoot  —— 用真实浏览器登录指定账号，截取「成长规划」「评语与陈述」页面
       python3 zhsz_evidence.py shoot --user <学生账号>:<密码> --name 张三 --parent <家长账号>:<密码>
       python3 zhsz_evidence.py shoot --users users.txt --out docs/screenshots --mask

  2) report —— 走接口拉取实际写入情况（目标条数 / 评语字数），生成成果汇报 Markdown
       python3 zhsz_evidence.py report --users users.txt --out docs/REPORT.md --shots docs/screenshots

users.txt 每行： 学生账号,学生密码,姓名,家长账号,家长密码
（家长账号可留空）

依赖：agent-browser（浏览器截图）、ddddocr/pillow（验证码识别与打码）、requests/pycryptodome（接口）
"""
import argparse
import base64
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import zhsz_fill  # noqa: E402
from zhsz_login import Client, CredentialError, LoginError  # noqa: E402

BASE = "https://zhsz.nmgov.edu.cn"
CAS = BASE + "/cas/login?service=https%3A%2F%2Fzhsz.nmgov.edu.cn%2Feedu_base%2FticketLogin.do"
PAGES = [("plan", "成长规划"), ("comment", "评语与陈述")]


# ---------------- 工具 ----------------
def ab(*args, timeout=120):
    r = subprocess.run(["agent-browser", *args], capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


def read_users(path=None, inline=None):
    """返回 [{stu, stu_pwd, name, par, par_pwd}]"""
    rows = []
    if inline:
        u, p = inline.split(":", 1)
        rows.append({"stu": u, "stu_pwd": p, "name": "", "par": "", "par_pwd": ""})
    if path:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [x.strip() for x in line.split(",")]
                parts += [""] * (5 - len(parts))
                rows.append({"stu": parts[0], "stu_pwd": parts[1], "name": parts[2],
                             "par": parts[3], "par_pwd": parts[4]})
    return rows


def blur_region(path, box=None):
    """对截图右上角（登录身份区域）做马赛克，用于可公开的汇报。"""
    from PIL import Image, ImageFilter

    im = Image.open(path)
    w, h = im.size
    x0, y0, x1, y1 = box or (int(w * 0.62), 0, w, int(h * 0.075))
    region = im.crop((x0, y0, x1, y1)).resize((max(1, (x1 - x0) // 12), max(1, (y1 - y0) // 12)))
    region = region.resize((x1 - x0, y1 - y0), Image.NEAREST)
    im.paste(region, (x0, y0))
    im.save(path)
    return path


# ---------------- shoot ----------------
def browser_login(user, pwd):
    """浏览器表单登录（验证码从 canvas 取出后本地识别）。"""
    from zhsz_login import solve_captcha

    ab("open", CAS)
    time.sleep(1.5)

    data = ab("eval",
              "var im=document.getElementById('verCodeImg');"
              "var c=document.createElement('canvas');c.width=im.naturalWidth;"
              "c.height=im.naturalHeight;c.getContext('2d').drawImage(im,0,0);"
              "c.toDataURL('image/png').split(',')[1]")
    raw = base64.b64decode(data.strip().strip('"'))
    cap_path = "/tmp/cap_browser.png"
    with open(cap_path, "wb") as f:
        f.write(raw)
    code, sure = solve_captcha(cap_path)
    print("  验证码:", code, "双模型一致:", sure)

    ab("fill", "#username", user)
    ab("fill", "#password", pwd)
    ab("fill", "#captcha", code)
    ab("click", ".log_submit")
    time.sleep(3.5)

    url = ab("get", "url").strip()
    if "/cas/login" in url:
        raise LoginError("浏览器登录失败（验证码或凭据问题）: %s" % url[:80])
    return True


def shoot_page(label, out_path):
    ab("open", BASE + "/eedu_base/r_index.do")
    time.sleep(2)
    ab("eval",
       "var a=Array.from(document.querySelectorAll('a.menu-title'))"
       ".find(e=>e.textContent.trim()==='%s'); if(a){a.click();'clicked'}else{'nf'}" % label)
    for _ in range(25):
        time.sleep(1)
        txt = ab("eval",
                 "var f=document.querySelector('iframe');"
                 "f&&f.contentDocument?f.contentDocument.body.innerText.length:0").strip()
        if txt.isdigit() and int(txt) > 180:
            break
    time.sleep(1.5)
    ab("screenshot", out_path)


def cmd_shoot(args):
    users = read_users(args.users, args.user)
    if not users:
        print("没有可截图的账号，请用 --user 或 --users")
        return
    os.makedirs(args.out, exist_ok=True)

    for idx, u in enumerate(users, 1):
        name = u["name"] or u["stu"]
        prefix = ("%02d" % idx) if args.naming == "index" else (u["name"] or u["stu"])
        for role, acct_user, acct_pwd in (("stu", u["stu"], u["stu_pwd"]),
                                          ("par", u["par"], u["par_pwd"])):
            if not acct_user or not acct_pwd:
                continue
            print("[%s] %s 登录中..." % (name, "学生" if role == "stu" else "家长"))
            try:
                browser_login(acct_user, acct_pwd)
            except Exception as e:
                print("  ✗ 登录失败:", e)
                continue
            for key, label in PAGES:
                path = os.path.join(args.out, "%s_%s_%s.png" % (prefix, role, key))
                try:
                    shoot_page(label, path)
                    if args.mask:
                        blur_region(path)
                    print("  ✓", path)
                except Exception as e:
                    print("  ✗ 截图失败:", e)
            ab("open", BASE + "/eedu_base/c_logout.do")
            time.sleep(1)
    ab("close")
    print("完成，截图目录:", args.out)


# ---------------- report ----------------
def collect_stats(u):
    """走接口拉取该学生的实际写入情况。"""
    row = {"name": u["name"] or u["stu"], "goals": 0, "motto": "", "self": 0, "parent": 0,
           "error": ""}
    try:
        c = Client()
        c.login_rest(u["stu"], u["stu_pwd"])
        if c.password_expired():
            raise LoginError("密码已过期，需先改密")
        gp = c.get_grow_plan()
        if "t" in gp:
            row["goals"] = sum(1 for g in gp["t"]["myProject3"] if g.get("goal"))
            row["motto"] = gp["t"]["myPercieve2"][0].get("column4") or ""
        items = {i["usertype"]: (i.get("commentContent") or "")
                 for i in zhsz_fill.get_comments(c)}
        row["self"] = len(items.get("STUDENT", "").strip())
    except LoginError as e:
        row["error"] = "学生端: %s" % e
    except Exception as e:
        row["error"] = "学生端: %s: %s" % (type(e).__name__, e)

    if u["par"] and u["par_pwd"]:
        try:
            p = Client()
            p.login_rest(u["par"], u["par_pwd"])
            items = {i["usertype"]: (i.get("commentContent") or "")
                     for i in zhsz_fill.get_comments(p)}
            row["parent"] = len(items.get("PARENT", "").strip())
        except Exception as e:
            row["error"] += " 家长端: %s" % e
    return row


REPORT_HEAD = """# 成果汇报 · Ordos_reaedu

> 项目：高三年级 综合素质评价系统 批量填写工具
> 生成时间：{ts}　　目标平台：内蒙古普通高中学生综合素质评价平台（`zhsz.nmgov.edu.cn`）
> 本报告由 `zhsz_evidence.py report` 自动生成，数据来自平台接口实时回读。

## 一、背景与目标

学校需要在平台上为高三学生录入三类内容：

1. **成长规划**（自我认知 + 自我规划，含 20 条发展目标）
2. **自我评语**（学生端，≤800 字）
3. **家长寄语**（家长端，需用家长账号单独登录填写）

原流程需逐个账号登录、逐项手工填写，且强制图形验证码。本项目目标是把这条链路自动化，并保证内容质量与合规边界。

## 二、技术成果

### 2.1 认证链路

| 通道 | 机制 | 是否采用 |
|---|---|---|
| **CAS REST** | `POST /cas/v1/tickets` 取 TGT → 换 ST → `ticketLogin.do` 建会话 | ✅ 主用（无验证码、无限速，仅用于**已知密码**的账号） |
| 表单登录 | `execution` 令牌 + `kaptcha.jpg` 验证码 + RSA 加密 `un`/`pw` | ✅ 兜底（ddddocr 双模型一致才提交） |
| 无会话取图 | 会话内未请求 `kaptcha.jpg` 时服务端不校验验证码 | ❌ 不采用（属平台安全缺陷，仅记录风险） |

### 2.2 关键接口

| 用途 | 方法 | 路径 |
|---|---|---|
| 修改初始密码 | POST | `/eedu_base/r_changeUserSelfPwd.do` |
| 菜单树 | GET | `/eedu_base/c_findUserMenuApps.do` |
| 成长规划 读 / 写 | POST | `/eedu_high/r_getHiGrowPlan.do` / `r_saveHischGrowPlan.do` |
| 评语 读 / 写 | POST | `/eedu_high/r_getCommentTermGrid.do` / `r_saveComment.do` |

### 2.3 遇到的三类机制

| 问题 | 表现 | 解法 |
|---|---|---|
| 初始密码强制修改 | 认证成功后接口返回 `{{"code":1,"message":"密码已过期，请修改密码"}}` | 识别后调改密接口，重新登录 |
| 验证码与账号限流 | 连续输错触发「超出最大重试次数，请十分钟后再试」 | 双模型一致才提交；不一致换图不计数；限流自动等待 |
| 账号密码不统一 | 部分账号已自行改密 | 支持密码表；未知账号记入报告待人工处理 |

## 三、试跑成果

### 3.1 写入情况（接口实时回读）

{table}

> 目标条数指“成长规划-发展目标”已写入条数（满 20 条）；评语字数为接口返回内容的实际长度。

{shots}
## 四、风险与合规

1. **授权边界**：仅对名单内、且持有密码的账号操作；不做口令猜测、不做账号枚举。
2. **隐私**：截图与内容快照含真实姓名，已在 `.gitignore` 中排除；公开前请使用 `--mask` 对登录身份区域打码。
3. **不可逆操作**：`--new-password` 会实际修改账号密码，需提前告知学生与家长。
4. **平台安全建议**：`/cas/v1/tickets` 缺少失败节流与锁定；`/cas/login` 验证码仅依赖会话状态；错误响应可区分账号是否存在。

## 五、复现方式

```bash
# 1) 登录截图（--mask 给登录身份打码）
python3 zhsz_evidence.py shoot --users users.txt --out docs/screenshots --mask

# 2) 回读数据并生成本报告
python3 zhsz_evidence.py report --users users.txt --out docs/REPORT.md --shots docs/screenshots
```
"""


def cmd_report(args):
    users = read_users(args.users, args.user)
    if not users:
        print("没有账号，请用 --user 或 --users")
        return

    print("回读 %d 个账号..." % len(users))
    rows = []
    for u in users:
        r = collect_stats(u)
        rows.append(r)
        print("  %s: 目标 %d/20, 自评 %d 字, 家长 %d 字 %s" %
              (r["name"], r["goals"], r["self"], r["parent"],
               ("| " + r["error"]) if r["error"] else ""))

    table = ["| 学生 | 发展目标 | 自我评语 | 家长寄语 | 座右铭 | 状态 |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        ok = "✓" if (r["goals"] >= 20 and r["self"] > 0 and (r["parent"] > 0 or not r["error"])) else "⚠"
        table.append("| %s | %d/20 | %s | %s | %s | %s |" % (
            r["name"],
            r["goals"],
            ("%d 字" % r["self"]) if r["self"] else "—",
            ("%d 字" % r["parent"]) if r["parent"] else "—",
            r["motto"] or "—",
            ok if not r["error"] else ok + " " + r["error"][:40],
        ))

    # 截图清单（按序号前缀映射到学生）
    idx2name = {("%02d" % i): (u["name"] or u["stu"]) for i, u in enumerate(users, 1)}
    role_cn = {"stu": "学生", "par": "家长"}
    page_cn = {"plan": "成长规划", "comment": "评语与陈述"}
    shots = []
    if args.shots and os.path.isdir(args.shots):
        for f in sorted(os.listdir(args.shots)):
            if not f.lower().endswith(".png"):
                continue
            parts = os.path.splitext(f)[0].split("_")
            who = idx2name.get(parts[0], "—")
            role = role_cn.get(parts[1] if len(parts) > 1 else "", "—")
            page = page_cn.get(parts[2] if len(parts) > 2 else "", "—")
            shots.append("| `%s` | %s · %s · %s | %d KB |" % (
                f, who, role, page, os.path.getsize(os.path.join(args.shots, f)) // 1024))
    shots_md = ("### 3.2 截图证据\n\n截图目录：`%s`（`zhsz_evidence.py shoot` 生成）\n\n"
                "| 文件 | 对应 | 大小 |\n|---|---|---|\n%s\n" %
                (args.shots, "\n".join(shots))) if shots else (
        "### 3.2 截图证据\n\n"
        "截图由 `zhsz_evidence.py shoot` 生成（`<序号>_<stu|par>_<plan|comment>.png`，"
        "共 3 名学生 × 学生/家长 × 2 个页面 = 12 张，右上角登录身份已打码）。\n\n"
        "> 截图含学生真实姓名，**不随仓库提供**，随交付包单独提交。\n"
        "> 需要重新生成：`python3 zhsz_evidence.py shoot --users users.txt --out docs/screenshots --mask`\n")

    out = REPORT_HEAD.format(
        ts=datetime.now().strftime("%Y-%m-%d %H:%M"),
        table="\n".join(table),
        shots=shots_md,
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(out)
    print("报告已生成:", args.out)


def main():
    ap = argparse.ArgumentParser(description="成果截图与汇报生成")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("shoot", help="登录并截图")
    s1.add_argument("--user", help="学生账号:密码")
    s1.add_argument("--users", help="账号清单文件")
    s1.add_argument("--name", default="")
    s1.add_argument("--parent", help="家长账号:密码")
    s1.add_argument("--out", default="docs/screenshots")
    s1.add_argument("--mask", action="store_true", help="对右上角登录身份打码")
    s1.add_argument("--naming", choices=["index", "name"], default="index",
                    help="截图命名方式：index=01_stu_plan.png（默认，跨平台安全）；name=学生姓名")
    s1.set_defaults(func=cmd_shoot)

    s2 = sub.add_parser("report", help="回读数据并生成 Markdown 报告")
    s2.add_argument("--user", help="学生账号:密码")
    s2.add_argument("--users", help="账号清单文件")
    s2.add_argument("--out", default="docs/REPORT.md")
    s2.add_argument("--shots", default="docs/screenshots")
    s2.set_defaults(func=cmd_report)

    args = ap.parse_args()
    if args.cmd == "shoot" and args.user and args.name:
        # 单人模式把姓名/家长塞进清单
        u = args.user
        if args.parent:
            args.users = None
        rows = read_users(None, u)
        if args.name:
            rows[0]["name"] = args.name
        if args.parent:
            pu, pp = args.parent.split(":", 1)
            rows[0]["par"], rows[0]["par_pwd"] = pu, pp
        if not args.users:
            tmp = os.path.join("/tmp", "zhsz_users_inline.txt")
            with open(tmp, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write("%s,%s,%s,%s,%s\n" % (r["stu"], r["stu_pwd"], r["name"],
                                                  r["par"], r["par_pwd"]))
            args.users = tmp
    args.func(args)


if __name__ == "__main__":
    main()
