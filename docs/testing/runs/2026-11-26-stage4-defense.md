# 防御性分流离线回归

- Revision 为 `61edbf2` 加本次工作区改动。
- 完整离线回归通过，覆盖 `RTR-E2E-003` 和 `RAG-INT-005`，作为 DEF-06 关闭证据。
- 命令为 `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --tb=short --basetemp <独立临时目录>`。
- 读数为 141 passed, 5 deselected, 11 xfailed。
- 真实模型调用未执行。
