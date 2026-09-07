# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目是什么

天猫自营（万家乐）经营看板。数据源在飞书维表（业绩达成表）+ 生意参谋（sycm），经本仓库的管道加工成 `data.js`/`index.html`，本地 `python3 server.py` 起服务（localhost:8080）查看，另同步一份到 GitHub Pages。

## 三条数据管道

### 1. 飞书读 + 写回（daily_pull.py → 看板）
- `daily_pull.py`：通过 CDP 读飞书维表（`FEISHU_URL` wiki），按 `AUG_COLS` 行列映射拉 8 月每日指标（a业绩/refund_amt退款/post_refund去退/v访客/b买家/aov/ref退款率/cart_users加购/cart_rate加购率），输出补进 `data.json`。
- `fix_data.py`：`data.json` → `data.js`。归一化 `.NN→0.NN`、补齐裸键、重算月合计、**同期对齐硬校验**（未来天若带 `ly_*` 直接抛错）。数据缺失时会把该天 `ly_*` 清空。
- `backfill_yoy_net.py` / `backfill_refund.py` / `backfill_ly_metrics.py` / `backfill_targets.py`：一次性/按需补历史字段。
- 8 月表用**新引擎** `s.getActiveSheet().getValue(row,col)`，`row = AUG_FIRST_ROW + (day-1)`。7 月表用旧引擎（`read_july.py` 参考），行号不同（7月tab行8无数据，用2025段行39算前同比）。

### 2. 生意参谋 → 飞书写回（sycm_extract.py → sycm_sync.py）
- `sycm_extract.py`：连 9225（商家登录 Chrome），`fetch_daily()` 抓昨日 sycm 指标（a/refund_amt/post_refund/v/b/aov/ref/cart_users/cart_rate，比率字段存百分数、转化存小数）。
- `sycm_sync.py`：把抓到的值**只补空缺**写回飞书 8 月表（`is_empty_val` 守卫，已有值不动），带写入后重开 tab 校验持久化。支持 `--dry-run`。
- `sycm_backfill.py`：用 sycm coreIndex 历史 API 补漏天。
- 编排入口 `sycm_sync.sh`，launchd `com.midea.dashboard.sycm-sync`（9:30/14:30）先写表，`daily-pull`（10:00/15:00）后读表。

### 3. 风控哨兵（sycm_risk_check.py）
三层检测（URL 关键词 / 正文文案 / 弹窗覆盖层），命中即**熔断**：写 `sycm_RISK.txt`、杀同步进程、关 9225、通知用户。`--check/--remind/--selftest/--clear`。

## 三个 Chrome 实例（必须分清）

| 端口 | profile | 登录态 | 用途 |
|------|---------|--------|------|
| 9223 | `chrome-profile/` | 飞书 | daily_pull / sycm_sync 写回 |
| 9225 | `chrome-profile-sycm/` | 商家（卖家）sycm | sycm_extract 抓生意参谋 |
| 9226 | `chrome-profile-tmall-buyer/` | 个人买家淘宝号 | 抓竞品详情页/评价 |

- CDP proxy 在 localhost:3456，`daily_pull.py` 的 `ensure_proxy()` 幂等启动。`cdp_new/cdp_eval/cdp_close` 走 proxy。
- 商家账号**无法**看别家店铺详情页（淘宝安全策略）；详情页要 9226 买家号。
- keep-warm：`sycm_keepalive.sh`（launchd 300s 间隔）保活 9225，不杀进程。
- **profile 和 cookie 文件绝不可提交 git**（`.gitignore` 已覆盖 `chrome-profile*`、`sycm_cookies.json`）。

## 关键坑（读代码前先看）

1. **飞书写入不持久化**：`s.setValue()` 只改内存模型，不触发保存。必须 `s.setActiveCell(row,col)` → CDP 键盘输入 → Enter，再 sleep 15s 等自动保存 → **重开新 tab 读回校验**。`sycm_sync.py` 的 `write_cells()` 是唯一正确写法，照抄。
2. **index.html 不能用 `sed -i ''`**（BSD sed 会清空文件）。版本号 `data.js?v=xxx` 用 Edit 工具改。
3. **git push 需关闭沙箱**：SSH 22 端口被沙箱拦，push 必须 `dangerouslyDisableSandbox: true`。
4. **git 双分支同步**：main（源码）+ gh-pages（部署）独立历史。同步流程见 `daily_pull.sh`：commit main → `git checkout gh-pages && git checkout main -- data.json data.js index.html && commit && push` → 回 main。push 失败常见（SSH），检查后重试。
5. **8 月双引擎**：读 8 月表用新 API `getValue(row,col)`；别拿 7 月旧引擎代码套 8 月。
6. **同期对齐**：`fix_data.py` 会清空无数据天的 `ly_*`；`daily_pull.py` 的 merge 逻辑要显式保留外部字段 `ly_post_refund`/`y_net`。
7. **竞品数据**（`r9u_competitors.json`）：天猫详情页 + 买家号抓取，评价原文 + 参数。数据口径以 sycm 跟踪链接的 4 个单品为准（海尔K70H/K80、美的M10S Max、万和V10L）。

## 常用命令

```bash
python3 server.py                 # 本地看板 http://localhost:8080（no-cache）
python3 fix_data.py               # data.json → data.js（含硬校验）
python3 daily_pull.py             # 飞书拉数补 data.json
python3 sycm_sync.py --dry-run    # 预览待写回（不改表）
launchctl kickstart -k gui/$(id -u)/com.midea.dashboard.daily-pull   # 测真实定时路径
```

手动更新流程：改 `data.json` → `python3 fix_data.py` → 刷新浏览器（`?v=` 已由脚本自动 bump；手动改的话 index.html 里 `data.js?v=` 也要改，否则静态缓存看不到更新）。

## 其他数据文件

- `category_data.js` / `channel_data.json`：品类/渠道拆分（`gen_category_js.py` 生成）。
- `core_monthly.json` / `july_data.json`：月度核心数据。
- `qqdocs_extract.py`：腾讯文档品类数据提取。
- `data.json` 是权威中间态，`data.js` 是产物；两者都要提交 gh-pages。
