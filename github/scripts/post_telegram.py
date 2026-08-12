import os, re, json, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Московское время
msk = timezone(timedelta(hours=3))
date_str = datetime.now(msk).strftime('%d.%m.%Y')

folder = Path(f'social/posting/{date_str}/dzen')
state_file = folder / 'state-dzen.json'

if not folder.exists():
    print(f'No folder: {folder}')
    exit(0)

# Читаем состояние
published = []
if state_file.exists():
    with open(state_file) as f:
        published = json.load(f).get('published', [])

print(f'Published: {published}')

# Находим следующий пост
next_file = None
next_num = None
for p in sorted(folder.glob('post-*.md'), key=lambda x: int(x.stem.split('-')[1])):
    n = int(p.stem.split('-')[1])
    if n not in published:
        next_file = p
        next_num = n
        break

if not next_file:
    print('All posts published')
    exit(0)

print(f'Publishing post #{next_num}: {next_file}')

# Читаем и конвертируем текст
text = next_file.read_text(encoding='utf-8')
lines = text.split('\n')
if lines and lines[0].startswith('# '):
    lines = lines[1:]
text = '\n'.join(lines).strip()

text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
text = re.sub(r'_(.+?)_', r'<i>\1</i>', text)
text = re.sub(r'^#{1,3}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
text = re.sub(r'^\-\s+', '• ', text, flags=re.MULTILINE)
text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
text = re.sub(r'\n{3,}', '\n\n', text)
if len(text) > 4096:
    text = text[:4090] + '...'

# Отправляем через Worker
worker_url = os.environ['WORKER_URL']
worker_secret = os.environ['WORKER_SECRET']

payload = json.dumps({
    'method': 'sendMessage',
    'params': {
        'chat_id': '@HotelAI_ru',
        'text': text,
        'parse_mode': 'HTML'
    }
}).encode('utf-8')

req = urllib.request.Request(
    worker_url,
    data=payload,
    headers={
        'Content-Type': 'application/json',
        'X-Worker-Secret': worker_secret
    }
)

try:
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
        print(f'Response: {result}')
        if result.get('ok'):
            print(f'Success!')
            published.append(next_num)
            state = {'date': date_str, 'platform': 'dzen', 'published': sorted(published), 'last_post': next_num}
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        else:
            print(f'Telegram error: {result}')
            exit(1)
except Exception as e:
    print(f'Error: {e}')
    exit(1)
