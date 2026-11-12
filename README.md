# Ordos_reaedu · 综合素质评价批量填写工具

内蒙古普通高中学生综合素质评价平台（`zhsz.nmgov.edu.cn`）自动化填写脚本，面向高三年级批量录入场景：
批量完成 **成长规划 + 自我评语 + 家长寄语**，支持单人版与班级/年级批量版。

> ⚠️ 本项目仅供**学校管理员、班主任或学生本人**在获得授权的前提下，为自己管理范围内的账号录入资料使用。
> 请勿用于未授权账号的访问、口令猜测或数据抓取。

## 文档

- [成果汇报（技术方案 + 试跑结果）](docs/REPORT.md)

## 功能

- **程序化登录**：走 CAS REST 协议（`/cas/v1/tickets`）换取票据，无需人工点验证码
- **验证码兜底**：REST 不可用时回退到表单登录，ddddocr 双模型识别验证码，两个模型一致才提交（换图不消耗重试次数）
- **初始密码处理**：账号提示“密码已过期”时，自动改密后继续（新密码由使用者指定）
- **内容生成**：按账号做随机种子，为每个学生生成差异化又稳定的资料；也可用 LLM 生成后覆盖
- **幂等续跑**：`--skip-done` 依据“已有评语”跳过已完成账号，失败可反复重试
- **结果留痕**：每次运行输出逐人 JSON 内容快照 + CSV 报告

## 安装

```bash
pip3 install -r requirements.txt
```

## 快速开始

### 1. 单人版

```bash
# 只生成内容、不提交（先看看效果）
python3 zhsz_fill.py --user 学生账号 --name 学生姓名 --parent 家长账号 --dry-run

# 真实提交
python3 zhsz_fill.py --user 学生账号 --name 学生姓名 --parent 家长账号
```

### 2. 批量版

把学生名单整理成 xls（每张工作表一个班，第 1 行标题、第 2 行表头，列顺序固定）：

| 序号 | 学生学籍号 | 学生姓名 | 学生别名 | 学生性别 | 学生用户名 | 家长用户名 |
|---|---|---|---|---|---|---|
| 1 | G1506... | 张三 | | 男 | x1000001 | p1000001 |

```bash
# 解析名单 + 生成内容
python3 zhsz_prepare.py --xls students.xls --prompts

# 小批量试跑
python3 zhsz_fill.py --xls students.xls --limit 3

# 单班、带初始密码与新密码
python3 zhsz_fill.py --class 300班 --password "初始密码" --new-password "新密码"

# 全量（凭据不同则用密码表）
python3 zhsz_fill.py --xls students.xls --passwords pwd.csv --skip-done
```

`pwd.csv`：

```csv
username,password
x1000001,abc12345
p1000001,abc12345
```

### 3. 用 LLM 精修内容

`zhsz_prepare.py --prompts` 会在 `prompts/` 下生成分批提示词（含字段规范与学生名单）。
把生成结果存成 JSON 数组后导入为覆盖内容：

```bash
python3 - <<'EOF'
import json
for c in json.load(open('content_batch_001.json', encoding='utf-8')):
    json.dump(c, open(f"content/{c['username']}.json", 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
EOF
```

`zhsz_fill.py` 优先读取 `content/<用户名>.json`，不存在才用内置生成器。

## 成果取证与汇报（zhsz_evidence.py）

一条命令完成“登录截图 → 回读数据 → 生成汇报”，不需要手工整理。

```bash
# 1) 登录并截图（--mask 给右上角登录身份打码；--naming index 用 01_stu_plan.png 这类 ASCII 名）
python3 zhsz_evidence.py shoot --users users.txt --out docs/screenshots --mask

# 2) 走接口回读实际写入量，生成 Markdown 汇报
python3 zhsz_evidence.py report --users users.txt --out docs/REPORT.md --shots docs/screenshots

# 单人：直接用 --user / --parent
python3 zhsz_evidence.py shoot --user 学生账号:密码 --parent 家长账号:密码 --name 张三
```

`users.txt` 每行：`学生账号,学生密码,姓名,家长账号,家长密码`

截图命名规则：`<序号>_<stu|par>_<plan|comment>.png`，序号对应用户清单顺序，
报告里会自动生成「文件 → 学生 · 身份 · 页面」的对照表。

> `--mask` 只模糊右上角的登录身份区域；页面正文（如家长寄语里出现学生姓名）仍会保留，
> 因此 `docs/screenshots/` 默认写入 `.gitignore`。

## 参数

| 参数 | 说明 |
|---|---|
| `--xls` | 学生名单 xls |
| `--user` / `--name` / `--parent` | 单人模式 |
| `--class` | 只处理某个班（如 `300班`） |
| `--limit` | 只处理前 N 个 |
| `--password` | 统一密码 |
| `--passwords` | 账号密码表 CSV，优先于 `--password` |
| `--new-password` | “密码已过期”账号改成的新密码，不填则跳过这类账号 |
| `--dry-run` | 只生成内容不提交 |
| `--skip-done` | 已有评语的跳过 |

## 接口对照

| 用途 | 方法 | 路径 |
|---|---|---|
| REST 认证 | POST | `/cas/v1/tickets`（取 TGT）→ `/cas/v1/tickets/{TGT}`（换 ST） |
| 票据换会话 | GET | `/eedu_base/ticketLogin.do?ticket=ST` |
| 表单登录（兜底） | GET/POST | `/cas/login` + `/cas/kaptcha.jpg` |
| 修改密码 | POST | `/eedu_base/r_changeUserSelfPwd.do` |
| 菜单树 | GET | `/eedu_base/c_findUserMenuApps.do` |
| 成长规划读 | POST | `/eedu_high/r_getHiGrowPlan.do` |
| 成长规划写 | POST | `/eedu_high/r_saveHischGrowPlan.do` |
| 评语读 | POST | `/eedu_high/r_getCommentTermGrid.do` |
| 评语写 | POST | `/eedu_high/r_saveComment.do` |

学科代码：`1语文 2数学 3外语 4物理 5化学 6生物 7历史 8政治 9地理 10音乐 11体育 12美术 13信息技术 14通用技术 15综合实践活动`

## 注意事项

- **风控**：表单登录路径连续输错验证码会触发“超出最大重试次数，请十分钟后再试”（按账号+IP）。脚本已内置双模型一致才提交、限流自动等待、每账号最多提交 2 次。
- **改密不可逆**：`--new-password` 会把账号密码改成新值，学生/家长之后必须用新密码登录，执行前请与相关人员沟通。
- **归档学期**：成长规划一旦归档（接口返回 `z=0`）不可写，脚本会跳过并在报告中标注。
- **数据合规**：生成的 `content/`、`out/` 含真实姓名与评语，请勿提交到公开仓库（`.gitignore` 已排除）。

## 免责声明

使用者应确保对目标账号拥有合法操作权限，并遵守学校的相关管理规定与目标平台的服务条款。
因超出授权范围使用产生的后果由使用者自行承担。

## License

MIT
