# Aurora ⚡ 电路智能助教

一个「会讲、也会做」的模拟电子技术智能体：**学伴**（引导式答疑）+ **电路分析**（解题流水线）。
项目名取自「极光」——太阳的带电粒子流穿越太空，在磁场中化作流光。既是电，也是自然，还寓意「在迷惘中点亮知识」。

> 本项目侧重 **Agent 搭建与流程跑通**，不追求真实电路图识别。目标是精进 Agent 开发能力，为面试做准备。

## 技术栈

- **模型**：DeepSeek（OpenAI 兼容接口）
- **语言/工具**：Python + uv 管理环境
- **界面**：Streamlit（后续加入）
- **不依赖**：LangChain / LangGraph（第一版手写，理解本质）

## 目录结构

```
aurora/
├── src/aurora/
│   ├── config.py          # 读取配置、返回 DeepSeek 客户端
│   └── companion_v0.py    # 电路学伴 v0（纯提示词版）
├── scripts/
│   └── demo_companion.py  # 最小运行示例
├── .env.example           # 配置模板
└── pyproject.toml
```

## 快速开始

```bash
# 1. 配置密钥
cp .env.example .env        # Windows: copy .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY

# 2. 运行示例
uv run scripts/demo_companion.py
```

## 路线图

- [x] W1 学伴 v0：纯提示词对话（本阶段）
- [ ] W2 学伴 v1：接入课件 RAG + 第一版评测
- [ ] W3 电路分析：类型识别 Router + 分析模板 + 工具计算
- [ ] W4 评估报告 + Streamlit 界面 + 面试复盘
