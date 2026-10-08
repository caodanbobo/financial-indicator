# 投资指标仪表盘

个人投资信号灯仪表盘（MVP）：每天抓 10Y 国债收益率 + 三个红利指数估值，按笔记规则算信号灯，生成一个无依赖的静态页面（手机可看、适合截图分享）。

## 本地运行

```bash
python3.13 -m venv .venv          # 已建好可跳过
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py          # 抓取 → 生成 site/index.html
open site/index.html              # 或直接双击打开
```

- 历史快照：`history/YYYY-MM-DD.json`（每天一份，抓取失败时自动降级显示上次读数并标注"数据陈旧"）
- 探测脚本：`scripts/probe_sources.py`（阶段 0 数据源实测用，可重复跑）
- 实测结论：`阶段0_数据源实测报告.md`

## 改阈值 / 规则

全部在 `config.yaml`：息差常数 X、各档门槛、清仓线、失真期月份、指数代码与口径（股息率1/2、PE1/2）。
纪律上只允许在校准窗口（每季财报季后 / 每年 4 月）修改。

## 托管（GitHub Pages）

`.github/workflows/daily.yml` 每天北京时间约 09:07 自动抓取并发布，也可在 Actions 页面手动触发（workflow_dispatch）。

## 数据源与已知限制（详见阶段 0 报告）

- 10Y：`akshare.bond_zh_us_rate`（节假日 NaN 行已处理）
- 指数估值：`akshare.stock_zh_index_value_csindex`（中证官网，T+1，无 PB → 515180 的 PB 档暂未纳入判定）
- 行情类东财接口对 Python 有 TLS 拦截，本项目的替代方案与代理处理见 `fetchers/network.py`
