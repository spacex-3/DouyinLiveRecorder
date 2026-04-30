# DONE

已完成（第二轮）：
- [x] A1 抖音专属状态卡
- [x] A2 最近录制任务详情（最近 10 条）
- [x] A3 录制文件页 + 筛选/搜索/排序
- [x] A4 磁盘使用率显示与分级告警
- [x] A5 异常横幅 / toast / 24h 错误统计
- [x] B1 RUNBOOK.md
- [x] B2 DELIVERY_CHECKLIST.md
- [x] B3 CHANGELOG.md

已完成（第三轮）：
- [x] R1 保存 config.ini / URL_config.ini 后立即请求主循环重载配置
- [x] R2 config.ini 页面改为按 section 分组的列表 + 默认折叠模式
- [x] R3 正在录制会话新增“停止”按钮与 `/api/stop_recording`

已完成（第三轮补充 round3b）：
- [x] R3B-1 为 ffmpeg 录制任务注册/注销进程句柄
- [x] R3B-2 新增 `/api/pause_recording` 与 `/api/resume_recording`
- [x] R3B-3 正在录制会话支持 暂停 / 继续 / 停止 三级控制
- [x] R3B-4 暂停状态显示为“已暂停”，停止时支持先继续再优雅退出

已完成（第三轮补充 round3c）：
- [x] R3C-1 去掉 SIGSTOP / SIGCONT 暂停方案
- [x] R3C-2 暂停改为“结束当前录制段并保留 paused 状态”
- [x] R3C-3 继续改为“清除暂停阻塞，等待主循环重新拉起新录制段”
- [x] R3C-4 前端保持暂停 / 继续 / 停止三级按钮，但 paused 状态不再显示为 stop pending

已完成（第三轮补充 round3d）：
- [x] R3D-1 停止请求区分 stop / pause 两种原因，pause 仍保持手动继续
- [x] R3D-2 stop 仅阻塞一个轮询周期，之后恢复 URL 监测
- [x] R3D-3 下一次检测到直播在线时自动清除 stop 标记并重新录制
- [x] R3D-4 已计划录制的直播间新增“重新录制”按钮与 `/api/resume_url`

说明：
- 未执行 `git commit`
- 已完成 `python3 -m py_compile src/web_console.py main.py` 语法校验
