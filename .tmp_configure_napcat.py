import json
import secrets
from pathlib import Path

config_dir = Path(r'D:\NapCat.Shell\config')
token = secrets.token_hex(16)
secret = secrets.token_hex(16)
files = sorted(config_dir.glob('onebot11_*.json'))
if not files:
    raise SystemExit('no onebot11 config found')
for path in files:
    data = json.loads(path.read_text(encoding='utf-8'))
    network = data.setdefault('network', {})
    network['httpServers'] = [{
        'name': 'wechat-ai-bridge-api',
        'enable': True,
        'port': 3000,
        'host': '127.0.0.1',
        'enableCors': True,
        'enableWebsocket': False,
        'messagePostFormat': 'array',
        'token': token,
        'debug': False,
    }]
    network['httpClients'] = [{
        'name': 'wechat-ai-bridge-webhook',
        'enable': True,
        'url': 'http://127.0.0.1:8081/onebot',
        'messagePostFormat': 'array',
        'reportSelfMessage': False,
        'token': secret,
        'debug': False,
    }]
    backup = path.with_suffix(path.suffix + '.bak')
    if not backup.exists():
        backup.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

env_path = Path('.env')
lines = env_path.read_text(encoding='utf-8-sig').splitlines() if env_path.exists() else []
managed = {
    'QQ_ONEBOT_API_URL': 'http://127.0.0.1:3000',
    'QQ_ONEBOT_TOKEN': token,
    'QQ_WEBHOOK_HOST': '127.0.0.1',
    'QQ_WEBHOOK_PORT': '8081',
    'QQ_WEBHOOK_PATH': '/onebot',
    'QQ_WEBHOOK_SECRET': secret,
    'QQ_STATE_DB': 'data/runtime/qq-chat-state.sqlite3',
    'QQ_PARTICIPATION_MODE': 'natural',
    'QQ_COOLDOWN_SECONDS': '5',
    'QQ_HOURLY_QUOTA': '60',
    'QQ_SPONTANEOUS_COOLDOWN_SECONDS': '5',
    'QQ_VOICE_ENABLED': 'true',
    'QQ_GPT_SOVITS_URL': 'http://127.0.0.1:9880',
    'QQ_GPT_SOVITS_REF_AUDIO': 'data/runtime/gpt-sovits/reference.wav',
    'QQ_GPT_SOVITS_PROMPT_TEXT': '',
    'QQ_GPT_SOVITS_PROMPT_LANG': 'zh',
    'QQ_GPT_SOVITS_TEXT_LANG': 'zh',
    'QQ_GPT_SOVITS_TIMEOUT_SECONDS': '30',
    'QQ_VOICE_MAX_CHARS': '120',
    'QQ_ALLOWED_GROUPS': '',
}
kept = []
for line in lines:
    key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
    if key in managed:
        continue
    kept.append(line)
while kept and not kept[-1].strip():
    kept.pop()
if kept:
    kept.append('')
kept.append('# QQ / NapCat OneBot 11')
for key, value in managed.items():
    kept.append(f'{key}={value}')
env_path.write_text('\n'.join(kept) + '\n', encoding='utf-8')
print('patched', [p.name for p in files])
print('env', env_path.resolve())
print('token/secret stored in NapCat config and .env (not printed)')
