"""Local-only read/write operator panel for health and private memories."""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from wechat_ai_bridge.health import collect_health
from wechat_ai_bridge.logging_setup import tail_log
from wechat_ai_bridge.memory import MemoryStore
from wechat_ai_bridge.settings import Settings


_STYLE = """
:root{color-scheme:light dark;font-family:system-ui,"Segoe UI",sans-serif}
body{margin:0;background:#f5f6f8;color:#20242c}
main{max-width:1180px;margin:0 auto;padding:28px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}
.muted{color:#687080}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.card{background:white;border:1px solid #e1e5eb;border-radius:12px;padding:16px}
.good{color:#168347;font-weight:600}.bad{color:#b42318;font-weight:600}
table{width:100%;border-collapse:collapse;background:white;border:1px solid #e1e5eb;border-radius:12px;overflow:hidden}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #edf0f3;vertical-align:top}
th{font-size:12px;color:#687080;background:#fafbfc}
input,button{font:inherit;padding:8px 10px;border:1px solid #cfd6df;border-radius:8px;background:white}
button{cursor:pointer}button.danger{color:#b42318;border-color:#f0b4ae}
form.inline{display:inline}
pre{white-space:pre-wrap;word-break:break-all;background:#161b22;color:#d7dde5;padding:14px;border-radius:10px;font-size:12px;max-height:360px;overflow:auto}
.tag{font-size:11px;background:#eef2f7;padding:2px 6px;border-radius:99px}
@media (prefers-color-scheme:dark){body{background:#111418;color:#e6e9ee}.card,table{background:#191e25;border-color:#2b333d}th{background:#151a20}input,button{background:#20262e;color:#e6e9ee;border-color:#3a4450}}
"""


def _page(settings: Settings, *, query: str = '') -> str:
    health = collect_health(settings)
    store = MemoryStore(settings.memory_db)
    memories = store.list_all(query=query, limit=300)
    ports = health['ports']
    cards = ''.join(
        f'<div class="card"><div class="muted">{html.escape(label)}</div>'
        f'<div class="{"good" if value else "bad"}">'
        f'{"在线" if value else "离线"}</div></div>'
        for label, value in (
            ('微信网关', health['wechat_gateway']),
            ('QQ webhook :8081', ports['qq_webhook']),
            ('NapCat :3000', ports['napcat']),
            ('GPT-SoVITS :9880', ports['gpt_sovits']),
        )
    )
    memory_count = health['memory']['count']
    queue = health['singing_queue']
    rows = ''.join(
        '<tr>'
        f'<td>{item.id}</td><td>{html.escape(item.platform)}</td>'
        f'<td class="muted">{html.escape(item.user_key)}</td>'
        f'<td>{html.escape(item.kind)}</td>'
        f'<td>{html.escape(item.content)}</td>'
        f'<td>{item.importance:.2f}</td>'
        f'<td>{html.escape(item.updated_at)}</td>'
        '<td><form class="inline" method="post" action="/memory/delete">'
        f'<input type="hidden" name="id" value="{item.id}">'
        '<button class="danger">删除</button></form></td></tr>'
        for item in memories
    ) or '<tr><td colspan="8" class="muted">没有匹配的记忆。</td></tr>'
    logs = '\n'.join(tail_log(settings.log_dir, lines=200)) or '还没有日志。'
    query_html = html.escape(query)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="15">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>wechat-ai-bridge panel</title><style>{_STYLE}</style></head>
<body><main>
<h1>运行面板</h1>
<div class="muted">本机只读状态 + 私聊记忆管理 · 15 秒自动刷新 · {html.escape(health['checked_at'])}</div>

<div class="grid" style="margin-top:16px">{cards}
<div class="card"><div class="muted">长期记忆</div><div><b>{memory_count}</b> 条</div></div>
<div class="card"><div class="muted">翻唱队列</div>
<div>queued <b>{queue['queued']}</b> · running <b>{queue['running']}</b></div>
<div class="muted">failed {queue['failed']} · done {queue['done']}</div></div>
</div>

<h2>私聊记忆</h2>
<form method="get" action="/" style="display:flex;gap:8px;margin-bottom:10px">
<input name="q" value="{query_html}" placeholder="搜索内容或 key" style="flex:1">
<button>搜索</button>
</form>
<table><thead><tr><th>ID</th><th>平台</th><th>会话</th><th>类型</th><th>内容</th><th>重要度</th><th>更新时间</th><th></th></tr></thead>
<tbody>{rows}</tbody></table>

<h2>按用户清空</h2>
<form method="post" action="/memory/clear" class="card" style="display:flex;gap:8px;flex-wrap:wrap">
<select name="platform"><option value="qq">QQ</option><option value="wechat">微信</option></select>
<input name="user_key" placeholder="private:123456" style="flex:1">
<button class="danger">清空这个用户的记忆和摘要</button>
</form>

<h2>日志尾部</h2>
<pre>{html.escape(logs)}</pre>
<div class="muted">日志目录：{html.escape(health['log_dir'])}。不要在这个页面暴露公网地址。</div>
</main></body></html>"""


class PanelHandler(BaseHTTPRequestHandler):
    settings: Settings

    def log_message(self, format, *args):
        return

    def _send(self, status: int, body: bytes, content_type: str = 'text/html; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/health':
            payload = json.dumps(collect_health(self.settings), ensure_ascii=False).encode()
            self._send(200, payload, 'application/json; charset=utf-8')
            return
        if parsed.path == '/api/memories':
            query = parse_qs(parsed.query).get('q', [''])[0]
            items = MemoryStore(self.settings.memory_db).list_all(query=query, limit=300)
            payload = json.dumps([item.__dict__ for item in items], ensure_ascii=False).encode()
            self._send(200, payload, 'application/json; charset=utf-8')
            return
        query = parse_qs(parsed.query).get('q', [''])[0]
        self._send(200, _page(self.settings, query=query).encode('utf-8'))

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0') or 0)
        if length > 64 * 1024:
            self._send(413, b'payload too large', 'text/plain; charset=utf-8')
            return
        body = self.rfile.read(length).decode('utf-8', 'replace')
        form = parse_qs(body)
        store = MemoryStore(self.settings.memory_db)
        if urlparse(self.path).path == '/memory/delete':
            try:
                store.delete_by_id(int(form.get('id', ['0'])[0]))
            except (ValueError, TypeError):
                pass
        elif urlparse(self.path).path == '/memory/clear':
            platform = form.get('platform', ['qq'])[0]
            user_key = form.get('user_key', [''])[0].strip()
            if platform in ('qq', 'wechat') and user_key:
                store.clear(platform, user_key)
        self.send_response(303)
        self.send_header('Location', '/')
        self.end_headers()


def serve(settings: Settings, host: str = '127.0.0.1', port: int = 8767) -> None:
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('panel host must bind to loopback')
    if not 1024 <= port <= 65535:
        raise ValueError('panel port must be 1024..65535')
    PanelHandler.settings = settings
    server = ThreadingHTTPServer((host, port), PanelHandler)
    print(f'panel: http://{host}:{port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
