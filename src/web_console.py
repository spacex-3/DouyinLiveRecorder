# -*- coding: utf-8 -*-
from __future__ import annotations

import configparser
import heapq
import json
import os
import re
import shutil
import threading
import time
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from src.config_store import ensure_file, ensure_sections, load_ini, save_ini, atomic_write_text, read_text

YES_VALUES = {"1", "true", "yes", "y", "是", "on"}
SENSITIVE_PATTERNS = ("cookie", "token", "密码", "令牌", "授权码", "secret", "access_key", "api_key")
RESTART_REQUIRED_OPTIONS = {
    "language(zh_cn/en)",
    "是否跳过代理检测(是/否)",
    "是否启用Web控制台(是/否)",
    "Web控制台监听地址",
    "Web控制台端口",
}
CONFIG_SECTION_ORDER = ["录制设置", "推送配置", "Cookie", "Authorization", "账号密码"]
DEFAULT_WEB_OPTIONS = {
    "是否启用Web控制台(是/否)": "是",
    "Web控制台监听地址": "auto",
    "Web控制台端口": "18080",
    "Web控制台文件索引上限": "500",
    "Web控制台文件索引缓存秒数": "30",
}
URL_PATTERN = re.compile(r"(https?://)?(www\.)?[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(:\d+)?(/.*)?")
RECORD_NAME_PREFIX_PATTERN = re.compile(r"^序号\d+\s*")
DATE_FOLDER_PATTERN = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
FILE_TIME_SUFFIX_PATTERN = re.compile(r"[_-]?\d{4}-\d{2}-\d{2}(?:[_ T]\d{2}[-:]\d{2}[-:]\d{2})?(?:_\d+)?$")

