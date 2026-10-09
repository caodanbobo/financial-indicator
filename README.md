# 投资指标仪表盘

个人投资信号灯仪表盘 + 本地组合记账模块：每天抓 10Y 国债收益率 + 三个红利指数估值，按笔记规则算信号灯，生成无依赖静态页（手机可看）；组合模块只在本地运行，持仓数据绝不出本机。

## 本地运行

```bash
python3.13 -m venv .venv          # 已建好可跳过
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py          # 抓取 → 生成托管版 site/index.html（无持仓信息）
.venv/bin/python main.py --local  # 额外生成本地版 local/index.html（指标 + 组合模块）
```

- 托管版页面：`site/index.html`（GitHub Actions 每天自动发布，不含任何持仓信息）
- 本地版页面：`local/index.html`（含组合占比/再平衡建议，`local/` 已 gitignore，不会被提交）
- 历史快照：`history/YYYY-MM-DD.json`（抓取失败自动降级显示上次读数并标"数据陈旧"）

## 组合模块（每月核对日用一次）

1. 首次：`cp portfolio.example.yaml portfolio.yaml`，按文件内注释格式填入真实持仓
   （名称/平台/类别/金额/币种/更新日期；`layer: onshore` 参与再平衡，`frozen` 冻结层只显示）。
2. 每月核对日（每月第一个周末）：打开各平台 App 抄总市值进 `portfolio.yaml` 的 `amount`，改 `updated` 日期。
3. 跑 `.venv/bin/python main.py --local`，浏览器打开 `local/index.html` 看：
   各类别占比 vs 目标区间、偏离灯色（≥5pp 🟡 增量纠偏 / ≥10pp 🔴 允许卖出）、
   单一 ETF 40% 红线、超 35 天未更新的条目标灰、本月可投金额的分流建议。
4. 外币持仓（USD/JPY）按当天中行牌价自动折算；牌价很慢，结果缓存在 `history/fx_cache.json`，当天内复用。

**隐私红线**：`portfolio.yaml`、`local/` 均在 .gitignore，任何时候不要 `git add -f` 它们。

## 改阈值 / 规则

全部在 `config.yaml`：信号灯息差 X 与各档门槛、失真期月份、组合目标区间、红线、月投金额与默认分流比例。
纪律上只允许在校准窗口（每季财报季后 / 每年 4 月）修改。

## 托管（GitHub Pages）

`.github/workflows/daily.yml` 在**交易日每天 4 次**自动抓取并发布：北京时间 9:30 / 11:30 / 13:30 / 14:30（cron `30 1,3,5,6 * * 1-5`），也可在 Actions 页面手动触发。

- GitHub Actions 定时任务有 5–30 分钟抖动，属正常；首次 9:30 那趟拿到的是前一交易日的 T+1 估值，盘中几趟主要刷新近实时源（国际金银、CoinGecko、DefiLlama、Ahr999）。
- `history/` 每天每指标只保留最新一条（同日期覆盖），盘中多次跑不会污染趋势图。
- 汇率不在页面显示（仅本地组合模块折算用，fetcher 保留）。
- 金银比 / 10Y TIPS 显示历史分位（近 10 年 + 全历史，数据：新浪伦敦金银日线 2006 至今 / FRED DFII10 2003 至今，每次构建实时拉取）。

## 数据源与已知限制（详见阶段 0 报告）

- 10Y：`akshare.bond_zh_us_rate`（节假日 NaN 行已处理）
- 指数估值：`akshare.stock_zh_index_value_csindex`（中证官网，T+1，无 PB → 515180 的 PB 档暂未纳入判定）
- 汇率：`akshare.currency_boc_safe`（很慢且偶发 SSL，已加重试 + 本地缓存）
- 行情类东财接口对 Python 有 TLS 拦截，本项目的替代方案与代理处理见 `fetchers/network.py`
