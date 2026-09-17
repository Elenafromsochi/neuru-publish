import os, re, json, time, mimetypes, uuid, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Автопостинг в сообщество ВКонтакте.
# Берём первый неопубликованный пост за сегодня из social/posting/<дата>/vk
# и публикуем. Отметки — в state-vk.json, отдельно от телеграмных.
#
# fix54:
# — не больше POST_MAX_PER_RUN постов за прогон (по умолчанию 1) и не чаще,
#   чем раз в POST_MIN_GAP_MIN минут (по умолчанию 45). Раньше отставание
#   догонялось пачкой с паузой 30 с, а ВК режет охват за серии постов;
# — время последней публикации хранится в state-vk.json;
# — картинка грузится ключом VK_USER_TOKEN, если он задан: метод
#   photos.getWallUploadServer не работает с ключом сообщества (ошибка 27);
# — если картинку загрузить не удалось, пост выходит без неё.
#
# fix54b: фото пробуется загрузить всеми заданными ключами по очереди,
#   в журнал пишется вид каждого ключа (пользователя или сообщества),
#   сами ключи не выводятся.

msk = timezone(timedelta(hours=3))
now = datetime.now(msk)
date_str = now.strftime('%d.%m.%Y')
folder = Path(f'social/posting/{date_str}/vk')
state_file = folder / 'state-vk.json'

API = 'https://api.vk.com/method/'
API_VERSION = '5.199'
TEXT_LIMIT = 15000

FIRST_HOUR = int(os.environ.get('POST_FIRST_HOUR', '12'))
MAX_PER_RUN = max(1, int(os.environ.get('POST_MAX_PER_RUN', '1')))
MIN_GAP_MIN = max(0, int(os.environ.get('POST_MIN_GAP_MIN', '45')))
GAP_SEC = int(os.environ.get('POST_GAP_SEC', '30'))

if not folder.exists():
    print(f'Папки {folder} нет — на сегодня посты ВК не одобрены. '
          f'Одобрите их в ИИ-Копирайтере-Публицисте на вкладке ВКонтакте.')
    exit(0)

token = os.environ.get('VK_ACCESS_TOKEN', '').strip()
user_token = os.environ.get('VK_USER_TOKEN', '').strip()
group_id = os.environ.get('VK_GROUP_ID', '').strip().lstrip('-')
if not token:
    print('Не задан секрет VK_ACCESS_TOKEN')
    exit(1)
if not group_id.isdigit():
    print(f'VK_GROUP_ID должен быть числом без минуса, получено: "{group_id}"')
    exit(1)

published, last_at = [], None
if state_file.exists():
    with open(state_file) as f:
        st = json.load(f)
    published = st.get('published', [])
    if st.get('last_at'):
        try:
            last_at = datetime.fromisoformat(st['last_at'])
        except ValueError:
            last_at = None
print(f'Опубликовано ранее: {published}'
      + (f', последний пост в {last_at.astimezone(msk):%H:%M} МСК' if last_at else ''))

all_posts = sorted(folder.glob('post-*.md'), key=lambda x: int(x.stem.split('-')[1]))
pending = [(p, int(p.stem.split('-')[1])) for p in all_posts
           if int(p.stem.split('-')[1]) not in published]

if not pending:
    print(f'Все одобренные посты за {date_str} уже вышли: {len(published)} из {len(all_posts)}. '
          f'Чтобы публикации продолжились, одобрите следующие в ИИ-Копирайтере-Публицисте.')
    exit(0)

hour = now.hour
due = 0 if hour < FIRST_HOUR else min(hour - FIRST_HOUR + 1, len(all_posts))
behind = due - len(published)

print(f'Час {hour}:00 МСК · одобрено {len(all_posts)}, опубликовано {len(published)}, '
      f'по расписанию должно быть {due}')

if behind <= 0:
    print(f'По расписанию публиковать пока нечего (первый пост в {FIRST_HOUR}:00, дальше по одному в час)')
    exit(0)

if last_at and MIN_GAP_MIN:
    passed = (now - last_at).total_seconds() / 60
    if passed < MIN_GAP_MIN:
        print(f'Прошлый пост вышел {int(passed)} мин назад — жду {MIN_GAP_MIN} мин между постами. '
              f'Отставание {behind}, догоню в следующих прогонах.')
        exit(0)

to_publish = pending[:min(behind, MAX_PER_RUN)]
if behind > len(to_publish):
    print(f'Отставание {behind}: публикую {len(to_publish)} сейчас, остальные — в следующих прогонах')
print('К публикации: ' + ', '.join('#' + str(n) for _, n in to_publish))


def prepare(md_path, num):
    """Готовит текст, ссылку для комментария и картинку одного поста."""
    text = md_path.read_text(encoding='utf-8')

    # Служебные поля копирайтера не публикуем
    text = re.sub(r'^[ \t]*(?:\*\*)?Промпт\s+для\s+картинки(?:\*\*)?\s*:[\s\S]*?(?=\n[ \t]*\n|\Z)',
                  '', text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r'^[ \t]*(?:\*\*)?Знаков(?:\*\*)?\s*:[^\n]*$', '', text,
                  flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r'\n*-{3,}\n*\s*Иллюстрация\s*:.*$', '', text,
                  flags=re.IGNORECASE | re.DOTALL)

    # Ссылка в первый комментарий: внешние ссылки в теле поста ВК пессимизирует
    comment_text = ''
    m = re.search(r'^[ \t]*\[В\s+КОММЕНТАРИЙ\]\s*(.*)$', text,
                  flags=re.MULTILINE | re.IGNORECASE)
    if m:
        comment_text = m.group(1).strip()
        text = text[:m.start()] + text[m.end():]
    else:
        promises = re.search(r'в\s+(?:первом\s+)?комментари', text, flags=re.IGNORECASE)
        link = re.search(r'(?:^|\s)((?:https?://|www\.)\S+)', text)
        if link:
            url = link.group(1).rstrip('.,);')
            para_start = text.rfind('\n\n', 0, link.start())
            para_start = 0 if para_start < 0 else para_start + 2
            para_end = text.find('\n\n', link.end())
            para_end = len(text) if para_end < 0 else para_end
            comment_text = text[para_start:para_end].strip()
            text = text[:para_start] + text[para_end:]
            print(f'   пометки [В КОММЕНТАРИЙ] нет — вынес ссылку в комментарий сам: {url[:60]}')
        elif promises:
            print('   ⚠ в тексте обещан комментарий, но ссылки нет — комментария не будет')

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


