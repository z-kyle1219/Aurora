# 意图分流离线回归

- Revision：`61edbf2` 加本次工作区意图分流改动。
- 判定：完整离线回归通过，包含 `RTR-E2E-002`。
- 命令：`.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --tb=no --basetemp <独立临时目录>`。
- 读数：135 passed, 5 deselected, 12 xfailed。
- 真实模型分类、生成质量和真实索引检索未执行。本次新增用例用替身响应验证分派和写回。
