#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
准备阶段：解析学生表格 → 输出 students.json + 为每人生成内容 JSON

用法:
  python3 zhsz_prepare.py --xls students.xls                # 用内置生成器生成全部内容
  python3 zhsz_prepare.py --xls students.xls --prompts       # 另外输出子智能体的分批提示词
  python3 zhsz_prepare.py --xls students.xls --batch-size 20
"""
import argparse
import json
import os

import zhsz_content

HERE = os.path.dirname(os.path.abspath(__file__))
CONTENT_DIR = os.path.join(HERE, "content")
PROMPT_DIR = os.path.join(HERE, "prompts")


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
                "class": cls, "no": vals[0], "xueji": vals[1], "name": vals[2],
                "gender": vals[4], "username": vals[5], "parent": vals[6],
            })
    return rows


PROMPT_HEADER = """你是高中综合素质评价的填写助手。请为下面每位学生生成一套成长规划与评语内容，\
输出一个 JSON 数组（不要任何解释文字），每个元素字段如下：

{
 "username": "学生账号原样",
 "name": "姓名",
 "subjectStr1": "感兴趣的学科代码，逗号分隔",   // 1语文 2数学 3外语 4物理 5化学 6生物 7历史 8政治 9地理 10音乐 11体育 12美术 13信息技术 14通用技术 15综合实践活动
 "subjectStr2": "优势学科代码",
 "subjectStr3": "本学期学业水平考试科目代码",
 "myCharacter": "性格特点，40-60字",
 "myMotto": "座右铭，一句",
 "myMerit": "优点，50-80字",
 "myDefect": "不足，40-60字",
 "myInterest": "爱好特长，30-50字",
 "myInterest2": "",
 "myInterest3": "",
 "myProfession1": "职业意向1",
 "myProfession2": "职业意向2",
 "myProfession3": "职业意向3",
 "professions": [{"professionName":"专业1","schoolName":"学校1"},{"professionName":"专业2","schoolName":"学校2"},{"professionName":"专业3","schoolName":"学校3"}],
 "goals": ["20条发展目标"]  // 顺序：思想品德4条、学业水平4条、身心健康4条、艺术素养4条、劳动与社会实践4条
 "selfComment": "自我评语，200-300字，学生第一人称",
 "parentComment": "家长寄语，150-250字，家长口吻，开头称呼学生姓名"
}

要求：
- 内容必须是内蒙古地区高中三年级学生的合理表达，积极正面，不出现具体分数、排名、奖项。
- 三个职业与三个专业/学校要相互对应，学校用内蒙古自治区内高校。
- 同一批内不要出现完全相同的句式，尽量差异化。
- 只输出 JSON 数组，字段名严格一致。

学生名单（班级/姓名/性别/学生账号）：
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xls", required=True)
    ap.add_argument("--prompts", action="store_true")
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    students = read_xls(args.xls)
    if args.limit:
        students = students[: args.limit]
    os.makedirs(CONTENT_DIR, exist_ok=True)

    with open(os.path.join(HERE, "students.json"), "w", encoding="utf-8") as f:
        json.dump(students, f, ensure_ascii=False, indent=1)
    print("学生总数: %d → students.json" % len(students))

    n_new = 0
    for s in students:
        p = os.path.join(CONTENT_DIR, s["username"] + ".json")
        if os.path.exists(p):
            continue
        c = zhsz_content.build_content(s["username"], s["name"], s["gender"], s["class"])
        with open(p, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
        n_new += 1
    print("生成内容文件: %d 份（已有则跳过）→ content/" % n_new)

    if args.prompts:
        os.makedirs(PROMPT_DIR, exist_ok=True)
        for i in range(0, len(students), args.batch_size):
            chunk = students[i: i + args.batch_size]
            lines = "\n".join(
                "%s\t%s\t%s\t%s" % (s["class"], s["name"], s["gender"], s["username"])
                for s in chunk
            )
            path = os.path.join(PROMPT_DIR, "batch_%03d.txt" % (i // args.batch_size + 1))
            with open(path, "w", encoding="utf-8") as f:
                f.write(PROMPT_HEADER + lines)
        print("提示词批次: %d 个 → prompts/" % ((len(students) + args.batch_size - 1) // args.batch_size))


if __name__ == "__main__":
    main()
