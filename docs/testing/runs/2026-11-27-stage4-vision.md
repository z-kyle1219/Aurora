# 图片输入离线验收

- 判定：阶段 4 离线回归 `Passed`；视觉模型识别质量 `Not Executed`。
- revision：`5f1f1e6` 工作区修改，尚未提交。
- 命令：`uv run pytest -q -p no:cacheprovider`；`node --check src/aurora/web/chat.js`；`git diff --check`。
- 读数：143 passed，11 xfailed，5 deselected；JavaScript 语法和 diff 检查通过。
- 未执行：真实视觉服务和课程图片评估；缺少视觉服务配置及冻结样例集。浏览器仅检查了聊天页上传入口布局。
