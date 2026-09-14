import os, re, json, time, mimetypes, uuid, urllib.request, urllib.parse
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

# Расписание: один пост в час начиная с FIRST_HOUR по Москве. GitHub запускает
# cron когда придётся и часть окон пропускает, поэтому скрипт считает, сколько
# постов должно было выйти к текущему часу, и догоняет отставание.
FIRST_HOUR = int(os.environ.get('POST_FIRST_HOUR', '12'))
GAP_SEC = int(os.environ.get('POST_GAP_SEC', '30'))

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

all_posts = sorted(folder.glob('post-*.md'), key=lambda x: int(x.stem.split('-')[1]))
pending = [(p, int(p.stem.split('-')[1])) for p in all_posts
           if int(p.stem.split('-')[1]) not in published]

if not pending:
    print('All posts published')
    exit(0)

hour = datetime.now(msk).hour
due = 0 if hour < FIRST_HOUR else min(hour - FIRST_HOUR + 1, len(all_posts))
behind = due - len(published)

print(f'Час {hour}:00 МСК · подготовлено {len(all_posts)}, опубликовано {len(published)}, '
      f'должно быть {due}')

if behind <= 0:
    print(f'По расписанию публиковать пока нечего (первый пост в {FIRST_HOUR}:00)')
    exit(0)

to_publish = pending[:behind]
if len(to_publish) > 1:
    print(f'Отставание {behind} постов — публикую их за этот прогон с паузой {GAP_SEC} с')
print('К публикации: ' + ', '.join('#' + str(n) for _, n in to_publish))
def prepare(md_path, num):
    """Готовит текст, ссылку для комментария и картинку одного поста."""
    text = md_path.read_text(encoding='utf-8')

    # ── Служебные поля копирайтера не публикуем ──
    text = re.sub(r'^[ \t]*(?:\*\*)?Промпт\s+для\s+картинки(?:\*\*)?\s*:[\s\S]*?(?=\n[ \t]*\n|\Z)',
                  '', text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r'^[ \t]*(?:\*\*)?Знаков(?:\*\*)?\s*:[^\n]*$', '', text,
                  flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r'\n*-{3,}\n*\s*Иллюстрация\s*:.*$', '', text,
                  flags=re.IGNORECASE | re.DOTALL)

    # ── Ссылка в первый комментарий ──
    # Внешние ссылки в теле поста ВК пессимизирует. Копирайтер помечает такую
    # строку как [В КОММЕНТАРИЙ] — вырезаем её из текста и публикуем отдельно.
    comment_text = ''
    m = re.search(r'^[ \t]*\[В\s+КОММЕНТАРИЙ\]\s*(.*)$', text,
                  flags=re.MULTILINE | re.IGNORECASE)
    if m:
        comment_text = m.group(1).strip()
        text = text[:m.start()] + text[m.end():]
    else:
        # Копирайтер иногда пишет «демонстрация в первом комментарии», а саму
        # ссылку ставит в текст или не ставит вовсе. Если пометки нет, но
        # в тексте есть ссылка — выносим её в комментарий сами: иначе внешняя
        # ссылка останется в теле поста и срежет охват.
        promises = re.search(r'в\s+(?:первом\s+)?комментари', text, flags=re.IGNORECASE)
        link = re.search(r'(?:^|\s)((?:https?://|www\.)\S+)', text)
        if link:
            url = link.group(1).rstrip('.,);')
            # Абзац, в котором лежит ссылка, целиком уходит в комментарий
            para_start = text.rfind('\n\n', 0, link.start())
            para_start = 0 if para_start < 0 else para_start + 2
            para_end = text.find('\n\n', link.end())
            para_end = len(text) if para_end < 0 else para_end
            comment_text = text[para_start:para_end].strip()
            text = (text[:para_start] + text[para_end:])
            print(f'   пометки [В КОММЕНТАРИЙ] нет — вынес ссылку в комментарий сам: {url[:60]}')
        elif promises:
            print('   ⚠ в тексте обещан комментарий, но ссылки нет ни с пометкой, '
                  'ни в тексте — комментария не будет. Проверьте промпт копирайтера.')

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

    image = None
    for ext in ('jpg', 'jpeg', 'png', 'webp'):
        cand = folder / f'post-{num}.{ext}'
        if cand.exists():
            image = cand
            break
    return text, comment_text, image


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
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, 'w') as f:
        json.dump({'date': date_str, 'published': sorted(published)}, f,
                  ensure_ascii=False, indent=2)


def publish(md_path, num):
    """Публикует один пост в сообщество. Возвращает True при успехе."""
    text, comment_text, image = prepare(md_path, num)
    print(f'— пост #{num}: {"картинка " + image.name if image else "без картинки"}'
          f'{", ссылка комментарием" if comment_text else ""}')

    attachment = None
    if image:
        attachment = upload_photo(image)
        print(f'   картинка загружена: {attachment}')

    params = {
        'owner_id': '-' + group_id,   # минус означает сообщество, а не человека
        'from_group': 1,              # публикуем от имени сообщества, не автора
        'message': text,
    }
    if attachment:
        params['attachments'] = attachment

    res = vk('wall.post', params)
    post_id = res.get('post_id')
    print(f'   опубликовано: https://vk.com/wall-{group_id}_{post_id}')

    if comment_text:
        try:
            vk('wall.createComment', {
                'owner_id': '-' + group_id,
                'post_id': post_id,
                'from_group': group_id,
                'message': comment_text,
            })
            print('   ссылка добавлена комментарием')
        except Exception as e:
            # Пост уже вышел — из-за комментария не откатываем
            print(f'   комментарий не добавлен: {e}')
    return True


sent = 0
try:
    for i, (md_path, num) in enumerate(to_publish):
        if i:
            time.sleep(GAP_SEC)     # ВК ограничивает частоту записей на стену
        publish(md_path, num)
        published.append(num)
        save_state()                # пишем после каждого — прогон может прерваться
        sent += 1

    print(f'Success! Опубликовано за прогон: {sent}. '
          f'Всего за {date_str}: {len(published)} из {len(all_posts)}')

except Exception as e:
    print(f'Error: {e}')
    if sent:
        print(f'Успело выйти постов: {sent}, состояние сохранено')
    exit(1)