HOT_RELOAD_NOTES = [
    "通过 Web 控制台保存 config.ini 或 URL_config.ini 后，会立即请求主循环重载配置，通常无需再等待完整轮询周期。",
    "大多数录制、推送、代理与下载参数会在主循环下一次重读配置后生效；已经启动中的 ffmpeg 录制任务不会被强行改成新的格式/分段策略，而是从下一次新开录制开始使用。",
    "language(zh_cn/en)、是否跳过代理检测(是/否)、Web 控制台监听地址/端口/开关属于启动级配置，修改后建议重启程序。",
    "Cookie、token、密码类字段默认脱敏；留空表示保持原值，勾选“清空”才会真正写成空字符串。",
]

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DouyinLiveRecorder Web 控制台</title>
  <style>
    :root {
      --bg: #0b1020;
      --panel: #111935;
      --panel-2: #172145;
      --text: #e8ecff;
      --muted: #9ca6d1;
      --line: rgba(255,255,255,0.09);
      --accent: #6fa8ff;
      --warn: #ffcc66;
      --danger: #ff7b7b;
      --ok: #7ee787;
      --shadow: 0 16px 40px rgba(0,0,0,0.25);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", sans-serif;
      background: linear-gradient(180deg, #0a0f1f, #0f1731 50%, #09101f 100%);
      color: var(--text);
    }
    a { color: var(--accent); }
    .layout {
      max-width: 1480px;
      margin: 0 auto;
      padding: 20px;
    }
    .topbar {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      margin-bottom: 16px;
      flex-wrap: wrap;
    }
    .title { font-size: 26px; font-weight: 700; }
    .subtitle { color: var(--muted); font-size: 13px; margin-top: 4px; }
    .nav {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .nav button, .btn {
      appearance: none;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.04);
      color: var(--text);
      padding: 10px 14px;
      border-radius: 10px;
      cursor: pointer;
      font-size: 14px;
    }
    .nav button.active, .btn.primary {
      background: rgba(111,168,255,0.18);
      border-color: rgba(111,168,255,0.45);
    }
    .btn.small { padding: 8px 12px; font-size: 13px; }
    .btn.danger {
      border-color: rgba(255,123,123,0.35);
      color: var(--danger);
    }
    .btn:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }
    .panel {
      display: none;
      background: rgba(12, 18, 38, 0.86);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(6px);
    }
    .panel.active { display: block; }
    .cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .card {
      padding: 14px;
      border-radius: 14px;
      background: linear-gradient(160deg, rgba(255,255,255,0.05), rgba(255,255,255,0.02));
      border: 1px solid var(--line);
    }
    .card .label { color: var(--muted); font-size: 12px; margin-bottom: 10px; }
    .card .value { font-size: 24px; font-weight: 700; word-break: break-word; }
    .card .hint { margin-top: 8px; color: var(--muted); font-size: 12px; line-height: 1.5; }
    .grid {
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 16px;
      margin-bottom: 16px;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-bottom: 16px;
    }
    .section {
      background: rgba(255,255,255,0.03);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      overflow: hidden;
    }
    .section h3 {
      margin: 0 0 12px;
      font-size: 17px;
    }
    .meta {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 12px;
      line-height: 1.6;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 10px 8px;
      text-align: left;
      vertical-align: top;
      word-break: break-word;
    }
    th { color: var(--muted); font-weight: 600; }
    tr:last-child td { border-bottom: none; }
    .table-wrap { overflow: auto; }
    .tag {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 12px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.04);
      margin-right: 6px;
      margin-bottom: 6px;
    }
    .tag.ok { color: var(--ok); border-color: rgba(126,231,135,0.25); }
    .tag.warn { color: var(--warn); border-color: rgba(255,204,102,0.25); }
    .tag.danger { color: var(--danger); border-color: rgba(255,123,123,0.25); }
    .tag.info { color: var(--accent); border-color: rgba(111,168,255,0.25); }
    .stack > * + * { margin-top: 12px; }
    .notes {
      padding-left: 18px;
      margin: 0;
      color: var(--muted);
      line-height: 1.7;
      font-size: 13px;
    }
    .toolbar {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 14px;
      flex-wrap: wrap;
    }
    .status {
      min-height: 22px;
      color: var(--muted);
      font-size: 13px;
    }
    .field {
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(255,255,255,0.02);
    }
    .field label {
      display: block;
      font-weight: 600;
      margin-bottom: 6px;
    }
    .field .field-meta {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.6;
      margin-bottom: 8px;
    }
    .field input[type="text"], .field input[type="number"], .field input[type="date"], .field textarea, .field select,
    .config-control input[type="text"], .config-control input[type="number"], .config-control textarea, .config-control select {
      width: 100%;
      padding: 10px 12px;
      border-radius: 10px;
      border: 1px solid rgba(255,255,255,0.12);
      background: rgba(8, 13, 28, 0.9);
      color: var(--text);
      font-size: 13px;
    }
    .field textarea, .config-control textarea { min-height: 92px; resize: vertical; }
    .filter-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }
    .filter-actions {
      display: flex;
      gap: 10px;
      margin-top: 12px;
      flex-wrap: wrap;
    }
    .muted { color: var(--muted); }
    .empty {
      color: var(--muted);
      padding: 14px;
      border: 1px dashed var(--line);
      border-radius: 12px;
      text-align: center;
      font-size: 13px;
    }
    .log-list {
      display: grid;
      gap: 10px;
      max-height: 460px;
      overflow: auto;
    }
    .log-item {
      padding: 10px 12px;
      border-radius: 10px;
      background: rgba(255,255,255,0.03);
      border: 1px solid var(--line);
      font-size: 13px;
    }
    .log-item .time { color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .log-item .source { color: var(--muted); font-size: 12px; margin-top: 4px; }
    .danger-text { color: var(--danger); }
    .ok-text { color: var(--ok); }
    .warn-text { color: var(--warn); }
    .textarea-large {
      width: 100%;
      min-height: 360px;
      border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.12);
      background: rgba(8, 13, 28, 0.94);
      color: var(--text);
      padding: 12px 14px;
      font-size: 13px;
      line-height: 1.6;
      resize: vertical;
    }
    .pill-row { margin-bottom: 8px; }
    .alert-stack {
      display: grid;
      gap: 12px;
      margin-bottom: 16px;
    }
    .alert-banner {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      padding: 14px 16px;
      border-radius: 14px;
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }
    .alert-banner.warn {
      background: rgba(255, 204, 102, 0.12);
      border-color: rgba(255, 204, 102, 0.35);
    }
    .alert-banner.danger {
      background: rgba(255, 123, 123, 0.13);
      border-color: rgba(255, 123, 123, 0.4);
    }
    .alert-title { font-weight: 700; margin-bottom: 4px; }
    .alert-text { font-size: 13px; line-height: 1.6; color: var(--text); }
    .toast-stack {
      position: fixed;
      top: 18px;
      right: 18px;
      z-index: 20;
      display: grid;
      gap: 10px;
      max-width: 360px;
    }
    .toast {
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      backdrop-filter: blur(8px);
      background: rgba(12, 18, 38, 0.96);
    }
    .toast.danger { border-color: rgba(255,123,123,0.45); }
    .toast.warn { border-color: rgba(255,204,102,0.4); }
    .toast.info { border-color: rgba(111,168,255,0.35); }
    .toast-title { font-size: 13px; font-weight: 700; margin-bottom: 4px; }
    .toast-message { font-size: 12px; line-height: 1.5; color: var(--muted); }
    .config-list {
      display: grid;
      gap: 12px;
    }
    .config-list-section {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(255,255,255,0.02);
      overflow: hidden;
    }
    .config-list-section.dirty {
      border-color: rgba(111,168,255,0.35);
    }
    .config-section-toggle {
      width: 100%;
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      padding: 14px 16px;
      border: none;
      background: transparent;
      color: var(--text);
      text-align: left;
      cursor: pointer;
    }
    .config-section-title {
      font-size: 15px;
      font-weight: 700;
      margin-bottom: 4px;
    }
    .config-section-count {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .config-section-toggle::after {
      content: '▸';
      color: var(--muted);
      font-size: 13px;
      margin-left: auto;
      transition: transform 0.15s ease;
    }
    .config-list-section.expanded .config-section-toggle::after {
      transform: rotate(90deg);
    }
    .config-section-body {
      display: none;
      border-top: 1px solid var(--line);
      padding: 0 12px 12px;
    }
    .config-list-section.expanded .config-section-body {
      display: block;
    }
    .config-list-header,
    .config-row {
      display: grid;
      grid-template-columns: minmax(220px, 280px) minmax(280px, 1fr) minmax(220px, 340px);
      gap: 12px;
      align-items: start;
    }
    .config-list-header {
      padding: 10px 8px;
      color: var(--muted);
      font-size: 12px;
      border-bottom: 1px solid var(--line);
    }
    .config-row {
      padding: 12px 8px;
      border-bottom: 1px solid rgba(255,255,255,0.06);
    }
    .config-row:last-child {
      border-bottom: none;
    }
    .config-row.dirty {
      background: rgba(111,168,255,0.05);
    }
    .config-key {
      font-size: 13px;
      font-weight: 600;
      line-height: 1.6;
    }
    .config-desc {
      font-size: 12px;
      color: var(--muted);
      line-height: 1.6;
    }
    .config-control .inline-check {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-top: 8px;
      font-size: 12px;
      color: var(--muted);
    }
    .config-control textarea[data-role="value"] {
      min-height: 72px;
    }
    @media (max-width: 1100px) {
      .grid, .grid-2 { grid-template-columns: 1fr; }
      .config-list-header,
      .config-row {
        grid-template-columns: 1fr;
      }
      .config-list-header { display: none; }
    }
  </style>
</head>
<body>
  <div class="layout">
    <div class="topbar">
      <div>
        <div class="title">DouyinLiveRecorder Web 控制台</div>
        <div class="subtitle" id="subtitle">正在加载状态...</div>
      </div>
      <div class="nav">
        <button data-panel="dashboard" class="active">总览</button>
        <button data-panel="files">录制文件</button>
        <button data-panel="config">config.ini</button>
        <button data-panel="urls">URL_config.ini</button>
      </div>
    </div>

    <div id="global-alert-root"></div>

    <div id="dashboard" class="panel active">
      <div class="toolbar">
        <div class="status" id="overview-status"></div>
        <div>
          <button class="btn small" id="refresh-overview">立即刷新</button>
        </div>
      </div>
      <div id="overview-root"></div>
    </div>

    <div id="files" class="panel">
      <div class="toolbar">
        <div class="status" id="files-status">支持按房间名、日期范围、关键词筛选，并可按大小/时间排序。</div>
        <div>
          <button class="btn small" id="refresh-files">刷新文件索引</button>
        </div>
      </div>
      <div class="section" style="margin-bottom:16px;">
        <h3>筛选 / 搜索</h3>
        <div class="meta">基于当前下载目录缓存结果筛选；若文件量很多，页面会提示缓存截断情况。</div>
        <div class="filter-grid">
          <div class="field">
            <label for="file-room-filter">房间名</label>
            <input id="file-room-filter" type="text" placeholder="例如：主播名 / 房间目录名">
          </div>
          <div class="field">
            <label for="file-keyword-filter">关键词</label>
            <input id="file-keyword-filter" type="text" placeholder="搜索文件名或相对路径">
          </div>
          <div class="field">
            <label for="file-start-filter">开始日期</label>
            <input id="file-start-filter" type="date">
          </div>
          <div class="field">
            <label for="file-end-filter">结束日期</label>
            <input id="file-end-filter" type="date">
          </div>
          <div class="field">
            <label for="file-sort-filter">排序方式</label>
            <select id="file-sort-filter">
              <option value="time_desc">按时间：最新在前</option>
              <option value="time_asc">按时间：最旧在前</option>
              <option value="size_desc">按大小：最大在前</option>
              <option value="size_asc">按大小：最小在前</option>
            </select>
          </div>
        </div>
        <div class="filter-actions">
          <button class="btn primary small" id="apply-file-filter">应用筛选</button>
          <button class="btn small" id="reset-file-filter">清空筛选</button>
        </div>
      </div>
      <div class="section">
        <h3>录制文件列表</h3>
        <div class="meta" id="file-meta">正在读取文件索引...</div>
        <div id="files-root"></div>
      </div>
    </div>

    <div id="config" class="panel">
      <div class="toolbar">
        <div class="status" id="config-status">敏感字段默认脱敏；留空代表保持原值。</div>
        <div>
          <button class="btn small" id="reload-config">重新读取</button>
          <button class="btn primary small" id="save-config">保存 config.ini</button>
        </div>
      </div>
      <div class="section" style="margin-bottom:16px;">
        <h3>热更新说明</h3>
        <ul class="notes" id="config-notes"></ul>
      </div>
      <div id="config-root"></div>
    </div>

    <div id="urls" class="panel">
      <div class="toolbar">
        <div class="status" id="url-status">每行一个直播间；支持 # 注释停录，支持 “画质,URL,备注”。</div>
        <div>
          <button class="btn small" id="reload-urls">重新读取</button>
          <button class="btn primary small" id="save-urls">保存 URL_config.ini</button>
        </div>
      </div>
      <div class="grid">
        <div class="section">
          <h3>原始内容编辑区</h3>
          <div class="meta">这里直接编辑 config/URL_config.ini 原始文本；保存后会原子写入文件，不需要额外开发服务器，也不需要重启主程序。</div>
          <textarea id="url-editor" class="textarea-large" spellcheck="false"></textarea>
        </div>
        <div class="stack">
          <div class="section">
            <h3>热更新说明</h3>
            <ul class="notes" id="url-notes"></ul>
          </div>
          <div class="section">
            <h3>解析预览</h3>
            <div id="url-preview"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="toast-stack" id="toast-root"></div>

  <script>
    const state = {
      activePanel: 'dashboard',
      configData: null,
      overviewData: null,
      urlData: null,
      filesData: null,
      timer: null,
      lastErrorKey: '',
      configExpandedSections: new Set(),
      configDirtySections: new Set(),
    };

    const panels = document.querySelectorAll('.panel');
    const navButtons = document.querySelectorAll('.nav button');

    function escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    function formatTime(ts) {
      if (!ts) return '-';
      const date = new Date(ts);
      if (Number.isNaN(date.getTime())) return ts;
      return date.toLocaleString();
    }

    function formatBytes(bytes) {
      if (bytes === null || bytes === undefined || Number.isNaN(Number(bytes))) return '-';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let size = Number(bytes);
      let unit = 0;
      while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
      }
      return `${size.toFixed(size >= 100 || unit === 0 ? 0 : 2)} ${units[unit]}`;
    }

    function formatDuration(seconds) {
      const total = Math.max(0, Math.floor(Number(seconds || 0)));
      const h = Math.floor(total / 3600);
      const m = Math.floor((total % 3600) / 60);
      const s = total % 60;
      return [h, m, s].map(v => String(v).padStart(2, '0')).join(':');
    }

    function formatPercent(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
      const num = Number(value);
      return `${num % 1 === 0 ? num.toFixed(0) : num.toFixed(1)}%`;
    }

    function levelClass(level) {
      const text = String(level || '').toUpperCase();
      if (['ERROR', 'CRITICAL'].includes(text)) return 'danger';
      if (['WARNING', 'WARN'].includes(text)) return 'warn';
      if (['SUCCESS', 'OK'].includes(text)) return 'ok';
      return 'info';
    }

    async function fetchJson(url, options = {}) {
      const response = await fetch(url, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || `请求失败: ${response.status}`);
      }
      return data;
    }

    function renderTable(columns, rows) {
      if (!rows || !rows.length) {
        return '<div class="empty">暂无数据</div>';
      }
      const head = columns.map(col => `<th>${escapeHtml(col.title)}</th>`).join('');
      const body = rows.map(row => {
        const cells = columns.map(col => `<td>${typeof col.render === 'function' ? col.render(row) : escapeHtml(row[col.key] ?? '-')}</td>`).join('');
        return `<tr>${cells}</tr>`;
      }).join('');
      return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    }

    function renderLogList(items, emptyText = '暂无日志') {
      if (!items || !items.length) return `<div class="empty">${escapeHtml(emptyText)}</div>`;
      return `<div class="log-list">${items.map(item => `
        <div class="log-item">
          <div class="time">${formatTime(item.timestamp)} · <span class="${levelClass(item.level)}-text">${escapeHtml(item.level)}</span></div>
          <div>${escapeHtml(item.message)}</div>
          ${item.source ? `<div class="source">${escapeHtml(item.source)}</div>` : ''}
        </div>`).join('')}</div>`;
    }

    function showToast(title, message, tone = 'danger') {
      const root = document.getElementById('toast-root');
      const toast = document.createElement('div');
      toast.className = `toast ${tone}`;
      toast.innerHTML = `<div class="toast-title">${escapeHtml(title)}</div><div class="toast-message">${escapeHtml(message)}</div>`;
      root.prepend(toast);
      window.setTimeout(() => toast.remove(), 7000);
    }

    function renderGlobalAlerts(data) {
      const root = document.getElementById('global-alert-root');
      const disk = data.disk || {};
      const alerts = data.alerts || {};
      const banners = [];

      if (disk.alert_level === 'warn' || disk.alert_level === 'danger') {
        banners.push(`
          <div class="alert-banner ${disk.alert_level}">
            <div>
              <div class="alert-title">磁盘容量告警</div>
              <div class="alert-text">下载目录所在磁盘已使用 ${formatPercent(disk.used_percent)}，剩余 ${escapeHtml(disk.free_human || '-')}（约 ${escapeHtml(String(disk.free_gb ?? '-'))} GB）。建议尽快清理旧录像或调整下载盘。</div>
            </div>
          </div>`);
      }

      if (alerts.latest_error) {
        const latest = alerts.latest_error;
        banners.push(`
          <div class="alert-banner danger">
            <div>
              <div class="alert-title">异常横幅</div>
              <div class="alert-text">最近 24 小时共有 ${escapeHtml(String(alerts.error_count_24h || 0))} 次错误。最新异常发生于 ${formatTime(latest.timestamp)}：${escapeHtml(latest.message || '-')}</div>
            </div>
          </div>`);
        const errorKey = latest.error_key || `${latest.timestamp}|${latest.message}|${latest.source || ''}`;
        if (state.lastErrorKey && state.lastErrorKey !== errorKey) {
          showToast('检测到新的 ERROR 事件', `${formatTime(latest.timestamp)} · ${latest.message || '请查看总览详情'}`, 'danger');
        }
        state.lastErrorKey = errorKey;
      } else {
        state.lastErrorKey = '';
      }

      root.innerHTML = banners.length ? `<div class="alert-stack">${banners.join('')}</div>` : '';
    }

    async function requestRecordingControl(actionPath, recordName, recordUrl, loadingText, successText, toastTitle, toastTone = 'info') {
      const status = document.getElementById('overview-status');
      status.textContent = loadingText;
      try {
        await fetchJson(actionPath, {
          method: 'POST',
          body: JSON.stringify({ record_name: recordName, record_url: recordUrl }),
        });
        status.textContent = successText;
        showToast(toastTitle, recordName || recordUrl || '录制任务', toastTone);
        await loadOverview();
      } catch (error) {
        status.textContent = error.message;
        showToast(`${toastTitle}失败`, error.message, 'danger');
      }
    }

    async function stopRecording(recordName, recordUrl) {
      await requestRecordingControl(
        '/api/stop_recording',
        recordName,
        recordUrl,
        '正在请求停止录制...',
        '已请求停止录制，等待录制线程优雅结束并保存片段...',
        '已发送停止请求',
        'warn'
      );
    }

    async function pauseRecording(recordName, recordUrl) {
      await requestRecordingControl(
        '/api/pause_recording',
        recordName,
        recordUrl,
        '正在请求暂停录制...',
        '录制任务已暂停。',
        '已暂停录制',
        'warn'
      );
    }

    async function resumeRecording(recordName, recordUrl) {
      await requestRecordingControl(
        '/api/resume_recording',
        recordName,
        recordUrl,
        '正在请求继续录制...',
        '录制任务已继续。',
        '已继续录制',
        'info'
      );
    }

    async function resumeUrl(recordUrl) {
      await requestRecordingControl(
        '/api/resume_url',
        '',
        recordUrl,
        '正在恢复该 URL 的录制...',
        '该 URL 已重新启用，主循环会继续监测并在开播时自动录制。',
        '已恢复 URL 录制',
        'info'
      );
    }

    function bindOverviewActions(root) {
      root.querySelectorAll('[data-action="pause-recording"]').forEach(button => {
        button.addEventListener('click', () => {
          if (button.disabled) return;
          pauseRecording(button.dataset.recordName || '', button.dataset.recordUrl || '');
        });
      });
      root.querySelectorAll('[data-action="resume-recording"]').forEach(button => {
        button.addEventListener('click', () => {
          if (button.disabled) return;
          resumeRecording(button.dataset.recordName || '', button.dataset.recordUrl || '');
        });
      });
      root.querySelectorAll('[data-action="stop-recording"]').forEach(button => {
        button.addEventListener('click', () => {
          if (button.disabled) return;
          stopRecording(button.dataset.recordName || '', button.dataset.recordUrl || '');
        });
      });
      root.querySelectorAll('[data-action="resume-url"]').forEach(button => {
        button.addEventListener('click', () => {
          if (button.disabled) return;
          resumeUrl(button.dataset.recordUrl || '');
        });
      });
    }

    function renderOverview(data) {
      const root = document.getElementById('overview-root');
      const subtitle = document.getElementById('subtitle');
      const summary = data.summary || {};
      const runtime = data.runtime || {};
      const disk = data.disk || {};
      const files = data.files || {};
      const planned = (data.planned || {}).entries || [];
      const active = runtime.active_sessions || [];
      const logs = runtime.recent_logs || [];
      const events = runtime.recent_events || [];
      const douyin = data.douyin || {};
      const douyinStats = douyin.stats || {};
      const douyinRooms = douyin.rooms || [];
      const recentRecordings = data.recent_recordings || [];
      const alerts = data.alerts || {};
      const errorItems = alerts.recent_errors || [];

      renderGlobalAlerts(data);
      subtitle.textContent = `监听 ${data.service?.access_url || '-'} · 启动于 ${formatTime(data.service?.started_at)} · 版本 ${summary.version || '-'}`;

      const cards = [
        { label: '当前录制格式', value: summary.video_save_type || '-', hint: `默认画质：${summary.video_record_quality || '-'}` },
        { label: '已计划直播间', value: planned.length, hint: `其中停用 ${planned.filter(item => !item.enabled).length} 条` },
        { label: '抖音监测房间', value: douyinStats.total || 0, hint: '仅统计当前启用或运行中抖音房间' },
        { label: '在线 / 录制中', value: `${douyinStats.online || 0} / ${douyinStats.recording || 0}`, hint: `等待 ${douyinStats.waiting || 0} · 异常 ${douyinStats.abnormal || 0}` },
        { label: '24h 异常', value: alerts.error_count_24h || 0, hint: errorItems[0] ? `最新：${formatTime(errorItems[0].timestamp)}` : '最近 24h 无 ERROR' },
        { label: '磁盘使用率', value: formatPercent(disk.used_percent), hint: `已用 ${disk.used_human || '-'} / 总计 ${disk.total_human || '-'}` },
        { label: '磁盘剩余', value: disk.free_human || '-', hint: `约 ${disk.free_gb ?? '-'} GB` },
        { label: '运行时长', value: formatDuration(summary.uptime_seconds || 0), hint: `最近主循环读取 ${summary.last_config_scan_at ? formatTime(summary.last_config_scan_at) : '尚未记录'}` },
        { label: '代理 / 错误', value: `${summary.use_proxy ? '代理开' : '代理关'} / ${summary.error_count || 0}`, hint: summary.global_proxy ? '检测到全局代理' : '未检测到全局代理' },
      ].map(card => `
        <div class="card">
          <div class="label">${escapeHtml(card.label)}</div>
          <div class="value">${escapeHtml(card.value)}</div>
          <div class="hint">${escapeHtml(card.hint || '')}</div>
        </div>`).join('');

      const plannedTable = renderTable([
        { title: '状态', render: row => {
          if (!row.enabled) return '<span class="tag warn">已注释</span>';
          if (row.stop_requested && row.has_active_session && row.stop_reason !== 'pause') return '<span class="tag warn">停止中</span>';
          if (row.stop_requested && row.stop_reason === 'pause') return '<span class="tag warn">已暂停</span>';
          if (row.stop_requested) return '<span class="tag warn">已停止</span><div class="muted">下次检测到开播时会自动恢复</div>';
          return '<span class="tag ok">启用</span>';
        } },
        { title: '画质', key: 'quality' },
        { title: '直播间 / URL', render: row => `<div>${escapeHtml(row.url || '-')}</div>${row.anchor_name ? `<div class="muted">备注/主播：${escapeHtml(row.anchor_name)}</div>` : ''}` },
        { title: '操作', render: row => {
          if (row.enabled && row.stop_requested && row.stop_reason === 'stop' && !row.has_active_session) {
            return `<button class="btn small" data-action="resume-url" data-record-url="${escapeHtml(row.url || '')}">重新录制</button>`;
          }
          return '<span class="muted">—</span>';
        } },
      ], planned.slice(0, 80));

      const activeTable = renderTable([
        { title: '录制对象', key: 'record_name' },
        { title: '状态', render: row => {
          if (row.control_state === 'paused') return `<span class="tag warn">已暂停</span><span class="tag">${escapeHtml(row.save_type || '-')}</span>`;
          if (row.stop_requested) return `<span class="tag warn">停止中</span><span class="tag">${escapeHtml(row.save_type || '-')}</span>`;
          return `<span class="tag ok">录制中</span><span class="tag">${escapeHtml(row.save_type || '-')}</span>`;
        } },
        { title: '时长', render: row => formatDuration(row.duration_seconds || 0) },
        { title: '输出', render: row => `<div>${escapeHtml(row.save_file_path || '-')}</div><div class="muted">${escapeHtml(row.record_url || '')}</div>` },
        { title: '操作', render: row => {
          const attrs = `data-record-name="${escapeHtml(row.record_name || '')}" data-record-url="${escapeHtml(row.record_url || '')}"`;
          const stopButton = `<button class="btn small danger" data-action="stop-recording" ${attrs} ${row.stop_requested && row.control_state !== 'paused' ? 'disabled' : ''}>${row.stop_requested && row.control_state !== 'paused' ? '停止中' : '停止'}</button>`;
          if (row.stop_requested && row.control_state !== 'paused') return stopButton;
          const controls = [];
          if (row.process_registered) {
            if (row.control_state === 'paused') {
              controls.push(`<button class="btn small" data-action="resume-recording" ${attrs}>继续</button>`);
            } else {
              controls.push(`<button class="btn small" data-action="pause-recording" ${attrs}>暂停</button>`);
            }
          } else if (row.control_state === 'paused') {
            controls.push(`<button class="btn small" data-action="resume-recording" ${attrs}>继续</button>`);
          }
          controls.push(stopButton);
          return controls.join(' ');
        } },
      ], active);

      const douyinTable = renderTable([
        { title: '房间 / 主播', render: row => `<div>${escapeHtml(row.room_name || '-')}</div><div class="muted">${escapeHtml(row.url || '')}</div>` },
        { title: '在线状态', render: row => `<span class="tag ${row.online_status === '在线' ? 'ok' : row.online_status === '状态未知' ? 'danger' : 'warn'}">${escapeHtml(row.online_status || '-')}</span>` },
        { title: '录制状态', render: row => `<span class="tag ${row.status_tone || 'info'}">${escapeHtml(row.record_status || '-')}</span>` },
        { title: '最近开始', render: row => formatTime(row.last_started_at) },
        { title: '最近时长', render: row => formatDuration(row.last_duration_seconds || 0) },
        { title: '最近结果', render: row => `<span class="tag ${row.last_result_tone || 'info'}">${escapeHtml(row.last_result_label || '-')}</span>` },
      ], douyinRooms);

      const recentTable = renderTable([
        { title: '房间名', render: row => `<div>${escapeHtml(row.room_name || row.record_name || '-')}</div><div class="muted">${escapeHtml(row.record_name || '')}</div>` },
        { title: '开始时间', render: row => `<div>${formatTime(row.started_at)}</div><div class="muted">结束：${formatTime(row.ended_at)}</div>` },
        { title: '时长', render: row => formatDuration(row.duration_seconds || 0) },
        { title: '状态', render: row => `<span class="tag ${row.result_tone || 'info'}">${escapeHtml(row.result_label || '-')}</span>` },
        { title: '文件 / 备注', render: row => `<div>${escapeHtml(row.save_file_path || '-')}</div>${row.note ? `<div class="muted">${escapeHtml(row.note)}</div>` : ''}` },
      ], recentRecordings);

      const fileTable = renderTable([
        { title: '房间名', render: row => `<div>${escapeHtml(row.room_name || '-')}</div><div class="muted">${escapeHtml(row.modified_at || '')}</div>` },
        { title: '文件', render: row => `<div>${escapeHtml(row.name || '-')}</div><div class="muted">${escapeHtml(row.relative_path || '')}</div>` },
        { title: '大小', render: row => formatBytes(row.size_bytes) },
      ], (files.entries || []).slice(0, 12));

      const fileMeta = [];
      if (files.status_message) fileMeta.push(files.status_message);
      if (files.truncated) fileMeta.push(`仅缓存最近 ${files.cached_count} 个文件，完整筛选请切到“录制文件”页面`);
      if (files.scanned_at) fileMeta.push(`文件索引更新时间：${formatTime(files.scanned_at)}`);

      root.innerHTML = `
        <div class="cards">${cards}</div>
        <div class="grid">
          <div class="section">
            <h3>抖音直播状态</h3>
            <div class="meta">当前启用中的抖音直播间会优先展示；若房间最近一次录制出错，会标记为“异常”。</div>
            <div class="pill-row">
              <span class="tag info">监测中：${escapeHtml(String(douyinStats.total || 0))}</span>
              <span class="tag ok">在线：${escapeHtml(String(douyinStats.online || 0))}</span>
              <span class="tag ok">录制中：${escapeHtml(String(douyinStats.recording || 0))}</span>
              <span class="tag warn">等待中：${escapeHtml(String(douyinStats.waiting || 0))}</span>
              <span class="tag danger">异常：${escapeHtml(String(douyinStats.abnormal || 0))}</span>
            </div>
            ${douyinTable}
          </div>
          <div class="section">
            <h3>值守摘要</h3>
            <div class="meta">${escapeHtml(summary.web_hint || '')}</div>
            <div class="pill-row">
              <span class="tag info">抖音 Cookie：${summary.has_douyin_cookie ? '已配置' : '未配置'}</span>
              <span class="tag ${summary.split_video_by_time ? 'ok' : 'warn'}">分段录制：${summary.split_video_by_time ? `开启 / ${escapeHtml(summary.split_time || '-') } 秒` : '关闭'}</span>
              <span class="tag ${disk.free_gb !== null && Number(disk.free_gb) <= Number(summary.disk_space_limit_gb || 0) ? 'danger' : 'ok'}">剩余阈值：${escapeHtml(summary.disk_space_limit_gb || '-')} GB</span>
              <span class="tag ${summary.create_time_file ? 'ok' : 'info'}">时间字幕：${summary.create_time_file ? '开启' : '关闭'}</span>
            </div>
            <div class="stack">
              <div><strong>当前下载路径：</strong><div class="muted">${escapeHtml(summary.download_path || '-')}</div></div>
              <div><strong>Web 控制台：</strong><div class="muted">${escapeHtml(data.service?.access_url || '-')}</div></div>
              <div><strong>监测循环：</strong><div class="muted">${escapeHtml(String(summary.delay_default || '-'))} 秒</div></div>
              <div><strong>并发线程上限：</strong><div class="muted">${escapeHtml(String(summary.max_request || '-'))}</div></div>
            </div>
            <ul class="notes" style="margin-top:12px;">
              <li>通过 Web 面板保存 config.ini / URL_config.ini 后，会立即请求主循环重载配置。</li>
              <li>正在录制时，可直接点击“停止”按钮，录制线程会尝试优雅结束并先保存已有片段。</li>
              <li>录制格式、保存路径、分段、转 MP4、推送参数等大多会在后续新任务中自动使用新配置。</li>
            </ul>
          </div>
        </div>
        <div class="grid-2">
          <div class="section">
            <h3>最近录制</h3>
            <div class="meta">展示最近 10 条录制记录，便于快速判断成功 / 失败 / 中断情况。</div>
            ${recentTable}
          </div>
          <div class="section">
            <h3>异常提示</h3>
            <div class="meta">最近 24 小时错误次数：${escapeHtml(String(alerts.error_count_24h || 0))}。出现新 ERROR 事件时，页面顶部会出现红色横幅和提醒。</div>
            ${renderLogList(errorItems, '最近没有 ERROR 事件')}
          </div>
        </div>
        <div class="grid-2">
          <div class="section">
            <h3>录制文件速览</h3>
            <div class="meta">${escapeHtml(fileMeta.join(' · ') || '显示最新录制文件；更多筛选请切到“录制文件”页面。')}</div>
            ${fileTable}
          </div>
          <div class="section">
            <h3>已计划录制的直播间 / URL</h3>
            <div class="meta">显示前 80 条；完整编辑请切到 URL_config.ini 页面。</div>
            ${plannedTable}
          </div>
        </div>
        <div class="grid-2">
          <div class="section">
            <h3>正在录制会话</h3>
            <div class="meta">当前实际启动中的录制会话，可直接发送停止请求。</div>
            ${activeTable}
          </div>
          <div class="section">
            <h3>最近日志</h3>
            ${renderLogList(logs.slice(0, 12), '暂无日志')}
          </div>
        </div>
        <div class="grid-2">
          <div class="section">
            <h3>关键事件</h3>
            ${renderLogList(events.slice(0, 12), '暂无关键事件')}
          </div>
          <div class="section">
            <h3>磁盘 / 目录说明</h3>
            <div class="stack">
              <div><strong>下载目录：</strong><div class="muted">${escapeHtml(disk.display_root || summary.download_path || '-')}</div></div>
              <div><strong>磁盘状态：</strong><div class="muted">使用率 ${formatPercent(disk.used_percent)}，剩余 ${escapeHtml(disk.free_human || '-')}</div></div>
              <div><strong>目录说明：</strong><div class="muted">${escapeHtml(disk.status_message || '目录状态正常，可继续值守录制。')}</div></div>
            </div>
          </div>
        </div>
      `;
      bindOverviewActions(root);
    }

    function configFieldDescription(field) {
      const parts = [];
      if (field.sensitive) parts.push('敏感字段，默认脱敏显示');
      if (field.restart_required) {
        parts.push('修改后建议重启程序');
      } else {
        parts.push('保存后会立即请求主循环重载');
      }
      if (field.choices && field.choices.length) {
        parts.push(`可选：${field.choices.join(' / ')}`);
      }
      if (field.has_value && field.sensitive) {
        parts.push('当前已有保存值');
      }
      if (field.hint) {
        parts.push(field.hint);
      }
      return parts.join('；');
    }

    function renderConfigFieldControl(section, field) {
      const choices = (field.choices || []).map(choice => `<option value="${escapeHtml(choice)}" ${String(choice) === String(field.value) ? 'selected' : ''}>${escapeHtml(choice)}</option>`).join('');
      if (field.sensitive) {
        return `
          <div class="config-control">
            <textarea data-role="value" placeholder="${escapeHtml(field.placeholder || '留空表示保持原值；若要清空，请勾选下方选项。')}"></textarea>
            <label class="inline-check"><input type="checkbox" data-role="clear-sensitive"> 清空已保存值</label>
          </div>`;
      }
      if (field.type === 'select') {
        return `<div class="config-control"><select data-role="value">${choices}</select></div>`;
      }
      if (field.type === 'textarea') {
        return `<div class="config-control"><textarea data-role="value">${escapeHtml(field.value || '')}</textarea></div>`;
      }
      if (field.type === 'number') {
        return `<div class="config-control"><input type="number" data-role="value" value="${escapeHtml(field.value || '')}"></div>`;
      }
      return `<div class="config-control"><input type="text" data-role="value" value="${escapeHtml(field.value || '')}"></div>`;
    }

    function findConfigSection(sectionName) {
      return Array.from(document.querySelectorAll('.config-list-section')).find(node => node.dataset.section === sectionName) || null;
    }

    function setConfigSectionExpanded(sectionName, expanded) {
      const section = findConfigSection(sectionName);
      if (!section) return;
      section.classList.toggle('expanded', expanded);
      if (expanded) {
        state.configExpandedSections.add(sectionName);
      } else {
        state.configExpandedSections.delete(sectionName);
      }
    }

    function markConfigSectionDirty(sectionName) {
      state.configDirtySections.add(sectionName);
      const section = findConfigSection(sectionName);
      if (section) section.classList.add('dirty');
      setConfigSectionExpanded(sectionName, true);
    }

    function bindConfigInteractions() {
      const root = document.getElementById('config-root');
      root.querySelectorAll('[data-action="toggle-config-section"]').forEach(button => {
        button.addEventListener('click', () => {
          const sectionName = button.dataset.section || '';
          const wrapper = button.closest('.config-list-section');
          setConfigSectionExpanded(sectionName, !(wrapper && wrapper.classList.contains('expanded')));
        });
      });
      root.querySelectorAll('.config-row').forEach(row => {
        const markDirty = () => {
          row.classList.add('dirty');
          markConfigSectionDirty(row.dataset.section || '');
        };
        row.querySelectorAll('[data-role="value"]').forEach(node => {
          node.addEventListener(node.tagName === 'SELECT' ? 'change' : 'input', markDirty);
        });
        row.querySelectorAll('[data-role="clear-sensitive"]').forEach(node => {
          node.addEventListener('change', markDirty);
        });
      });
    }

    function renderConfig(data) {
      state.configData = data;
      state.configDirtySections.clear();
      document.getElementById('config-notes').innerHTML = (data.notes || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
      const root = document.getElementById('config-root');
      const sections = data.sections || [];
      if (!sections.length) {
        root.innerHTML = '<div class="empty">未读取到配置内容</div>';
        return;
      }
      root.innerHTML = `<div class="config-list">${sections.map(section => {
        const expanded = state.configExpandedSections.has(section.name);
        return `
          <div class="config-list-section ${expanded ? 'expanded' : ''}" data-section="${escapeHtml(section.name)}">
            <button type="button" class="config-section-toggle" data-action="toggle-config-section" data-section="${escapeHtml(section.name)}">
              <div>
                <div class="config-section-title">${escapeHtml(section.name)}</div>
                <div class="muted">${escapeHtml(section.description || '')}</div>
              </div>
              <div class="config-section-count">${escapeHtml(String(section.field_count || (section.fields || []).length))} 项</div>
            </button>
            <div class="config-section-body">
              <div class="config-list-header">
                <div>配置项</div>
                <div>值</div>
                <div>说明</div>
              </div>
              ${(section.fields || []).map(field => `
                <div class="config-row"
                  data-section="${escapeHtml(section.name)}"
                  data-option="${escapeHtml(field.option)}"
                  data-sensitive="${field.sensitive ? '1' : '0'}">
                  <div class="config-key">${escapeHtml(field.option)}</div>
                  <div>${renderConfigFieldControl(section, field)}</div>
                  <div class="config-desc">
                    ${field.sensitive ? '<span class="tag warn">敏感字段</span>' : ''}
                    ${field.restart_required ? '<span class="tag danger">建议重启</span>' : '<span class="tag ok">支持立即重载 / 新任务生效</span>'}
                    ${field.has_value && field.sensitive ? '<span class="tag info">当前已设置</span>' : ''}
                    <div>${escapeHtml(configFieldDescription(field))}</div>
                  </div>
                </div>`).join('')}
            </div>
          </div>`;
      }).join('')}</div>`;
      bindConfigInteractions();
    }

    function collectConfigPayload() {
      const payload = { sections: {} };
      document.querySelectorAll('#config-root .config-row').forEach(field => {
        const section = field.dataset.section;
        const option = field.dataset.option;
        payload.sections[section] = payload.sections[section] || {};
        const isSensitive = field.dataset.sensitive === '1';
        const valueNode = field.querySelector('[data-role="value"]');
        const clearNode = field.querySelector('[data-role="clear-sensitive"]');
        if (isSensitive) {
          const raw = (valueNode?.value || '').trim();
          const clear = Boolean(clearNode?.checked);
          let mode = 'keep';
          let value = '';
          if (clear) {
            mode = 'clear';
          } else if (raw) {
            mode = 'replace';
            value = raw;
          }
          payload.sections[section][option] = { mode, value };
        } else {
          payload.sections[section][option] = { value: valueNode?.value ?? '' };
        }
      });
      return payload;
    }

    function renderUrlPreview(data) {
      const root = document.getElementById('url-preview');
      const entries = data.entries || [];
      root.innerHTML = renderTable([
        { title: '状态', render: row => {
          if (!row.enabled) return '<span class="tag warn">注释</span>';
          if (row.stop_requested && row.stop_reason === 'pause') return '<span class="tag warn">已暂停</span>';
          if (row.stop_requested) return '<span class="tag warn">已停止</span>';
          return '<span class="tag ok">启用</span>';
        } },
        { title: '画质', key: 'quality' },
        { title: '地址 / 备注', render: row => `<div>${escapeHtml(row.url || '-')}</div>${row.anchor_name ? `<div class="muted">${escapeHtml(row.anchor_name)}</div>` : ''}` },
      ], entries.slice(0, 100));
    }

    function getFileFilters() {
      return {
        room: document.getElementById('file-room-filter').value.trim(),
        keyword: document.getElementById('file-keyword-filter').value.trim(),
        start_date: document.getElementById('file-start-filter').value,
        end_date: document.getElementById('file-end-filter').value,
        sort: document.getElementById('file-sort-filter').value || 'time_desc',
      };
    }

    function setFileFilters(filters = {}) {
      document.getElementById('file-room-filter').value = filters.room || '';
      document.getElementById('file-keyword-filter').value = filters.keyword || '';
      document.getElementById('file-start-filter').value = filters.start_date || '';
      document.getElementById('file-end-filter').value = filters.end_date || '';
      document.getElementById('file-sort-filter').value = filters.sort || 'time_desc';
    }

    function renderFiles(data) {
      state.filesData = data;
      const root = document.getElementById('files-root');
      const meta = document.getElementById('file-meta');
      const entries = data.entries || [];
      const hints = [];
      if (data.root) hints.push(`目录：${data.root}`);
      if (data.status_message) hints.push(data.status_message);
      hints.push(`筛选结果 ${data.count || 0} 条 / 缓存 ${data.cached_count || 0} 条`);
      if (data.total_files !== undefined) hints.push(`磁盘内总文件 ${data.total_files || 0} 条`);
      if (data.truncated) hints.push('缓存列表已截断，建议提高 Web控制台文件索引上限');
      if (data.scanned_at) hints.push(`索引更新时间：${formatTime(data.scanned_at)}`);
      meta.textContent = hints.join(' · ');
      root.innerHTML = renderTable([
        { title: '房间名', render: row => `<div>${escapeHtml(row.room_name || '-')}</div><div class="muted">${escapeHtml(row.modified_at || '')}</div>` },
        { title: '文件名', render: row => `<div>${escapeHtml(row.name || '-')}</div><div class="muted">${escapeHtml(row.relative_path || '')}</div>` },
        { title: '大小', render: row => formatBytes(row.size_bytes) },
        { title: '绝对路径', render: row => `<div class="muted">${escapeHtml(row.absolute_path || '-')}</div>` },
      ], entries);
    }

    async function loadOverview() {
      const status = document.getElementById('overview-status');
      status.textContent = '正在刷新总览...';
      try {
        const data = await fetchJson('/api/overview');
        state.overviewData = data;
        renderOverview(data);
        status.textContent = `最近刷新：${new Date().toLocaleTimeString()}`;
      } catch (error) {
        status.textContent = error.message;
      }
    }

    async function loadFiles() {
      const status = document.getElementById('files-status');
      status.textContent = '正在读取录制文件索引...';
      try {
        const params = new URLSearchParams(getFileFilters());
        const data = await fetchJson(`/api/files?${params.toString()}`);
        renderFiles(data);
        status.textContent = `文件索引已刷新：${new Date().toLocaleTimeString()}`;
      } catch (error) {
        status.textContent = error.message;
      }
    }

    async function loadConfig() {
      const status = document.getElementById('config-status');
      status.textContent = '正在读取 config.ini ...';
      try {
        const data = await fetchJson('/api/config');
        renderConfig(data);
        status.textContent = 'config.ini 已读取。';
      } catch (error) {
        status.textContent = error.message;
      }
    }

    async function saveConfig() {
      const status = document.getElementById('config-status');
      status.textContent = '正在保存 config.ini ...';
      try {
        await fetchJson('/api/config', {
          method: 'POST',
          body: JSON.stringify(collectConfigPayload()),
        });
        status.textContent = 'config.ini 保存成功，已请求主循环立即重载配置。';
        await loadConfig();
        await loadOverview();
      } catch (error) {
        status.textContent = error.message;
      }
    }

    async function loadUrls() {
      const status = document.getElementById('url-status');
      status.textContent = '正在读取 URL_config.ini ...';
      try {
        const data = await fetchJson('/api/url-config');
        state.urlData = data;
        document.getElementById('url-editor').value = data.content || '';
        document.getElementById('url-notes').innerHTML = (data.notes || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
        renderUrlPreview(data.preview || {});
        status.textContent = 'URL_config.ini 已读取。';
      } catch (error) {
        status.textContent = error.message;
      }
    }

    async function saveUrls() {
      const status = document.getElementById('url-status');
      status.textContent = '正在保存 URL_config.ini ...';
      try {
        const content = document.getElementById('url-editor').value;
        const data = await fetchJson('/api/url-config', {
          method: 'POST',
          body: JSON.stringify({ content }),
        });
        renderUrlPreview(data.preview || {});
        status.textContent = 'URL_config.ini 保存成功，已请求主循环立即重载配置。';
        await loadOverview();
      } catch (error) {
        status.textContent = error.message;
      }
    }

    function resetFilesFilter() {
      setFileFilters({ sort: 'time_desc' });
      loadFiles();
    }

    function switchPanel(panelName) {
      state.activePanel = panelName;
      panels.forEach(panel => panel.classList.toggle('active', panel.id === panelName));
      navButtons.forEach(button => button.classList.toggle('active', button.dataset.panel === panelName));
      if (panelName === 'dashboard') loadOverview();
      if (panelName === 'files') loadFiles();
      if (panelName === 'config') loadConfig();
      if (panelName === 'urls') loadUrls();
    }

    navButtons.forEach(button => button.addEventListener('click', () => switchPanel(button.dataset.panel)));
    document.getElementById('refresh-overview').addEventListener('click', loadOverview);
    document.getElementById('refresh-files').addEventListener('click', loadFiles);
    document.getElementById('apply-file-filter').addEventListener('click', loadFiles);
    document.getElementById('reset-file-filter').addEventListener('click', resetFilesFilter);
    document.getElementById('reload-config').addEventListener('click', loadConfig);
    document.getElementById('save-config').addEventListener('click', saveConfig);
    document.getElementById('reload-urls').addEventListener('click', loadUrls);
    document.getElementById('save-urls').addEventListener('click', saveUrls);

    ['file-room-filter', 'file-keyword-filter', 'file-start-filter', 'file-end-filter'].forEach(id => {
      document.getElementById(id).addEventListener('keydown', event => {
        if (event.key === 'Enter') {
          event.preventDefault();
          loadFiles();
        }
      });
    });
    document.getElementById('file-sort-filter').addEventListener('change', loadFiles);

    loadOverview();
    state.timer = setInterval(() => {
      if (state.activePanel === 'dashboard') {
        loadOverview();
      }
    }, 5000);
  </script>
</body>
</html>
"""


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in YES_VALUES


def coerce_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def duration_seconds_between(started_at: Any, ended_at: Any | None = None) -> int:
    started = parse_iso_datetime(started_at)
    end = parse_iso_datetime(ended_at) if ended_at else datetime.now()
    if not started or not end:
        return 0
    return max(0, int((end - started).total_seconds()))


def normalize_record_name(value: Any) -> str:
    text = RECORD_NAME_PREFIX_PATTERN.sub('', str(value or '')).strip()
    return text


def is_douyin_url(value: Any) -> bool:
    return 'douyin.com' in str(value or '').lower()


def is_douyin_session(session: dict[str, Any]) -> bool:
    platform = str(session.get('platform') or '')
    return is_douyin_url(session.get('record_url')) or '抖音' in platform


def infer_room_name_from_file(relative_path: str, fallback_name: str = '') -> str:
    normalized = str(relative_path or '').replace('\\', '/').strip('/')
    parts = [part for part in normalized.split('/') if part]
    for part in parts[:-1]:
        if not DATE_FOLDER_PATTERN.fullmatch(part):
            return part

    stem = Path(parts[-1]).stem if parts else Path(fallback_name or normalized or '-').stem
    stem = FILE_TIME_SUFFIX_PATTERN.sub('', stem).strip(' _-')
    return normalize_record_name(stem) or fallback_name or '-'


def build_event_key(event: dict[str, Any]) -> str:
    return '|'.join([
        str(event.get('timestamp') or ''),
        str(event.get('level') or ''),
        str(event.get('message') or ''),
        str(event.get('source') or ''),
    ])


def is_container_environment() -> bool:
    return Path('/.dockerenv').exists() or bool(os.environ.get('container')) or bool(os.environ.get('KUBERNETES_SERVICE_HOST'))


def resolve_listen_host(value: str | None) -> str:
    host = (value or 'auto').strip() or 'auto'
    if host.lower() == 'auto':
        return '0.0.0.0' if is_container_environment() else '127.0.0.1'
    return host


def ensure_web_console_defaults(config_file: str | Path) -> dict[str, str]:
    parser = load_ini(config_file)
    ensure_sections(parser, ['录制设置'])
    changed = False
    values: dict[str, str] = {}
    for option, default in DEFAULT_WEB_OPTIONS.items():
        if not parser.has_option('录制设置', option):
            parser.set('录制设置', option, default)
            changed = True
            values[option] = default
        else:
            values[option] = parser.get('录制设置', option)
    if changed:
        save_ini(parser, config_file)
    return values


def read_web_console_settings(config_file: str | Path) -> dict[str, Any]:
    values = ensure_web_console_defaults(config_file)
    host_raw = values.get('Web控制台监听地址', 'auto')
    port = coerce_int(values.get('Web控制台端口', 18080), 18080)
    file_index_limit = max(50, min(coerce_int(values.get('Web控制台文件索引上限', 500), 500), 5000))
    file_cache_seconds = max(5, min(coerce_int(values.get('Web控制台文件索引缓存秒数', 30), 30), 3600))
    return {
        'enabled': parse_bool(values.get('是否启用Web控制台(是/否)', '是'), True),
        'listen_host_raw': host_raw,
        'listen_host': resolve_listen_host(host_raw),
        'port': port,
        'file_index_limit': file_index_limit,
        'file_cache_ttl_seconds': file_cache_seconds,
    }


def is_sensitive_option(section: str, option: str) -> bool:
    text = f'{section}.{option}'.lower()
    return any(pattern in text for pattern in SENSITIVE_PATTERNS)


def infer_field_type(option: str, value: str, sensitive: bool) -> str:
    if sensitive:
        return 'textarea'
    if option == '视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频':
        return 'select'
    if option == '原画|超清|高清|标清|流畅':
        return 'select'
    if '(是/否)' in option or value in ('是', '否'):
        return 'select'
    if option == 'bark推送中断级别':
        return 'select'
    if option.endswith('端口') or option.endswith('(秒)') or option.endswith('(gb)'):
        return 'number'
    if len(value or '') > 120:
        return 'textarea'
    return 'text'


def infer_choices(option: str, value: str) -> list[str]:
    if option == '视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频':
        return ['ts', 'mkv', 'flv', 'mp4', 'mp3音频', 'm4a音频']
    if option == '原画|超清|高清|标清|流畅':
        return ['原画', '超清', '高清', '标清', '流畅']
    if '(是/否)' in option or value in ('是', '否'):
        return ['是', '否']
    if option == 'bark推送中断级别':
        return ['active', 'time-sensitive', 'passive']
    return []


def is_restart_required_option(option: str) -> bool:
    return option in RESTART_REQUIRED_OPTIONS


def find_existing_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        candidate = candidate.absolute()
    current = candidate
    while not current.exists() and current != current.parent:
        current = current.parent
    return current if current.exists() else candidate.parent


class RuntimeState:
    def __init__(self) -> None:
        self.started_at = now_iso()
        self._lock = threading.RLock()
        self._active_sessions: dict[str, dict[str, Any]] = {}
        self._active_processes: dict[str, Any] = {}
        self._completed_sessions: deque[dict[str, Any]] = deque(maxlen=120)
        self._recent_logs: deque[dict[str, Any]] = deque(maxlen=220)
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=220)
        self._config_reload_requested = False
        self._config_reload_requested_at = ''
        self._config_reload_source = ''
        self._stop_record_requests: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _stop_request_keys(record_name: str = '', record_url: str = '') -> set[str]:
        keys: set[str] = set()
        if record_name and str(record_name).strip():
            keys.add(f'name:{str(record_name).strip()}')
        if record_url and str(record_url).strip():
            keys.add(f'url:{str(record_url).strip()}')
        return keys

    def _find_active_session_locked(self, record_name: str = '', record_url: str = '') -> tuple[str, dict[str, Any] | None]:
        target_name = str(record_name or '').strip()
        target_url = str(record_url or '').strip()
        if target_name and target_name in self._active_sessions:
            return target_name, self._active_sessions[target_name]
        if target_url:
            for name, session in self._active_sessions.items():
                if str(session.get('record_url') or '').strip() == target_url:
                    return name, session
        return target_name, None

    @staticmethod
    def _build_stop_request_meta(reason: str = 'stop') -> dict[str, Any]:
        return {
            'request_id': str(time.time_ns()),
            'reason': str(reason or 'stop'),
            'requested_at': now_iso(),
            'requested_at_ts': time.time(),
        }

    def _get_stop_request_state_locked(self, record_name: str = '', record_url: str = '') -> dict[str, Any]:
        keys = self._stop_request_keys(record_name, record_url)
        matched = [self._stop_record_requests[key] for key in keys if key in self._stop_record_requests]
        if not matched:
            return {
                'requested': False,
                'request_id': '',
                'reason': '',
                'requested_at': '',
                'age_seconds': 0,
            }

        preferred = next((item for item in matched if item.get('reason') == 'pause'), None)
        meta = preferred or max(matched, key=lambda item: float(item.get('requested_at_ts') or 0))
        requested_at_ts = float(meta.get('requested_at_ts') or 0)
        return {
            'requested': True,
            'request_id': str(meta.get('request_id') or ''),
            'reason': str(meta.get('reason') or 'stop'),
            'requested_at': str(meta.get('requested_at') or ''),
            'age_seconds': max(0, int(time.time() - requested_at_ts)),
        }

    def add_log(self, level: str, message: str, source: str = '') -> None:
        entry = {
            'timestamp': now_iso(),
            'level': level.upper(),
            'message': message,
            'source': source,
        }
        with self._lock:
            self._recent_logs.appendleft(entry)

    def add_event(self, level: str, message: str, source: str = '') -> None:
        entry = {
            'timestamp': now_iso(),
            'level': level.upper(),
            'message': message,
            'source': source,
        }
        with self._lock:
            self._recent_events.appendleft(entry)

    def request_config_reload(self, source: str = '') -> None:
        with self._lock:
            self._config_reload_requested = True
            self._config_reload_requested_at = now_iso()
            self._config_reload_source = source

    def is_config_reload_requested(self) -> bool:
        with self._lock:
            return self._config_reload_requested

    def clear_config_reload_request(self) -> None:
        with self._lock:
            self._config_reload_requested = False
            self._config_reload_requested_at = ''
            self._config_reload_source = ''

    def register_process(self, record_name: str, process: Any) -> None:
        with self._lock:
            self._active_processes[record_name] = process
            if record_name in self._active_sessions:
                self._active_sessions[record_name]['process_registered'] = True

    def unregister_process(self, record_name: str) -> None:
        with self._lock:
            self._active_processes.pop(record_name, None)
            if record_name in self._active_sessions:
                self._active_sessions[record_name]['process_registered'] = False

    def is_paused(self, record_name: str = '', record_url: str = '') -> bool:
        with self._lock:
            _, active = self._find_active_session_locked(record_name, record_url)
            return bool(active and active.get('control_state') == 'paused')

    def pause_recording(self, record_name: str = '', record_url: str = '') -> dict[str, Any]:
        with self._lock:
            matched_name, active = self._find_active_session_locked(record_name, record_url)
            if not active:
                return {'ok': False, 'error': '未找到正在录制的任务'}
            if active.get('control_state') == 'paused':
                return {
                    'ok': True,
                    'record_name': matched_name,
                    'record_url': active.get('record_url', ''),
                    'control_state': 'paused',
                }
            if not active.get('process_registered', False):
                return {'ok': False, 'error': '当前录制任务不支持暂停/继续控制'}
            active['control_state'] = 'paused'
            keys = self._stop_request_keys(matched_name, active.get('record_url', ''))
            meta = self._build_stop_request_meta('pause')
            for key in keys:
                self._stop_record_requests[key] = dict(meta)
            return {
                'ok': True,
                'record_name': matched_name,
                'record_url': active.get('record_url', ''),
                'control_state': 'paused',
            }

    def resume_recording(self, record_name: str = '', record_url: str = '') -> dict[str, Any]:
        with self._lock:
            matched_name, active = self._find_active_session_locked(record_name, record_url)
            if not active:
                return {'ok': False, 'error': '未找到可继续的录制任务'}
            if active.get('control_state') != 'paused':
                return {
                    'ok': True,
                    'record_name': matched_name,
                    'record_url': active.get('record_url', ''),
                    'control_state': active.get('control_state', 'recording'),
                }
            active['control_state'] = 'recording'
            active['process_registered'] = False
            active.pop('paused_at', None)
            active.pop('paused_duration_seconds', None)
            keys = self._stop_request_keys(matched_name, active.get('record_url', ''))
            for key in keys:
                self._stop_record_requests.pop(key, None)
            return {
                'ok': True,
                'record_name': matched_name,
                'record_url': active.get('record_url', ''),
                'control_state': 'recording',
            }

    def request_stop_recording(self, record_name: str = '', record_url: str = '', reason: str = 'stop') -> dict[str, Any]:
        with self._lock:
            target_name = str(record_name or '').strip()
            target_url = str(record_url or '').strip()
            matched_name, active = self._find_active_session_locked(target_name, target_url)
            if active:
                target_name = active.get('record_name', matched_name or target_name)
                target_url = active.get('record_url', target_url)
            keys = self._stop_request_keys(target_name, target_url)
            if not keys:
                return {'ok': False, 'record_name': target_name, 'record_url': target_url}
            meta = self._build_stop_request_meta(reason)
            for key in keys:
                self._stop_record_requests[key] = dict(meta)
            if active and not active.get('process_registered', False):
                self._active_sessions.pop(target_name, None)
                self._active_processes.pop(target_name, None)
            return {
                'ok': True,
                'record_name': target_name,
                'record_url': target_url,
                'matched_active_session': active is not None,
                'reason': str(reason or 'stop'),
                'requested_at': meta['requested_at'],
            }

    def should_stop_recording(self, record_name: str = '', record_url: str = '') -> bool:
        with self._lock:
            return bool(self._get_stop_request_state_locked(record_name, record_url).get('requested'))

    def get_stop_request_state(self, record_name: str = '', record_url: str = '') -> dict[str, Any]:
        with self._lock:
            return self._get_stop_request_state_locked(record_name, record_url)

    def should_block_new_recording(self, record_name: str = '', record_url: str = '',
                                   cooldown_seconds: int = 0) -> bool:
        cooldown_seconds = max(0, int(cooldown_seconds or 0))
        with self._lock:
            state = self._get_stop_request_state_locked(record_name, record_url)
            if not state.get('requested'):
                return False
            if state.get('reason') == 'pause':
                return True
            return int(state.get('age_seconds') or 0) < cooldown_seconds

    def clear_stop_recording_request(self, record_name: str = '', record_url: str = '') -> bool:
        keys = self._stop_request_keys(record_name, record_url)
        if not keys:
            return False
        cleared = False
        with self._lock:
            request_ids = {
                str(self._stop_record_requests[key].get('request_id') or '')
                for key in keys
                if key in self._stop_record_requests
            }
            for key in keys:
                if self._stop_record_requests.pop(key, None) is not None:
                    cleared = True
            if request_ids:
                stale_keys = [
                    key for key, meta in self._stop_record_requests.items()
                    if str(meta.get('request_id') or '') in request_ids
                ]
                for key in stale_keys:
                    self._stop_record_requests.pop(key, None)
                    cleared = True
        return cleared

    def clear_all_stop_recording_requests(self) -> None:
        with self._lock:
            self._stop_record_requests.clear()

    def recording_started(
        self,
        record_name: str,
        record_url: str,
        save_file_path: str,
        save_type: str,
        platform: str = '',
        quality: str = '',
    ) -> None:
        session = {
            'record_name': record_name,
            'record_url': record_url,
            'save_file_path': save_file_path,
            'save_type': save_type,
            'platform': platform,
            'quality': quality,
            'started_at': now_iso(),
            'control_state': 'recording',
            'process_registered': False,
        }
        with self._lock:
            previous = self._active_sessions.get(record_name)
            if previous and previous.get('started_at') and previous.get('process_registered'):
                session['started_at'] = previous['started_at']
            self._active_sessions[record_name] = session
        self.add_event('INFO', f'{record_name} 开始录制', save_file_path)

    def recording_paused(self, record_name: str, save_file_path: str | None = None, note: str = '') -> None:
        paused_at = now_iso()
        with self._lock:
            active = self._active_sessions.get(record_name, {})
            self._active_processes.pop(record_name, None)
            started_at = active.get('started_at', paused_at)
            item = {
                'record_name': record_name,
                'status': 'paused',
                'status_label': '已暂停',
                'save_file_path': save_file_path or active.get('save_file_path', ''),
                'record_url': active.get('record_url', ''),
                'save_type': active.get('save_type', ''),
                'platform': active.get('platform', ''),
                'quality': active.get('quality', ''),
                'started_at': started_at,
                'ended_at': paused_at,
                'duration_seconds': duration_seconds_between(started_at, paused_at),
                'note': note,
            }
            self._completed_sessions.appendleft(item)
            if active:
                active['save_file_path'] = item['save_file_path']
                active['control_state'] = 'paused'
                active['process_registered'] = False
                active['paused_at'] = paused_at
                active['paused_duration_seconds'] = item['duration_seconds']
        self.add_event('WARNING', f'{record_name} 已暂停', save_file_path or note)

    def recording_finished(
        self,
        record_name: str,
        status: str,
        save_file_path: str | None = None,
        note: str = '',
    ) -> None:
        ended_at = now_iso()
        with self._lock:
            active = self._active_sessions.pop(record_name, {})
            self._active_processes.pop(record_name, None)
            started_at = active.get('started_at', ended_at)
            item = {
                'record_name': record_name,
                'status': status,
                'status_label': {
                    'completed': '已完成',
                    'stopped': '已停止',
                    'error': '出错',
                }.get(status, status),
                'save_file_path': save_file_path or active.get('save_file_path', ''),
                'record_url': active.get('record_url', ''),
                'save_type': active.get('save_type', ''),
                'platform': active.get('platform', ''),
                'quality': active.get('quality', ''),
                'started_at': started_at,
                'ended_at': ended_at,
                'duration_seconds': duration_seconds_between(started_at, ended_at),
                'note': note,
            }
            self._completed_sessions.appendleft(item)
        level = 'ERROR' if status == 'error' else 'INFO'
        self.add_event(level, f'{record_name} {item["status_label"]}', item['save_file_path'] or note)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            active = []
            for item in self._active_sessions.values():
                data = dict(item)
                if data.get('control_state') == 'paused':
                    data['duration_seconds'] = int(data.get('paused_duration_seconds', 0) or 0)
                else:
                    data['duration_seconds'] = duration_seconds_between(data.get('started_at'))
                stop_state = self._get_stop_request_state_locked(data.get('record_name', ''), data.get('record_url', ''))
                data['stop_requested'] = bool(stop_state.get('requested'))
                data['stop_request_reason'] = stop_state.get('reason', '')
                data['is_paused'] = data.get('control_state') == 'paused'
                active.append(data)
            active.sort(key=lambda x: x.get('started_at', ''))
            completed = []
            for item in self._completed_sessions:
                data = dict(item)
                if 'duration_seconds' not in data:
                    data['duration_seconds'] = duration_seconds_between(data.get('started_at'), data.get('ended_at'))
                completed.append(data)
            return {
                'started_at': self.started_at,
                'active_sessions': active,
                'completed_sessions': completed,
                'recent_logs': list(self._recent_logs),
                'recent_events': list(self._recent_events),
                'config_reload_requested': self._config_reload_requested,
                'config_reload_requested_at': self._config_reload_requested_at,
                'config_reload_source': self._config_reload_source,
            }


class DownloadDirectoryCache:
    def __init__(self, max_entries: int = 500, ttl_seconds: int = 30) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._cache: dict[str, Any] = {}

    def configure(self, max_entries: int, ttl_seconds: int) -> None:
        with self._lock:
            self.max_entries = max_entries
            self.ttl_seconds = ttl_seconds

    def get(self, root_path: str | Path, limit: int | None = None) -> dict[str, Any]:
        root = str(Path(root_path).expanduser())
        now = time.time()
        with self._lock:
            if self._cache and self._cache.get('root') == root and now - self._cache.get('scanned_at_epoch', 0) < self.ttl_seconds:
                cached = dict(self._cache)
                cached['entries'] = cached.get('entries', [])[:limit or self.max_entries]
                return cached

        scanned = self._scan(root)
        with self._lock:
            self._cache = scanned
            cached = dict(scanned)
            cached['entries'] = cached.get('entries', [])[:limit or self.max_entries]
            return cached

    def _scan(self, root_path: str) -> dict[str, Any]:
        root = Path(root_path).expanduser()
        result: dict[str, Any] = {
            'root': str(root),
            'display_root': str(root.resolve()) if root.exists() else str(root),
            'status': 'ok',
            'status_message': '',
            'entries': [],
            'cached_count': 0,
            'total_files': 0,
            'total_size_bytes': 0,
            'truncated': False,
            'scanned_at_epoch': time.time(),
            'scanned_at': now_iso(),
            'errors': [],
        }

        if not root.exists():
            result['status'] = 'missing'
            result['status_message'] = '下载目录不存在，等待首次录制时自动创建或手动创建。'
            return result
        if not root.is_dir():
            result['status'] = 'not_dir'
            result['status_message'] = '下载路径不是目录，请检查 config.ini 中的直播保存路径。'
            return result
        if not os.access(root, os.R_OK):
            result['status'] = 'permission_denied'
            result['status_message'] = '下载目录无读取权限，文件列表无法展示。'
            return result

        heap: list[tuple[float, str, int]] = []
        for current_root, dirs, files in os.walk(root, topdown=True):
            dirs.sort()
            files.sort()
            for filename in files:
                file_path = Path(current_root) / filename
                try:
                    stat = file_path.stat()
                except PermissionError:
                    result['errors'].append(f'无权限读取: {file_path}')
                    continue
                except OSError as exc:
                    result['errors'].append(f'读取失败: {file_path} ({exc})')
                    continue

                result['total_files'] += 1
                result['total_size_bytes'] += stat.st_size
                item = (stat.st_mtime, str(file_path), stat.st_size)
                if len(heap) < self.max_entries:
                    heapq.heappush(heap, item)
                elif item[0] > heap[0][0]:
                    heapq.heapreplace(heap, item)

        newest = sorted(heap, key=lambda item: item[0], reverse=True)
        result['entries'] = [
            {
                'name': Path(path).name,
                'relative_path': str(Path(path).relative_to(root)).replace('\\', '/'),
                'absolute_path': str(Path(path)),
                'room_name': infer_room_name_from_file(str(Path(path).relative_to(root)).replace('\\', '/'), Path(path).name),
                'size_bytes': size,
                'modified_epoch': int(mtime),
                'modified_at': datetime.fromtimestamp(mtime).isoformat(timespec='seconds'),
            }
            for mtime, path, size in newest
        ]
        result['cached_count'] = len(result['entries'])
        result['truncated'] = result['total_files'] > len(result['entries'])
        if result['total_files'] == 0:
            result['status'] = 'empty'
            result['status_message'] = '下载目录目前为空。'
        elif result['errors']:
            result['status_message'] = f'扫描时出现 {len(result["errors"])} 个权限/读取问题，已跳过异常文件。'
        return result


def parse_url_entry_line(raw_line: str) -> dict[str, Any] | None:
    original = raw_line.rstrip('\n')
    stripped = original.strip()
    if not stripped:
        return None

    enabled = not stripped.startswith('#')
    working = stripped.lstrip('#').strip()
    parts = re.split('[,，]', working, maxsplit=2)
    quality = '原画'
    url = ''
    anchor_name = ''

    if len(parts) == 1:
        url = parts[0].strip()
    elif len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        if URL_PATTERN.search(left):
            url = left
            anchor_name = right
        else:
            quality = left or '原画'
            url = right
    else:
        quality = parts[0].strip() or '原画'
        url = parts[1].strip()
        anchor_name = parts[2].strip()

    return {
        'enabled': enabled,
        'quality': quality,
        'url': url,
        'anchor_name': anchor_name,
        'raw_line': original,
    }


def build_url_preview(text: str) -> dict[str, Any]:
    entries = []
    for line in text.splitlines():
        entry = parse_url_entry_line(line)
        if entry:
            entries.append(entry)
    return {'entries': entries}


class WebConsoleService:
    def __init__(
        self,
        config_file: str | Path,
        url_config_file: str | Path,
        default_download_path: str | Path,
        snapshot_provider: Callable[[], dict[str, Any]],
        logger: Any | None = None,
    ) -> None:
        self.config_file = str(config_file)
        self.url_config_file = str(url_config_file)
        self.default_download_path = str(default_download_path)
        self.snapshot_provider = snapshot_provider
        self.logger = logger
        self.runtime = RuntimeState()
        self.file_cache = DownloadDirectoryCache()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._log_sink_id: int | None = None
        self._host = ''
        self._port = 0
        self._enabled = False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def access_url(self) -> str:
        host = self._host or '127.0.0.1'
        if host in ('0.0.0.0', '::'):
            host = '127.0.0.1'
        return f'http://{host}:{self._port}' if self._port else ''

    def start_from_config(self) -> bool:
        settings = read_web_console_settings(self.config_file)
        self._enabled = settings['enabled']
        self.file_cache.configure(settings['file_index_limit'], settings['file_cache_ttl_seconds'])
        if not settings['enabled']:
            self.runtime.add_event('INFO', 'Web 控制台已在配置中禁用')
            return False

        host = settings['listen_host']
        port = settings['port']
        try:
            handler = self._build_handler()
            self._server = ThreadingHTTPServer((host, port), handler)
            self._server.daemon_threads = True
            self._host = host
            self._port = port
            self._thread = threading.Thread(target=self._server.serve_forever, name='web-console', daemon=True)
            self._thread.start()
            self._attach_log_sink()
            self.runtime.add_event('INFO', f'Web 控制台启动成功: {self.access_url}', f'绑定地址 {host}:{port}')
            return True
        except OSError as exc:
            self.runtime.add_event('ERROR', f'Web 控制台启动失败: {exc}', f'{host}:{port}')
            if self.logger:
                self.logger.error(f'Web 控制台启动失败: {exc}')
            return False

    def _attach_log_sink(self) -> None:
        if not self.logger or self._log_sink_id is not None:
            return

        def sink(message: Any) -> None:
            try:
                record = message.record
                source = f"{record['name']}:{record['function']}:{record['line']}"
                self.runtime.add_log(record['level'].name, record['message'], source)
            except Exception:
                return

        self._log_sink_id = self.logger.add(sink, level='DEBUG', enqueue=False)

    def recording_started(
        self,
        record_name: str,
        record_url: str,
        save_file_path: str,
        save_type: str,
        platform: str = '',
        quality: str = '',
    ) -> None:
        self.runtime.recording_started(record_name, record_url, save_file_path, save_type, platform, quality)

    def recording_finished(self, record_name: str, status: str, save_file_path: str | None = None, note: str = '') -> None:
        self.runtime.recording_finished(record_name, status, save_file_path, note)

    def recording_paused(self, record_name: str, save_file_path: str | None = None, note: str = '') -> None:
        self.runtime.recording_paused(record_name, save_file_path, note)

    def add_event(self, level: str, message: str, source: str = '') -> None:
        self.runtime.add_event(level, message, source)

    def request_config_reload(self, source: str = '') -> None:
        self.runtime.request_config_reload(source)
        self.runtime.add_event('INFO', '已请求主循环立即重载配置', source or 'web_console')

    def is_config_reload_requested(self) -> bool:
        return self.runtime.is_config_reload_requested()

    def clear_config_reload_request(self) -> None:
        self.runtime.clear_config_reload_request()

    def request_stop_recording(self, record_name: str = '', record_url: str = '') -> dict[str, Any]:
        result = self.runtime.request_stop_recording(record_name, record_url)
        if result.get('ok'):
            target_name = result.get('record_name') or record_name or record_url
            self.runtime.add_event('WARNING', f'已请求停止录制: {target_name}', result.get('record_url') or '')
        return result

    def should_stop_recording(self, record_name: str = '', record_url: str = '') -> bool:
        return self.runtime.should_stop_recording(record_name, record_url)

    def get_stop_request_state(self, record_name: str = '', record_url: str = '') -> dict[str, Any]:
        return self.runtime.get_stop_request_state(record_name, record_url)

    def should_block_recording_start(self, record_name: str = '', record_url: str = '',
                                     cooldown_seconds: int = 0) -> bool:
        return self.runtime.should_block_new_recording(record_name, record_url, cooldown_seconds=cooldown_seconds)

    def clear_stop_recording_request(self, record_name: str = '', record_url: str = '') -> bool:
        return self.runtime.clear_stop_recording_request(record_name, record_url)

    def clear_all_stop_recording_requests(self) -> None:
        self.runtime.clear_all_stop_recording_requests()

    def release_stop_request(self, record_name: str = '', record_url: str = '',
                             source: str = 'manual') -> dict[str, Any]:
        state = self.runtime.get_stop_request_state(record_name, record_url)
        if not state.get('requested'):
            return {'ok': False, 'error': '未找到已停止的 URL'}
        if state.get('reason') != 'stop':
            return {'ok': False, 'error': '当前任务处于暂停状态，请使用继续录制'}
        cleared = self.runtime.clear_stop_recording_request(record_name, record_url)
        if not cleared:
            return {'ok': False, 'error': '清除停止标记失败'}
        target_name = str(record_name or '').strip() or str(record_url or '').strip()
        if source == 'auto-live':
            message = f'检测到直播在线，已恢复自动录制: {target_name}'
        else:
            message = f'已恢复 URL 录制: {target_name}'
        self.runtime.add_event('INFO', message, str(record_url or target_name))
        return {
            'ok': True,
            'record_name': str(record_name or '').strip(),
            'record_url': str(record_url or '').strip(),
            'reason': state.get('reason', ''),
            'source': source,
        }

    def register_recording_process(self, record_name: str, process: Any) -> None:
        self.runtime.register_process(record_name, process)

    def unregister_recording_process(self, record_name: str) -> None:
        self.runtime.unregister_process(record_name)

    def pause_recording(self, record_name: str = '', record_url: str = '') -> dict[str, Any]:
        result = self.runtime.pause_recording(record_name, record_url)
        if result.get('ok'):
            target_name = result.get('record_name') or record_name or record_url
            self.runtime.add_event('WARNING', f'已暂停录制: {target_name}', result.get('record_url') or '')
        return result

    def resume_recording(self, record_name: str = '', record_url: str = '') -> dict[str, Any]:
        result = self.runtime.resume_recording(record_name, record_url)
        if result.get('ok'):
            target_name = result.get('record_name') or record_name or record_url
            self.runtime.add_event('INFO', f'已继续录制: {target_name}', result.get('record_url') or '')
        return result

    def is_recording_paused(self, record_name: str = '', record_url: str = '') -> bool:
        return self.runtime.is_paused(record_name, record_url)

    def _build_handler(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'DouyinLiveRecorderWeb/1.0'

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_html(self, content: str, status: int = 200) -> None:
                body = content.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get('Content-Length', '0') or 0)
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                return json.loads(raw.decode('utf-8') or '{}')

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == '/':
                    self._send_html(INDEX_HTML)
                    return
                if parsed.path == '/favicon.ico':
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.end_headers()
                    return
                if parsed.path == '/api/overview':
                    self._send_json(service.get_overview())
                    return
                if parsed.path == '/api/config':
                    self._send_json(service.get_config_payload())
                    return
                if parsed.path == '/api/url-config':
                    self._send_json(service.get_url_config_payload())
                    return
                if parsed.path == '/api/files':
                    self._send_json(service.get_files_payload(parse_qs(parsed.query)))
                    return
                self._send_json({'error': 'Not Found'}, 404)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                try:
                    payload = self._read_json()
                except json.JSONDecodeError:
                    self._send_json({'error': 'JSON 解析失败'}, 400)
                    return

                try:
                    if parsed.path == '/api/config':
                        result = service.save_config_payload(payload)
                        self._send_json(result)
                        return
                    if parsed.path == '/api/url-config':
                        result = service.save_url_config_payload(payload)
                        self._send_json(result)
                        return
                    if parsed.path == '/api/pause_recording':
                        result = service.pause_recording_payload(payload)
                        self._send_json(result)
                        return
                    if parsed.path == '/api/resume_recording':
                        result = service.resume_recording_payload(payload)
                        self._send_json(result)
                        return
                    if parsed.path == '/api/resume_url':
                        result = service.resume_url_payload(payload)
                        self._send_json(result)
                        return
                    if parsed.path == '/api/stop_recording':
                        result = service.stop_recording_payload(payload)
                        self._send_json(result)
                        return
                except configparser.Error as exc:
                    self._send_json({'error': f'配置格式错误: {exc}'}, 400)
                    return
                except ValueError as exc:
                    self._send_json({'error': str(exc)}, 400)
                    return
                except Exception as exc:
                    service.runtime.add_event('ERROR', f'Web API 出错: {exc}', parsed.path)
                    self._send_json({'error': f'请求处理失败: {exc}'}, 500)
                    return

                self._send_json({'error': 'Not Found'}, 404)

        return Handler

    def get_overview(self) -> dict[str, Any]:
        settings = read_web_console_settings(self.config_file)
        self.file_cache.configure(settings['file_index_limit'], settings['file_cache_ttl_seconds'])
        snapshot = self._safe_snapshot()
        download_path = snapshot.get('download_path') or self.default_download_path
        planned_text = read_text(self.url_config_file)
        runtime = self.runtime.snapshot()
        preview = self._annotate_url_preview(build_url_preview(planned_text), runtime=runtime)
        recent_recordings = self._build_recent_recordings(runtime.get('completed_sessions', []), limit=10)
        return {
            'service': {
                'enabled': self._enabled,
                'bind_host': self._host,
                'port': self._port,
                'access_url': self.access_url,
                'started_at': runtime.get('started_at'),
            },
            'summary': self._build_summary(snapshot),
            'planned': preview,
            'runtime': runtime,
            'douyin': self._build_douyin_status(preview, runtime),
            'alerts': self._build_alert_payload(runtime),
            'recent_recordings': recent_recordings,
            'disk': self._build_disk_usage(download_path),
            'files': self.file_cache.get(download_path, limit=min(settings['file_index_limit'], 200)),
        }

    def get_config_payload(self) -> dict[str, Any]:
        parser = load_ini(self.config_file)
        sections_payload = []
        ordered_sections = [section for section in CONFIG_SECTION_ORDER if parser.has_section(section)]
        ordered_sections.extend(section for section in parser.sections() if section not in ordered_sections)
        descriptions = {
            '录制设置': '录制、保存、代理、轮询、分段与内置 Web 控制台参数。',
            '推送配置': '开播/关播消息推送与通知节流。',
            'Cookie': '平台 Cookie，默认脱敏显示。',
            'Authorization': '平台 token / 授权参数。',
            '账号密码': '平台账号与密码类字段，默认脱敏显示。',
        }

        for section in ordered_sections:
            fields = []
            for option, value in parser.items(section):
                sensitive = is_sensitive_option(section, option)
                fields.append({
                    'option': option,
                    'value': '' if sensitive else value,
                    'has_value': bool(value),
                    'sensitive': sensitive,
                    'type': infer_field_type(option, value, sensitive),
                    'choices': infer_choices(option, value),
                    'restart_required': is_restart_required_option(option),
                    'hint': '留空保持原值。' if sensitive else '',
                    'placeholder': '已设置；留空则保持原值。',
                })
            sections_payload.append({
                'name': section,
                'description': descriptions.get(section, ''),
                'field_count': len(fields),
                'fields': fields,
            })

        return {'notes': HOT_RELOAD_NOTES, 'sections': sections_payload}

    def save_config_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if 'sections' not in payload or not isinstance(payload['sections'], dict):
            raise ValueError('缺少 sections 配置内容')

        parser = load_ini(self.config_file)
        submitted_sections: dict[str, Any] = payload['sections']
        for section, fields in submitted_sections.items():
            if not parser.has_section(section):
                parser.add_section(section)
            if not isinstance(fields, dict):
                continue
            for option, detail in fields.items():
                if not isinstance(detail, dict):
                    value = '' if detail is None else str(detail)
                    parser.set(section, option, value)
                    continue

                value = '' if detail.get('value') is None else str(detail.get('value', ''))
                if is_sensitive_option(section, option):
                    mode = detail.get('mode', 'keep')
                    if mode == 'keep':
                        continue
                    if mode == 'clear':
                        value = ''
                parser.set(section, option, value)

        save_ini(parser, self.config_file)
        settings = read_web_console_settings(self.config_file)
        self.file_cache.configure(settings['file_index_limit'], settings['file_cache_ttl_seconds'])
        self.request_config_reload('/api/config')
        self.runtime.add_event('INFO', 'config.ini 已保存', '已请求主循环立即重载配置')
        return {'ok': True, 'reload_requested': True}

    def get_url_config_payload(self) -> dict[str, Any]:
        content = read_text(self.url_config_file)
        runtime = self.runtime.snapshot()
        return {
            'content': content,
            'notes': HOT_RELOAD_NOTES,
            'preview': self._annotate_url_preview(build_url_preview(content), runtime=runtime),
        }

    def save_url_config_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        content = payload.get('content', '')
        if not isinstance(content, str):
            raise ValueError('content 必须是字符串')
        atomic_write_text(self.url_config_file, content)
        self.clear_all_stop_recording_requests()
        runtime = self.runtime.snapshot()
        preview = self._annotate_url_preview(build_url_preview(content), runtime=runtime)
        self.request_config_reload('/api/url-config')
        self.runtime.add_event('INFO', 'URL_config.ini 已保存', f'当前共 {len(preview.get("entries", []))} 条配置')
        return {'ok': True, 'preview': preview, 'reload_requested': True}

    def stop_recording_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        record_name = str(payload.get('record_name', '') or '').strip()
        record_url = str(payload.get('record_url', '') or '').strip()
        if not record_name and not record_url:
            raise ValueError('record_name 或 record_url 至少需要提供一个')
        result = self.request_stop_recording(record_name=record_name, record_url=record_url)
        if not result.get('ok'):
            raise ValueError('未找到可停止的录制任务')
        return result

    def pause_recording_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        record_name = str(payload.get('record_name', '') or '').strip()
        record_url = str(payload.get('record_url', '') or '').strip()
        if not record_name and not record_url:
            raise ValueError('record_name 或 record_url 至少需要提供一个')
        result = self.pause_recording(record_name=record_name, record_url=record_url)
        if not result.get('ok'):
            raise ValueError(str(result.get('error') or '暂停录制失败'))
        return result

    def resume_recording_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        record_name = str(payload.get('record_name', '') or '').strip()
        record_url = str(payload.get('record_url', '') or '').strip()
        if not record_name and not record_url:
            raise ValueError('record_name 或 record_url 至少需要提供一个')
        result = self.resume_recording(record_name=record_name, record_url=record_url)
        if not result.get('ok'):
            raise ValueError(str(result.get('error') or '继续录制失败'))
        return result

    def resume_url_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        record_name = str(payload.get('record_name', '') or '').strip()
        record_url = str(payload.get('record_url', '') or '').strip()
        if not record_name and not record_url:
            raise ValueError('record_name 或 record_url 至少需要提供一个')
        result = self.release_stop_request(record_name=record_name, record_url=record_url, source='manual')
        if not result.get('ok'):
            raise ValueError(str(result.get('error') or '恢复 URL 录制失败'))
        return result

    def get_files_payload(self, query: dict[str, list[str]] | None = None) -> dict[str, Any]:
        query = query or {}
        settings = read_web_console_settings(self.config_file)
        self.file_cache.configure(settings['file_index_limit'], settings['file_cache_ttl_seconds'])
        snapshot = self._safe_snapshot()
        download_path = snapshot.get('download_path') or self.default_download_path
        cached = self.file_cache.get(download_path, limit=settings['file_index_limit'])
        entries = [dict(item) for item in cached.get('entries', [])]

        room = (query.get('room') or [''])[0].strip()
        keyword = (query.get('keyword') or [''])[0].strip()
        start_date = (query.get('start_date') or [''])[0].strip()
        end_date = (query.get('end_date') or [''])[0].strip()
        sort = (query.get('sort') or ['time_desc'])[0].strip() or 'time_desc'

        room_lower = room.lower()
        keyword_lower = keyword.lower()
        filtered = []
        for entry in entries:
            room_name = str(entry.get('room_name') or infer_room_name_from_file(entry.get('relative_path', ''), entry.get('name', '')))
            entry['room_name'] = room_name
            relative_path = str(entry.get('relative_path') or '')
            name = str(entry.get('name') or '')
            modified_date = str(entry.get('modified_at') or '')[:10]
            searchable = ' '.join([room_name, relative_path, name]).lower()

            if room_lower and room_lower not in room_name.lower() and room_lower not in relative_path.lower():
                continue
            if keyword_lower and keyword_lower not in name.lower() and keyword_lower not in relative_path.lower():
                continue
            if start_date and modified_date and modified_date < start_date:
                continue
            if end_date and modified_date and modified_date > end_date:
                continue
            filtered.append(entry)

        sort_options: dict[str, tuple[str, bool]] = {
            'time_desc': ('modified_epoch', True),
            'time_asc': ('modified_epoch', False),
            'size_desc': ('size_bytes', True),
            'size_asc': ('size_bytes', False),
        }
        sort_key, reverse = sort_options.get(sort, ('modified_epoch', True))
        filtered.sort(key=lambda item: item.get(sort_key) or 0, reverse=reverse)

        return {
            'root': cached.get('display_root'),
            'status': cached.get('status'),
            'status_message': cached.get('status_message'),
            'entries': filtered,
            'filters': {
                'room': room,
                'keyword': keyword,
                'start_date': start_date,
                'end_date': end_date,
                'sort': sort,
            },
            'count': len(filtered),
            'cached_count': cached.get('cached_count', 0),
            'total_files': cached.get('total_files', 0),
            'total_size_bytes': cached.get('total_size_bytes', 0),
            'truncated': cached.get('truncated', False),
            'scanned_at': cached.get('scanned_at'),
            'errors': cached.get('errors', []),
        }

    def _safe_snapshot(self) -> dict[str, Any]:
        try:
            return self.snapshot_provider() or {}
        except Exception as exc:
            self.runtime.add_event('ERROR', f'读取运行时状态失败: {exc}')
            return {}

    def _build_summary(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            'version': snapshot.get('version', '-'),
            'video_save_type': snapshot.get('video_save_type', '-'),
            'video_record_quality': snapshot.get('video_record_quality', '-'),
            'monitoring': snapshot.get('monitoring', 0),
            'max_request': snapshot.get('max_request', 0),
            'use_proxy': snapshot.get('use_proxy', False),
            'global_proxy': snapshot.get('global_proxy', False),
            'split_video_by_time': snapshot.get('split_video_by_time', False),
            'split_time': snapshot.get('split_time', ''),
            'create_time_file': snapshot.get('create_time_file', False),
            'delay_default': snapshot.get('delay_default', 0),
            'error_count': snapshot.get('error_count', 0),
            'download_path': snapshot.get('download_path', self.default_download_path),
            'uptime_seconds': snapshot.get('uptime_seconds', 0),
            'has_douyin_cookie': snapshot.get('has_douyin_cookie', False),
            'disk_space_limit_gb': snapshot.get('disk_space_limit_gb', 0),
            'last_config_scan_at': snapshot.get('last_config_scan_at'),
            'web_hint': snapshot.get('web_hint', ''),
        }

    def _build_recent_recordings(self, completed_sessions: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
        status_meta = {
            'completed': ('成功', 'ok'),
            'error': ('失败', 'danger'),
            'stopped': ('中断', 'warn'),
        }
        recent = []
        for item in completed_sessions[:limit]:
            label, tone = status_meta.get(item.get('status'), (item.get('status_label') or item.get('status') or '-', 'info'))
            recent.append({
                'record_name': item.get('record_name', ''),
                'room_name': normalize_record_name(item.get('record_name', '')),
                'started_at': item.get('started_at'),
                'ended_at': item.get('ended_at'),
                'duration_seconds': item.get('duration_seconds', 0),
                'status': item.get('status'),
                'result_label': label,
                'result_tone': tone,
                'save_file_path': item.get('save_file_path', ''),
                'note': item.get('note', ''),
            })
        return recent

    def _build_alert_payload(self, runtime: dict[str, Any]) -> dict[str, Any]:
        recent_events = runtime.get('recent_events', []) or []
        now_dt = datetime.now()
        recent_errors = []
        error_count_24h = 0
        for event in recent_events:
            level = str(event.get('level') or '').upper()
            if level not in {'ERROR', 'CRITICAL'}:
                continue
            item = dict(event)
            item['error_key'] = build_event_key(item)
            recent_errors.append(item)
            event_dt = parse_iso_datetime(item.get('timestamp'))
            if event_dt and (now_dt - event_dt).total_seconds() <= 86400:
                error_count_24h += 1
        return {
            'has_errors': bool(recent_errors),
            'latest_error': recent_errors[0] if recent_errors else None,
            'recent_errors': recent_errors[:8],
            'error_count_24h': error_count_24h,
        }

    def _build_douyin_status(self, preview: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
        active_sessions = runtime.get('active_sessions', []) or []
        completed_sessions = runtime.get('completed_sessions', []) or []
        active_by_url = {
            str(item.get('record_url')).strip(): item
            for item in active_sessions
            if is_douyin_session(item) and str(item.get('record_url') or '').strip()
        }
        active_by_name = {
            normalize_record_name(item.get('record_name', '')): item
            for item in active_sessions
            if is_douyin_session(item) and normalize_record_name(item.get('record_name', ''))
        }
        completed_by_url: dict[str, dict[str, Any]] = {}
        completed_by_name: dict[str, dict[str, Any]] = {}
        for item in completed_sessions:
            if not is_douyin_session(item):
                continue
            record_url = str(item.get('record_url') or '').strip()
            room_name = normalize_record_name(item.get('record_name', ''))
            if record_url and record_url not in completed_by_url:
                completed_by_url[record_url] = item
            if room_name and room_name not in completed_by_name:
                completed_by_name[room_name] = item

        rooms: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        def append_room(room_name: str, url: str, quality: str, source: str, active_item: dict[str, Any] | None,
                        completed_item: dict[str, Any] | None) -> None:
            key = url or room_name
            if key in seen_keys:
                return
            seen_keys.add(key)
            latest = active_item or completed_item or {}
            if active_item:
                online_status = '在线'
                if active_item.get('control_state') == 'paused':
                    record_status = '已暂停'
                    status_tone = 'warn'
                else:
                    record_status = '录制中'
                    status_tone = 'ok'
            elif completed_item and completed_item.get('status') == 'error':
                online_status = '状态未知'
                record_status = '异常'
                status_tone = 'danger'
            else:
                online_status = '待开播'
                record_status = '等待中'
                status_tone = 'warn'

            rooms.append({
                'room_name': room_name or normalize_record_name(latest.get('record_name', '')) or url or '-',
                'url': url,
                'quality': quality or latest.get('quality', '') or '-',
                'source': source,
                'online_status': online_status,
                'record_status': record_status,
                'status_tone': status_tone,
                'last_started_at': latest.get('started_at'),
                'last_duration_seconds': active_item.get('duration_seconds', 0) if active_item else latest.get('duration_seconds', 0),
                'last_result_label': (completed_item.get('status_label', '') if completed_item else '') or ('录制中' if active_item else '暂无'),
                'last_result_tone': (
                    'ok' if completed_item and completed_item.get('status') == 'completed'
                    else 'warn' if completed_item and completed_item.get('status') == 'stopped'
                    else 'danger' if completed_item and completed_item.get('status') == 'error'
                    else 'info'
                ),
                'save_file_path': latest.get('save_file_path', ''),
                'enabled': source != 'commented',
            })

        for entry in preview.get('entries', []) or []:
            if not is_douyin_url(entry.get('url')) or not entry.get('enabled', True):
                continue
            url = str(entry.get('url') or '').strip()
            room_name = str(entry.get('anchor_name') or '').strip()
            room_key = normalize_record_name(room_name)
            active_item = active_by_url.get(url) or active_by_name.get(room_key)
            completed_item = completed_by_url.get(url) or completed_by_name.get(room_key)
            append_room(room_name or url, url, str(entry.get('quality') or ''), 'planned',
                        active_item, completed_item)

        for item in active_sessions:
            if not is_douyin_session(item):
                continue
            append_room(
                normalize_record_name(item.get('record_name', '')),
                str(item.get('record_url') or ''),
                str(item.get('quality') or ''),
                'runtime',
                item,
                completed_by_url.get(str(item.get('record_url') or '').strip())
                or completed_by_name.get(normalize_record_name(item.get('record_name', ''))),
            )

        for item in completed_sessions:
            if not is_douyin_session(item):
                continue
            append_room(
                normalize_record_name(item.get('record_name', '')),
                str(item.get('record_url') or ''),
                str(item.get('quality') or ''),
                'history',
                active_by_url.get(str(item.get('record_url') or '').strip())
                or active_by_name.get(normalize_record_name(item.get('record_name', ''))),
                item,
            )

        priority = {'在线': 0, '状态未知': 1, '待开播': 2}
        rooms.sort(key=lambda item: (priority.get(item.get('online_status', ''), 9), item.get('room_name', '')))
        return {
            'rooms': rooms,
            'stats': {
                'total': len(rooms),
                'online': sum(1 for item in rooms if item.get('online_status') == '在线'),
                'recording': sum(1 for item in rooms if item.get('record_status') == '录制中'),
                'waiting': sum(1 for item in rooms if item.get('record_status') == '等待中'),
                'abnormal': sum(1 for item in rooms if item.get('record_status') == '异常'),
            },
        }

    def _annotate_url_preview(self, preview: dict[str, Any], runtime: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime = runtime or self.runtime.snapshot()
        active_by_url = {
            str(item.get('record_url') or '').strip(): item
            for item in (runtime.get('active_sessions', []) or [])
            if str(item.get('record_url') or '').strip()
        }
        entries = []
        for item in preview.get('entries', []) or []:
            data = dict(item)
            record_url = str(data.get('url') or '').strip()
            state = self.runtime.get_stop_request_state(record_url=record_url)
            active_item = active_by_url.get(record_url)
            data['stop_requested'] = bool(state.get('requested'))
            data['request_id'] = str(state.get('request_id') or '')
            data['stop_reason'] = str(state.get('reason') or '')
            data['stop_requested_at'] = str(state.get('requested_at') or '')
            data['stop_age_seconds'] = int(state.get('age_seconds') or 0)
            data['has_active_session'] = active_item is not None
            data['active_control_state'] = str(active_item.get('control_state') or '') if active_item else ''
            entries.append(data)
        return {
            **preview,
            'entries': entries,
        }

    def _build_disk_usage(self, target_path: str | Path) -> dict[str, Any]:
        path_obj = Path(target_path).expanduser()
        existing = find_existing_path(path_obj)
        payload = {
            'root': str(path_obj),
            'display_root': str(path_obj.resolve()) if path_obj.exists() else str(path_obj),
            'status': 'ok',
            'status_message': '',
            'total_bytes': None,
            'used_bytes': None,
            'free_bytes': None,
            'total_human': '-',
            'used_human': '-',
            'free_human': '-',
            'free_gb': None,
            'used_percent': None,
            'alert_level': '',
        }
        try:
            usage = shutil.disk_usage(existing)
            payload['total_bytes'] = usage.total
            payload['used_bytes'] = usage.used
            payload['free_bytes'] = usage.free
            payload['free_gb'] = round(usage.free / (1024 ** 3), 2)
            payload['used_percent'] = round((usage.used / usage.total) * 100, 1) if usage.total else None
            payload['total_human'] = self._human_bytes(usage.total)
            payload['used_human'] = self._human_bytes(usage.used)
            payload['free_human'] = self._human_bytes(usage.free)
            if payload['used_percent'] is not None:
                if payload['used_percent'] >= 90:
                    payload['alert_level'] = 'danger'
                elif payload['used_percent'] >= 80:
                    payload['alert_level'] = 'warn'
            if not path_obj.exists():
                payload['status'] = 'missing'
                payload['status_message'] = '目标下载目录尚不存在，当前容量来自其最近的已存在父目录。'
        except PermissionError:
            payload['status'] = 'permission_denied'
            payload['status_message'] = '读取磁盘容量时权限不足。'
        except OSError as exc:
            payload['status'] = 'error'
            payload['status_message'] = f'读取磁盘容量失败: {exc}'
        return payload

    @staticmethod
    def _human_bytes(value: int | None) -> str:
        if value is None:
            return '-'
        size = float(value)
        units = ['B', 'KB', 'MB', 'GB', 'TB']
        index = 0
        while size >= 1024 and index < len(units) - 1:
            size /= 1024
            index += 1
        return f'{size:.2f} {units[index]}'
