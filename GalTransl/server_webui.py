"""内嵌单页 Web UI（0.4.10 从 server.py 抽出）。

后端以 GET / 直接返回该页面，供无桌面壳时用浏览器访问后端。
本模块无任何内部依赖，是 server.py 功能域拆分中依赖面最小的一块。
"""
from __future__ import annotations


INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>GalTransl Backend Mode</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --line: #d9e1ec;
      --text: #1f2a37;
      --muted: #5b6b7f;
      --primary: #2f6feb;
      --primary-hover: #1d5fe0;
      --success: #0f9d58;
      --warning: #f39c12;
      --danger: #d93025;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .page {
      max-width: 1080px;
      margin: 0 auto;
      padding: 24px;
    }
    .hero {
      margin-bottom: 20px;
    }
    .hero h1 {
      margin: 0 0 8px;
      font-size: 30px;
    }
    .hero p {
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(320px, 420px) 1fr;
      gap: 20px;
      align-items: start;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 8px 30px rgba(16, 24, 40, 0.05);
    }
    .card h2 {
      margin: 0 0 16px;
      font-size: 18px;
    }
    .field {
      margin-bottom: 14px;
    }
    .field label {
      display: block;
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .field input,
    .field select {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      font-size: 14px;
      background: #fff;
    }
    .actions {
      display: flex;
      gap: 10px;
      align-items: center;
      margin-top: 10px;
    }
    button {
      border: 0;
      background: var(--primary);
      color: white;
      padding: 10px 16px;
      border-radius: 10px;
      font-size: 14px;
      cursor: pointer;
    }
    button:hover { background: var(--primary-hover); }
    button.secondary {
      background: #e8eef9;
      color: var(--primary);
    }
    .hint {
      font-size: 13px;
      color: var(--muted);
      line-height: 1.6;
      margin-top: 12px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 600;
    }
    .status-pending { background: #eef3ff; color: #3159c9; }
    .status-running { background: #fff4dd; color: #a96500; }
    .status-completed { background: #e7f8ee; color: var(--success); }
    .status-failed { background: #fdecea; color: var(--danger); }
    .status-cancelled { background: #f1f3f5; color: #667085; }
    .job-list {
      display: grid;
      gap: 12px;
    }
    .job {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      background: #fff;
    }
    .job-header {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 8px;
    }
    .job-title {
      font-weight: 600;
      overflow-wrap: anywhere;
    }
    .job-meta {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 8px 14px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
      margin-top: 10px;
    }
    .job-error {
      margin-top: 10px;
      color: var(--danger);
      background: #fff4f4;
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 13px;
      white-space: pre-wrap;
    }
    .empty {
      color: var(--muted);
      font-size: 14px;
      padding: 18px 0;
    }
    @media (max-width: 860px) {
      .layout { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>GalTransl Backend Mode</h1>
      <p>这是一个为后续 Web UI 铺路的最小验证界面。它不会替换原来的 <code>run_GalTransl.py</code>，而是通过独立的本地服务入口提交和查看翻译任务。</p>
    </section>

    <div class="layout">
      <section class="card">
        <h2>提交任务</h2>
        <form id="job-form">
          <div class="field">
            <label for="project_dir">项目目录</label>
            <input id="project_dir" name="project_dir" placeholder="例如：E:\\GalTransl\\sampleProject" required />
          </div>
          <div class="field">
            <label for="config_file_name">配置文件名</label>
            <input id="config_file_name" name="config_file_name" value="config.yaml" required />
          </div>
          <div class="field">
            <label for="translator">翻译模板</label>
            <select id="translator" name="translator" required></select>
          </div>
          <div class="actions">
            <button type="submit">启动任务</button>
            <button type="button" class="secondary" id="refresh-btn">刷新状态</button>
          </div>
        </form>
        <div class="hint">
          建议先用 <code>show-plugs</code> 或现有可工作的项目配置来验证服务链路。当前版本采用单执行槽，避免和共享日志、缓存输出发生冲突。
        </div>
      </section>

      <section class="card">
        <h2>任务列表</h2>
        <div id="jobs" class="job-list"></div>
      </section>
    </div>
  </div>

  <script>
    const translatorSelect = document.getElementById('translator');
    const jobsContainer = document.getElementById('jobs');
    const form = document.getElementById('job-form');
    const refreshBtn = document.getElementById('refresh-btn');

    function escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    function statusClass(status) {
      return `status-${status || 'pending'}`;
    }

    async function loadTranslators() {
      const response = await fetch('/api/translators');
      const data = await response.json();
      translatorSelect.innerHTML = data.translators.map(item => {
        return `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} - ${escapeHtml(item.description)}</option>`;
      }).join('');
    }

    function renderJobs(jobs) {
      if (!jobs.length) {
        jobsContainer.innerHTML = '<div class="empty">还没有任务，先从左侧提交一个本地任务。</div>';
        return;
      }

      jobsContainer.innerHTML = jobs.map(job => `
        <article class="job">
          <div class="job-header">
            <div class="job-title">${escapeHtml(job.project_dir)}</div>
            <span class="pill ${statusClass(job.status)}">${escapeHtml(job.status)}</span>
          </div>
          <div>${escapeHtml(job.translator)} / ${escapeHtml(job.config_file_name)}</div>
          <div class="job-meta">
            <div><strong>任务 ID：</strong>${escapeHtml(job.job_id)}</div>
            <div><strong>创建时间：</strong>${escapeHtml(job.created_at || '-')}</div>
            <div><strong>开始时间：</strong>${escapeHtml(job.started_at || '-')}</div>
            <div><strong>结束时间：</strong>${escapeHtml(job.finished_at || '-')}</div>
            <div><strong>执行结果：</strong>${job.success ? 'success' : 'not finished / failed'}</div>
          </div>
          ${job.error ? `<div class="job-error">${escapeHtml(job.error)}</div>` : ''}
        </article>
      `).join('');
    }

    async function loadJobs() {
      const response = await fetch('/api/jobs');
      const data = await response.json();
      renderJobs(data.jobs || []);
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const payload = {
        project_dir: document.getElementById('project_dir').value,
        config_file_name: document.getElementById('config_file_name').value,
        translator: translatorSelect.value,
      };

      const response = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await response.json();

      if (!response.ok) {
        alert(data.error || '提交任务失败');
        return;
      }

      await loadJobs();
    });

    refreshBtn.addEventListener('click', () => loadJobs());
    loadTranslators().then(loadJobs);
    setInterval(loadJobs, 2000);
  </script>
</body>
</html>
"""

