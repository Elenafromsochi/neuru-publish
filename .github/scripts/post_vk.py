import os, re, json, mimetypes, uuid, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Автопостинг в сообщество ВКонтакте.
# Логика та же, что у Telegram: берём первый неопубликованный пост за сегодня
# из social/posting/<дата>/vk и публикуем. Отметки — в state-vk.json,
# отдельно от телеграмных, чтобы счётчики площадок не пересекались.

msk = timezone(timedelta(hours=3))
date_str = datetime.now(msk).strftime('%d.%m.%Y')
folder = Path(f'social/posting/{date_str}/vk')
state_file = folder / 'state-vk.json'

API = 'https://api.vk.com/method/'
API_VERSION = '5.199'
TEXT_LIMIT = 15000   # технический лимит ВК заметно выше, но дальше падает вовлечение

if not folder.exists():
    print(f'No folder: {folder}')
    exit(0)

token = os.environ.get('VK_ACCESS_TOKEN', '').strip()
group_id = os.environ.get('VK_GROUP_ID', '').strip().lstrip('-')
if not token:
    print('Не задан секрет VK_ACCESS_TOKEN')
    exit(1)
if not group_id.isdigit():
    print(f'VK_GROUP_ID должен быть числом без минуса, получено: "{group_id}"')
    exit(1)

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
text = re.sub(r'^[ \t]*(?:\*\*)?Промпт\s+для\s+картинки(?:\*\*)?\s*:[\s\S]*?(?=\n[ \t]*\n|\Z)',
              '', text, flags=re.MULTILINE | re.IGNORECASE)
text = re.sub(r'^[ \t]*(?:\*\*)?Знаков(?:\*\*)?\s*:[^\n]*$', '', text,
              flags=re.MULTILINE | re.IGNORECASE)
text = re.sub(r'\n*-{3,}\n*\s*Иллюстрация\s*:.*$', '', text, flags=re.IGNORECASE | re.DOTALL)

# ── Ссылка в первый комментарий ──
# Внешние ссылки в теле поста ВК пессимизирует. Копирайтер помечает такую
# строку как [В КОММЕНТАРИЙ] — вырезаем её из текста и публикуем отдельно.
comment_text = ''
m = re.search(r'^[ \t]*\[В\s+КОММЕНТАРИЙ\]\s*(.*)$', text, flags=re.MULTILINE | re.IGNORECASE)
if m:
    comment_text = m.group(1).strip()
    text = text[:m.start()] + text[m.end():]
    print(f'Ссылка уйдёт комментарием: {comment_text[:80]}')

# ВК не понимает markdown — снимаем разметку, заголовок оставляем строкой
text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
text = re.sub(r'\*([^*\n]+)\*', r'\1', text)
text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 \2', text)
text = re.sub(r'^[ \t]*Хэштеги\s*:\s*', '', text, flags=re.MULTILINE | re.IGNORECASE)
text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
text = re.sub(r'\n{3,}', '\n\n', text).strip()
if len(text) > TEXT_LIMIT:
    text = text[:TEXT_LIMIT - 3] + '...'

# ── Картинка ──
image = None
for ext in ('jpg', 'jpeg', 'png', 'webp'):
    cand = folder / f'post-{next_num}.{ext}'
    if cand.exists():
        image = cand
        break
print(f'Image: {image if image else "нет — публикую только текст"}')


def vk(method, params):
    """Вызов метода VK API. Ошибки приходят с кодом 200, поэтому проверяем тело."""
    data = dict(params)
    data['access_token'] = token
    data['v'] = API_VERSION
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(API + method, data=body)
    with urllib.request.urlopen(req) as resp:
        out = json.loads(resp.read())
    if 'error' in out:
        e = out['error']
        raise RuntimeError(f"VK {method}: код {e.get('error_code')} — {e.get('error_msg')}")
    return out.get('response')


def upload_photo(path):
    """Загрузка картинки на стену: получить сервер, отправить файл, сохранить."""
    srv = vk('photos.getWallUploadServer', {'group_id': group_id})
    url = srv['upload_url']

    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or 'image/jpeg'
    blob = bytearray()
    blob += (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
             f'filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n').encode()
    blob += path.read_bytes()
    blob += f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(
        url, data=bytes(blob),
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req) as resp:
        up = json.loads(resp.read())
    if not up.get('photo') or up.get('photo') == '[]':
        raise RuntimeError(f'ВК не принял файл при загрузке: {up}')

    saved = vk('photos.saveWallPhoto', {
        'group_id': group_id,
        'photo': up['photo'],
        'server': up['server'],
        'hash': up['hash'],
    })
    ph = saved[0]
    return f"photo{ph['owner_id']}_{ph['id']}"


def save_state():
    published.append(next_num)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, 'w') as f:
        json.dump({'date': date_str, 'published': sorted(published)}, f,
                  ensure_ascii=False, indent=2)


try:
    attachment = None
    if image:
        attachment = upload_photo(image)
        print(f'Картинка загружена: {attachment}')

    params = {
        'owner_id': '-' + group_id,   # минус означает сообщество, а не человека
        'from_group': 1,              # публикуем от имени сообщества, не автора
        'message': text,
    }
    if attachment:
        params['attachments'] = attachment

    res = vk('wall.post', params)
    post_id = res.get('post_id')
    print(f'Опубликовано: https://vk.com/wall-{group_id}_{post_id}')

    if comment_text:
        try:
            vk('wall.createComment', {
                'owner_id': '-' + group_id,
                'post_id': post_id,
                'from_group': group_id,
                'message': comment_text,
            })
            print('Ссылка добавлена комментарием')
        except Exception as e:
            # Пост уже вышел — из-за комментария не откатываем
            print(f'Комментарий не добавлен: {e}')

    save_state()
    print('Success!')

except Exception as e:
    print(f'Error: {e}')
    exit(1)
