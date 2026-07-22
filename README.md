# GravityWell

面向同城或邻城、2～4 人约会的公平会面地点推荐 Agent。

当前阶段首先验证推荐是否真实、公平、可解释；正式视觉设计与分享卡片不在 MVP 范围内。

## 架构原则

- DeepSeek：判断新需求或继续调整，形成活动类别、场所形态与搜索计划，并在路线计算前做一次候选语义复核。
- 高德：地址、真实 POI、路线耗时、换乘、步行与天气。
- SearchProvider：可替换地调用智谱、博查或 Tavily，以公开网页补充场所氛围证据。
- Python 程序：执行确定性的多人公平评分。
- SQLite：只保存当前会话；采纳、重开或超时后删除。

## 本地运行

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m uvicorn app.main:app --reload
```

访问 `http://127.0.0.1:8000`。API 密钥只保存在被 Git 忽略的 `api.env`。

搜索功能是可选增强。`api.env` 可加入 `ZHIPU_API_KEY`、`BOCHA_API_KEY` 或
`TAVILY_API_KEY`，并用 `SEARCH_PROVIDER=auto` 自动选择；目前自动顺序为智谱、博查、
Tavily。搜索引擎只收到“城市 + 候选场所名 + 氛围偏好”，不会收到参与者出发地或路线。
同一场所结果默认缓存 30 天。

推荐链采用“受约束 Agent”结构：LLM 可以规划和反思，但最多触发一次重搜；高德事实、
营业时间、路线、天气、隐私、硬限制和数学评分始终由程序控制。活动类别使用稳定枚举，
氛围描述保留开放文本，不依靠不断扩大的形容词关键词表。

## 验证

运行不消耗 API 的自动测试：

```powershell
.venv\Scripts\python -m pytest -q
```

运行真实同城案例：

```powershell
.venv\Scripts\python scripts\smoke_test.py
```

运行真实上海—苏州邻城案例：

```powershell
.venv\Scripts\python scripts\smoke_test.py --intercity
```

比较已经配置的搜索引擎（不会显示密钥）：

```powershell
.venv\Scripts\python scripts\compare_search_providers.py "上海 上生新所 咖啡 安静 设计感"
```

更详细的模块说明见 `docs/ARCHITECTURE.md`，产品测试清单见 `docs/TEST_CASES.md`。
