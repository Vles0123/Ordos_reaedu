#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
综合素质评价 - 填写器

用法:
  单人版:
    python3 zhsz_fill.py --user <学生账号> --name <姓名> --parent <家长账号>
    python3 zhsz_fill.py --user <学生账号> --name <姓名> --parent <家长账号> --dry-run

  批量版:
    python3 zhsz_fill.py --xls students.xls --password "<统一密码>"
    python3 zhsz_fill.py --xls students.xls --limit 5 --dry-run

参数:
  --password   统一密码（不填则用 --passwords 表）
  --dry-run    只生成内容不提交
  --skip-done  已填写过（自我评语非空）的跳过
  --workers    并发数（默认 1，建议不超过 3，避免触发风控）
"""
import argparse
import csv
import json
import os
import random
import sys
import time
import traceback
from datetime import datetime

import zhsz_content
from zhsz_login import Client, LoginError, LockedError, CredentialError

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
CONTENT_OVERRIDE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "content")
os.makedirs(OUT_DIR, exist_ok=True)


def load_content(username, name):
    """优先使用 content/<username>.json（人工/子智能体生成），否则用内置生成器。"""
    p = os.path.join(CONTENT_OVERRIDE_DIR, username + ".json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("username", username)
        data.setdefault("name", name)
        return data, "override"
    return zhsz_content.build_content(username, name), "auto"

ASPECT_ORDER = ["思想品德", "学业水平", "身心健康", "艺术素养", "劳动与社会实践"]


# ---------------- 成长规划 ----------------
def fill_grow_plan(cli: Client, content: dict, dry_run=False):
    data = cli.get_grow_plan()
    if data.get("z") == 0:
        return "跳过：当前学期成长规划不可编辑(已归档)"

    goals = data["t"]["myProject3"]
    texts = content["goals"]

    # 按方面分组填充，避免顺序错位
    by_aspect = {}
    idx = 0
    for aspect in ASPECT_ORDER:
        n = len([g for g in goals if g.get("aspectName") == aspect])
        by_aspect[aspect] = texts[idx: idx + n]
        idx += n

    counters = {a: 0 for a in by_aspect}
    for g in goals:
        a = g.get("aspectName")
        i = counters.get(a, 0)
        picks = by_aspect.get(a, [])
        if i < len(picks):
            g["goal"] = picks[i]
        counters[a] = i + 1

    payload = {
        "plan": {
            "myCharacter": content["myCharacter"],
            "myMotto": content["myMotto"],
            "myMerit": content["myMerit"],
            "myDefect": content["myDefect"],
            "myInterest": content["myInterest"],
            "myInterest2": content.get("myInterest2", ""),
            "myInterest3": content.get("myInterest3", ""),
            "myProfession1": content["myProfession1"],
            "myProfession2": content["myProfession2"],
            "myProfession3": content["myProfession3"],
        },
        "subjectStr1": content["subjectStr1"],
        "subjectStr2": content["subjectStr2"],
        "subjectStr3": content["subjectStr3"],
        "professions": content["professions"],
        "goals": goals,
    }
    if dry_run:
        return "dry-run：成长规划 payload 就绪（目标 %d 条）" % len(goals)

    r = cli.save_grow_plan(payload)
    if not r.get("success"):
        raise RuntimeError("成长规划保存失败: %s" % r.get("msg"))
    return "成长规划 ✓ " + str(r.get("msg"))


# ---------------- 评语 ----------------
def get_comments(cli: Client):
    r = cli.post("/eedu_high/r_getCommentTermGrid.do", data={})
    r.raise_for_status()
    return r.json()


def save_comment(cli: Client, text: str, dry_run=False):
    if dry_run:
        return "dry-run：评语 %d 字就绪" % len(text)
    r = cli.post(
        "/eedu_high/r_saveComment.do",
        data=json.dumps({"comment": text}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    r.raise_for_status()
    j = r.json()
    if not j.get("success"):
        raise RuntimeError("评语保存失败: %s" % j.get("msg"))
    return "评语 ✓ " + str(j.get("msg"))


def already_has_comment(items, usertype):
    for it in items:
        if it.get("usertype") == usertype:
            c = it.get("commentContent") or ""
            return bool(c.strip()) and "<textarea" not in c
    return False


# ---------------- 单人流程 ----------------
def open_session(username, password, new_password=None):
    """登录并返回 Client；若账号处于"密码已过期"状态且提供了 new_password，则先改密再登录。"""
    cli = Client()
    cli.login_rest(username, password)
    if cli.password_expired():
        if not new_password:
            raise LoginError("该账号密码已过期，需先修改密码（用 --new-password 指定新密码）")
        cli.change_password(new_password)
        cli = Client()
        cli.login_rest(username, new_password)
        if cli.password_expired():
            raise LoginError("改密后仍提示密码过期")
    return cli


def process_one(username, name, parent_username, password, args):
    result = {"username": username, "name": name, "parent": parent_username,
              "grow_plan": "", "self_comment": "", "parent_comment": "", "error": ""}
    content, src = load_content(username, name)
    result["content"] = content
    result["source"] = src

    # 1) 学生端
    try:
        stu = open_session(username, password, args.new_password)
        result["grow_plan"] = fill_grow_plan(stu, content, args.dry_run)

        items = get_comments(stu)
        if args.skip_done and already_has_comment(items, "STUDENT"):
            result["self_comment"] = "跳过：已有自我评语"
        else:
            result["self_comment"] = save_comment(stu, content["selfComment"], args.dry_run)
    except CredentialError as e:
        result["error"] = "学生账号异常: %s" % e
        return result
    except Exception as e:
        result["error"] = "学生端失败: %s" % e
        traceback.print_exc()

    # 2) 家长端（只填家长寄语）
    if parent_username:
        try:
            par = open_session(parent_username, password, args.new_password)
            items = get_comments(par)
            if args.skip_done and already_has_comment(items, "PARENT"):
                result["parent_comment"] = "跳过：已有家长寄语"
            else:
                result["parent_comment"] = save_comment(
                    par, content["parentComment"], args.dry_run)
        except CredentialError as e:
            result["parent_comment"] = "家长账号异常: %s" % e
        except Exception as e:
            result["parent_comment"] = "家长端失败: %s" % e
            traceback.print_exc()

    return result


# ---------------- 表格读取 ----------------
def read_xls(path):
    import xlrd
    wb = xlrd.open_workbook(path)
    rows = []
    for sh in wb.sheets():
        cls = sh.name.replace("高中3年级", "").strip()
        for r in range(2, sh.nrows):
            vals = [str(sh.cell_value(r, c)).strip() for c in range(sh.ncols)]
            if len(vals) < 7 or not vals[5]:
                continue
            rows.append({
                "class": cls,
                "no": vals[0],
                "xueji": vals[1],
                "name": vals[2],
                "gender": vals[4],
                "username": vals[5],
                "parent": vals[6],
            })
    return rows


def load_password_map(path):
    """可选的账号密码表: CSV 两列 username,password"""
    m = {}
    if not path:
        return m
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip():
                m[row[0].strip()] = row[1].strip()
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xls")
    ap.add_argument("--user")
    ap.add_argument("--name", default="")
    ap.add_argument("--parent", default="")
    ap.add_argument("--password", default="", help="统一密码（初始密码）")
    ap.add_argument("--passwords", help="账号密码表 CSV（两列: username,password），优先于 --password")
    ap.add_argument("--new-password",
                    help="账号提示“密码已过期”时，改成的统一新密码（不填则跳过这类账号）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-done", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--class", dest="klass", help="只处理某个班，如 300班")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()

    if args.xls:
        students = read_xls(args.xls)
        if args.klass:
            students = [s for s in students if s["class"] == args.klass]
        if args.limit:
            students = students[: args.limit]
        print("共 %d 名学生待处理" % len(students))
    elif args.user:
        students = [{"username": args.user, "name": args.name or args.user,
                     "parent": args.parent, "class": "", "gender": "",
                     "no": "", "xueji": ""}]
    else:
        ap.error("需要 --xls 或 --user")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    pmap = load_password_map(args.passwords)
    content_dir = os.path.join(OUT_DIR, "content_" + stamp)
    os.makedirs(content_dir, exist_ok=True)
    report_path = os.path.join(OUT_DIR, "report_%s.csv" % stamp)

    done = ok = fail = 0
    with open(report_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["班级", "学号", "姓名", "学生账号", "家长账号",
                    "成长规划", "自我评语", "家长寄语", "错误"])

        for i, s in enumerate(students, 1):
            print("\n[%d/%d] %s %s (%s)" % (i, len(students), s["class"], s["name"], s["username"]))
            pwd = s.get("password") or pmap.get(s["username"]) or args.password
            res = process_one(s["username"], s["name"], s.get("parent"),
                              pwd, args)
            with open(os.path.join(content_dir, s["username"] + ".json"), "w",
                      encoding="utf-8") as cf:
                json.dump(res.get("content", {}), cf, ensure_ascii=False, indent=1)

            w.writerow([s["class"], s["no"], s["name"], s["username"], s.get("parent", ""),
                        res["grow_plan"], res["self_comment"], res["parent_comment"],
                        res["error"]])
            f.flush()

            done += 1
            if res["error"]:
                fail += 1
                print("  ✗", res["error"])
            else:
                ok += 1
                print("  ✓ 规划:", res["grow_plan"])
                print("  ✓ 自评:", res["self_comment"])
                print("  ✓ 家长:", res["parent_comment"])

            time.sleep(random.uniform(0.8, 1.8))

    print("\n完成 %d 人：成功 %d，失败 %d" % (done, ok, fail))
    print("报告:", report_path)
    print("内容:", content_dir)


if __name__ == "__main__":
    main()