def vk(method, params, use_token=None):
    """Вызов метода VK API. Ошибки приходят с кодом 200, поэтому проверяем тело."""
    data = dict(params)
    data['access_token'] = use_token or token
    data['v'] = API_VERSION
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(API + method, data=body)
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = json.loads(resp.read())
    if 'error' in out:
        e = out['error']
        raise RuntimeError(f"VK {method}: код {e.get('error_code')} — {e.get('error_msg')}")
    return out.get('response')


def token_kind(tok):
    """Определяет вид ключа, не раскрывая его: метод
    groups.getTokenPermissions отвечает только ключу сообщества."""
    try:
        vk('groups.getTokenPermissions', {}, tok)
        return 'ключ сообщества'
    except Exception:
        pass
    try:
        u = vk('users.get', {}, tok)
        if u:
            return f"ключ пользователя (id{u[0].get('id')})"
    except Exception as e:
        return f'ключ не принят: {e}'
    return 'вид ключа не определён'


def photo_tokens():
    """Ключи-кандидаты для загрузки фото: сначала VK_USER_TOKEN, потом основной."""
    out, seen = [], set()
    for name, tok in (('VK_USER_TOKEN', user_token), ('VK_ACCESS_TOKEN', token)):
        if tok and tok not in seen:
            seen.add(tok)
            out.append((name, tok))
    return out


_tokens_checked = False


def upload_photo(path):
    """Загрузка картинки на стену: получить сервер, отправить файл, сохранить.
    Методы фото работают только с ключом пользователя-администратора,
    поэтому пробуем по очереди все заданные ключи."""
    global _tokens_checked
    cands = photo_tokens()
    if not _tokens_checked:
        _tokens_checked = True
        if not user_token:
            print('   секрет VK_USER_TOKEN пуст или не передан в workflow')
        for name, tok in cands:
            print(f'   {name}: {token_kind(tok)}')

    errors = []
    for name, tok in cands:
        try:
            srv = vk('photos.getWallUploadServer', {'group_id': group_id}, tok)
        except Exception as e:
            errors.append(f'{name}: {e}')
            continue
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
        with urllib.request.urlopen(req, timeout=120) as resp:
            up = json.loads(resp.read())
        if not up.get('photo') or up.get('photo') == '[]':
            raise RuntimeError(f'ВК не принял файл при загрузке: {up}')

        saved = vk('photos.saveWallPhoto', {
            'group_id': group_id,
            'photo': up['photo'],
            'server': up['server'],
            'hash': up['hash'],
        }, tok)
        ph = saved[0]
        print(f'   фото загружено ключом {name}')
        return f"photo{ph['owner_id']}_{ph['id']}"

    raise RuntimeError('ни один ключ не подошёл для загрузки фото — ' + ' | '.join(errors)
                       + '. Нужен ключ пользователя-администратора сообщества с правом photos '
                       + 'в секрете VK_USER_TOKEN')


def save_state():
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, 'w') as f:
        json.dump({
            'date': date_str,
            'published': sorted(published),
            'last_at': last_at.isoformat() if last_at else None,
        }, f, ensure_ascii=False, indent=2)


def publish(md_path, num):
    """Публикует один пост в сообщество."""
    text, comment_text, image = prepare(md_path, num)
    print(f'— пост #{num}: {"картинка " + image.name if image else "без картинки"}'
          f'{", ссылка комментарием" if comment_text else ""}')

    attachment = None
    if image:
        try:
            attachment = upload_photo(image)
            print(f'   картинка загружена: {attachment}')
        except Exception as e:
            # Пост важнее картинки: выходит без неё, а в журнале видна причина
            print(f'::warning title=VK Posting::пост #{num} выходит без картинки — {e}')
            print(f'   ⚠ картинку загрузить не удалось, публикую без неё: {e}')

    params = {
        'owner_id': '-' + group_id,   # минус означает сообщество, а не человека
        'from_group': 1,              # публикуем от имени сообщества
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
            print(f'   комментарий не добавлен: {e}')


sent = 0
try:
    for i, (md_path, num) in enumerate(to_publish):
        if i:
            time.sleep(GAP_SEC)
        publish(md_path, num)
        published.append(num)
        last_at = datetime.now(msk)
        save_state()                # пишем после каждого — прогон может прерваться
        sent += 1

    left = len(all_posts) - len(published)
    print(f'Success! Опубликовано за прогон: {sent}. '
          f'Всего за {date_str}: {len(published)} из {len(all_posts)}'
          + (f', в очереди ещё {left}' if left else ''))

except Exception as e:
    print(f'::error title=VK Posting::{e}')
    print(f'Error: {e}')
    if sent:
        print(f'Успело выйти постов: {sent}, состояние сохранено')
    exit(1)
