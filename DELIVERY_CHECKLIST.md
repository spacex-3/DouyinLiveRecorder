# DELIVERY CHECKLIST

- [x] 项目可通过 CLI 启动
- [x] 保留并增强现有内置 Web 控制台
- [x] 总览页新增抖音直播状态区
- [x] 总览页新增最近录制区（最近 10 条）
- [x] 总览页新增磁盘使用率告警横幅
- [x] 总览页新增异常提示 / 24h 错误统计
- [x] 新增“录制文件”页，支持筛选、搜索、排序
- [x] 未引入 Flask / FastAPI 等重框架
- [x] 代码改动集中在 `src/web_console.py`
- [x] 未修改 `main.py` 核心录制逻辑
- [x] 新增 `RUNBOOK.md`
- [x] 新增 `CHANGELOG.md`
- [x] 新增 `DONE.md`
- [x] 未执行 `git commit`
- [x] 已执行 `python3 -m py_compile src/web_console.py` 基本语法校验

## 建议交付前人工复核
- [ ] 打开 Web 面板检查总览页布局
- [ ] 在真实录制文件存在时验证文件筛选结果
- [ ] 用一条抖音地址验证状态卡与最近录制展示
- [ ] 人工确认 Docker / CLI / EXE 运行说明符合交付环境
