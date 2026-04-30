# CHANGELOG

## 本次交付改动

### Web 控制台增强
- 在现有 `src/web_console.py` 基础上增强总览页，而非重写
- 新增“抖音直播状态”区域
  - 汇总监测中的抖音房间
  - 展示在线状态、录制状态、最近开始时间、最近时长、最近结果
- 新增“最近录制”区域
  - 展示最近 10 条完成/失败/中断记录
- 新增“录制文件”页
  - 支持按房间名筛选
  - 支持按日期范围筛选
  - 支持关键词搜索文件名/路径
  - 支持按时间/大小排序
- 新增顶部磁盘告警横幅
  - 展示下载目录所在磁盘使用率与剩余空间
  - `>80%` 黄色告警，`>90%` 红色告警
- 新增异常提示能力
  - 汇总 `recent_events` 中的 ERROR/CRITICAL
  - 显示最近 24h 错误次数
  - 新异常出现时前端弹出醒目提醒

### 后端数据增强
- `RuntimeState.completed_sessions` 增加录制时长字段
- `DownloadDirectoryCache` 文件项增加房间名推断与修改时间戳
- 新增 `/api/files` 接口供文件页筛选/排序使用
- `get_overview()` 输出增加：
  - `douyin`
  - `alerts`
  - `recent_recordings`
- 磁盘信息增加使用率百分比与告警级别

### 交付文档
- 新增 `RUNBOOK.md`
- 新增 `DELIVERY_CHECKLIST.md`
- 新增 `DONE.md`
