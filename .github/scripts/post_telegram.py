import os, re, json, mimetypes, uuid, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

msk = timezone(timedelta(hours=3))
date_str = datetime.now(msk).strftime('%d.%m.%Y')
folder = Path(f'social/posting/{date_str}/dzen')
state_file = folder / 'state-dzen.json'

CHAT_ID = '@HotelAI_ru'
CAPTION_LIMIT = 1024   # лимит подписи к фото в Telegram
TEXT_LIMIT = 4096      # лимит обычного сообщения

if not folder.exists():
    print(f'No folder: {folder}')
    exit(0)

published = []
if state_file.exists():
    with open(state_file) as f:
        published = json.load(f).get('published', [])
print(f'Published: {published}')

next_file = next_num = None
for p in sorted(folder.glob('post-*.md'), key=lambda x: int(x.stem.split('-')[1])):
    n = int(p.stem.split('-')[1])
    if n not in published:
        next_file, next_num = p, n
        break

if not next_file:
    print('All posts published')
    exit(0)

print(f'Publishing post #{next_num}')
text = next_file.read_text(encoding='utf-8')

# ── Служебные поля копирайтера не публикуем ──
# Дашборд с fix36 чистит их при отправке, но в старых файлах они остались,
# и «Промпт для картинки» с «Знаков: 1187» уже улетали в канал.
text = re.sub(r'^[ \t]*(?:\*\*)?Промпт\s+для\s+картинки(?:\*\*)?\s*:[\s\S]*?(?=\n[ \t]*\n|\Z)',
              '', text, flags=re.MULTILINE | re.IGNORECASE)
text = re.sub(r'^[ \t]*(?:\*\*)?Знаков(?:\*\*)?\s*:[^\n]*$', '', text,
              flags=re.MULTILINE | re.IGNORECASE)
# Старая строка со ссылкой на картинку — она отправлялась как текст
text = re.sub(r'\n*-{3,}\n*\s*Иллюстрация\s*:.*$', '', text, flags=re.IGNORECASE | re.DOTALL)

# Заголовок публикации сохраняем — раньше первая строка «# …» просто
# удалялась, и пост в канале начинался сразу с текста.
text = text.strip()
text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
text = re.sub(r'^#{1,3}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
text = re.sub(r'^\-\s+', '• ', text, flags=re.MULTILINE)
text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
# Слово «Хэштеги:» — служебная метка копирайтера, в канале нужны сами теги
text = re.sub(r'^[ \t]*(?:<b>)?Хэштеги(?:</b>)?\s*:\s*', '', text,
              flags=re.MULTILINE | re.IGNORECASE)
text = re.sub(r'\n{3,}', '\n\n', text).strip()

# ── Ищем картинку, положенную дашбордом рядом с текстом ──
image = None
for ext in ('jpg', 'jpeg', 'png', 'webp'):
    cand = folder / f'post-{next_num}.{ext}'
    if cand.exists():
        image = cand
        break
print(f'Image: {image if image else "нет — публикую только текст"}')

token = os.environ['TELEGRAM_BOT_TOKEN']


def api(method, payload):
    """Обычный JSON-запрос к Bot API."""
    url = f'https://api.telegram.org/bot{token}/{method}'
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def api_photo(path, caption=None):
    """sendPhoto с файлом. Собираем multipart руками, чтобы не тянуть зависимости."""
    url = f'https://api.telegram.org/bot{token}/sendPhoto'
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or 'image/jpeg'

    def field(name, value):
        return (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f'{value}\r\n').encode()

    body = bytearray()
    body += field('chat_id', CHAT_ID)
    if caption:
        body += field('caption', caption)
        body += field('parse_mode', 'HTML')
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
             f'filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n').encode()
    body += path.read_bytes()
    body += f'\r\n--{boundary}--\r\n'.encode()

    req = urllib.request.Request(
        url, data=bytes(body),
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def save_state():
    published.append(next_num)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, 'w') as f:
        json.dump({'date': date_str, 'published': sorted(published)}, f,
                  ensure_ascii=False, indent=2)


try:
    if image and len(text) <= CAPTION_LIMIT:
        # Короткий пост — картинка и текст одним сообщением, так выглядит лучше
        print(f'sendPhoto с подписью ({len(text)} симв.)')
        result = api_photo(image, caption=text)
        if not result.get('ok'):
            print(f'Telegram error: {result}')
            exit(1)

    elif image:
        # Подпись не влезает (лимит 1024) — фото, затем текст отдельным сообщением
        print(f'Текст {len(text)} симв. — длиннее подписи. Отправляю фото, затем текст.')
        result = api_photo(image)
        if not result.get('ok'):
            print(f'Telegram error (photo): {result}')
            exit(1)
        body = text[:TEXT_LIMIT - 6] + '...' if len(text) > TEXT_LIMIT else text
        result = api('sendMessage', {'chat_id': CHAT_ID, 'text': body,
                                     'parse_mode': 'HTML'})
        if not result.get('ok'):
            # Фото уже ушло — в состояние пост не пишем, иначе текст потеряется
            print(f'Telegram error (text): {result}')
            exit(1)

    else:
        body = text[:TEXT_LIMIT - 6] + '...' if len(text) > TEXT_LIMIT else text
        result = api('sendMessage', {'chat_id': CHAT_ID, 'text': body,
                                     'parse_mode': 'HTML'})
        if not result.get('ok'):
            print(f'Telegram error: {result}')
            exit(1)

    print('Success!')
    save_state()

except Exception as e:
    print(f'Error: {e}')
    exit(1)
