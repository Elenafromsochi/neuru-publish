import os, re, json, time, mimetypes, uuid, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Автопостинг в сообщество ВКонтакте.
#
# fix58: если загрузка на стену недоступна (ключ сообщества, ошибка 27),
#   картинка грузится через сервер фото для сообщений — этот путь работает
#   с ключом сообщества. Нужны только VK_ACCESS_TOKEN и VK_GROUP_ID.
#
# fix57: ссылка в первом комментарии есть всегда: если в посте нет строки
#   [В КОММЕНТАРИЙ] и ссылки, ставится стандартная ссылка с UTM.
#   Комментарий пробуется обоими ключами, ошибка видна в Annotations.
#
# fix56: общая очередь за несколько дней.
# — В очередь попадают неопубликованные посты из social/posting/<дата>/vk
#   за сегодня и за прошлые дни (POST_BACKLOG_DAYS, по умолчанию 14).
#   Порядок: от старой даты к новой, внутри дня — по номеру поста.
# — Посты выходят по одному в час с POST_FIRST_HOUR до POST_LAST_HOUR
#   по Москве (10:00–21:00), не больше одного за прогон и не чаще раза
#   в POST_MIN_GAP_MIN минут. Что не поместилось, переходит на завтра.
# — Отметки о выходе хранятся в state-vk.json каждой папки дня,
#   а журнал публикаций по дням — в social/posting/vk-log.json.
#
# fix54b: фото пробуется загрузить всеми заданными ключами по очереди,
#   в журнал пишется вид каждого ключа, сами ключи не выводятся.
# fix54: если картинку загрузить не удалось, пост выходит без неё.

msk = timezone(timedelta(hours=3))
now = datetime.now(msk)
today = now.date()
ROOT = Path('social/posting')
LOG_FILE = ROOT / 'vk-log.json'

API = 'https://api.vk.com/method/'
API_VERSION = '5.199'
TEXT_LIMIT = 15000

FIRST_HOUR = int(os.environ.get('POST_FIRST_HOUR', '10'))
LAST_HOUR = int(os.environ.get('POST_LAST_HOUR', '21'))
MAX_PER_RUN = max(1, int(os.environ.get('POST_MAX_PER_RUN', '1')))
MIN_GAP_MIN = max(0, int(os.environ.get('POST_MIN_GAP_MIN', '45')))
GAP_SEC = int(os.environ.get('POST_GAP_SEC', '30'))
BACKLOG_DAYS = max(0, int(os.environ.get('POST_BACKLOG_DAYS', '14')))

token = os.environ.get('VK_ACCESS_TOKEN', '').strip()
user_token = os.environ.get('VK_USER_TOKEN', '').strip()
group_id = os.environ.get('VK_GROUP_ID', '').strip().lstrip('-')
if not token:
    print('Не задан секрет VK_ACCESS_TOKEN')
    exit(1)
if not group_id.isdigit():
    print(f'VK_GROUP_ID должен быть числом без минуса, получено: "{group_id}"')
    exit(1)


# ── Чтение очереди ──

def read_state(folder):
    f = folder / 'state-vk.json'
    if not f.exists():
        return {'published': [], 'last_at': None}
    try:
        with open(f) as fh:
            st = json.load(fh)
        return {'published': st.get('published', []), 'last_at': st.get('last_at'),
                'date': st.get('date')}
    except Exception:
        return {'published': [], 'last_at': None}


def write_state(folder, st):
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / 'state-vk.json', 'w') as fh:
        json.dump({'date': folder.parent.name,
                   'published': sorted(st['published']),
                   'last_at': st.get('last_at')}, fh, ensure_ascii=False, indent=2)


def parse_iso(v):
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


folders = []
if ROOT.exists():
    for d in ROOT.iterdir():
        try:
            day = datetime.strptime(d.name, '%d.%m.%Y').date()
        except ValueError:
            continue
        if (d / 'vk').is_dir() and 0 <= (today - day).days <= BACKLOG_DAYS:
            folders.append((day, d / 'vk'))
folders.sort()

queue, states = [], {}
last_at = None
for day, folder in folders:
    st = read_state(folder)
    states[folder] = st
    t = parse_iso(st.get('last_at'))
    if t and (last_at is None or t > last_at):
        last_at = t
    for p in sorted(folder.glob('post-*.md'), key=lambda x: int(x.stem.split('-')[1])):
        n = int(p.stem.split('-')[1])
        if n not in st['published']:
            queue.append((day, folder, p, n))

# Журнал: сколько постов уже вышло сегодня
log = {}
if LOG_FILE.exists():
    try:
        with open(LOG_FILE) as fh:
            log = json.load(fh) or {}
    except Exception:
        log = {}
today_key = today.isoformat()
if today_key in log:
    published_today = len(log[today_key])
else:
    # Первый запуск с журналом: до этого публиковалась только папка сегодняшнего дня
    tf = ROOT / today.strftime('%d.%m.%Y') / 'vk'
    t = parse_iso(states.get(tf, {}).get('last_at')) if tf in states else None
    published_today = len(states[tf]['published']) if tf in states and t and t.date() == today else 0

old = [q for q in queue if q[0] < today]
print(f'Очередь ВК: {len(queue)} постов'
      + (f', из них за прошлые дни {len(old)} ('
         + ', '.join(sorted({q[0].strftime("%d.%m") for q in old})) + ')' if old else '')
      + f'. Сегодня уже вышло: {published_today}'
      + (f', последний пост в {last_at.astimezone(msk):%d.%m %H:%M} МСК' if last_at else ''))

if not queue:
    print('Неопубликованных постов нет. Одобрите следующие в ИИ-Копирайтере-Публицисте.')
    exit(0)

hour = now.hour
if hour < FIRST_HOUR:
    print(f'Рано: публикации идут с {FIRST_HOUR}:00 до {LAST_HOUR}:00 по Москве')
    exit(0)
if hour > LAST_HOUR + 1:
    print(f'Поздно: окно публикаций закрылось в {LAST_HOUR}:00, очередь продолжится завтра с {FIRST_HOUR}:00')
    exit(0)

span = LAST_HOUR - FIRST_HOUR + 1
due = min(hour - FIRST_HOUR + 1, span)
behind = due - published_today
print(f'Час {hour}:00 МСК · к этому часу должно выйти {due}, вышло {published_today}')
if behind <= 0:
    print(f'По расписанию пока рано: следующий пост в {min(FIRST_HOUR + published_today, LAST_HOUR)}:00'
          if published_today < span else f'Дневной лимит {span} постов выбран — остальное завтра')
    exit(0)

if last_at and MIN_GAP_MIN:
    passed = (now - last_at).total_seconds() / 60
    if passed < MIN_GAP_MIN:
        print(f'Прошлый пост вышел {int(passed)} мин назад — жду {MIN_GAP_MIN} мин между постами.')
        exit(0)

to_publish = queue[:min(behind, MAX_PER_RUN)]
print('К публикации: ' + ', '.join(f'{q[0]:%d.%m} №{q[3]}' for q in to_publish))


def prepare(md_path, num, folder):
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
        if not re.search(r'https?://', comment_text):
            day_tag = folder.parent.name[:2]
            comment_text = (comment_text.rstrip(' :') + ': https://neuru.ru/ai-porter?utm_source=vk'
                            f'&utm_medium=publication&utm_campaign=pub&utm_content={day_tag}-{num}')
    else:
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
        else:
            # fix58: если загрузка на стену недоступна (ключ сообщества, ошибка 27),
#   картинка грузится через сервер фото для сообщений — этот путь работает
#   с ключом сообщества. Нужны только VK_ACCESS_TOKEN и VK_GROUP_ID.
#
# fix57: ни пометки, ни ссылки — комментарий всё равно нужен
            day_tag = folder.parent.name[:2]
            url = ('https://neuru.ru/ai-porter?utm_source=vk&utm_medium=publication'
                   f'&utm_campaign=pub&utm_content={day_tag}-{num}')
            comment_text = f'Послушать, как отвечает ИИ-портье: {url}'
            print('   в посте нет ни строки [В КОММЕНТАРИЙ], ни ссылки — ставлю стандартную ссылку в комментарий')

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


def send_file(upload_url, path):
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or 'image/jpeg'
    blob = bytearray()
    blob += (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
             f'filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n').encode()
    blob += path.read_bytes()
    blob += f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(
        upload_url, data=bytes(blob),
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req, timeout=120) as resp:
        up = json.loads(resp.read())
    if not up.get('photo') or up.get('photo') == '[]':
        raise RuntimeError(f'ВК не принял файл при загрузке: {up}')
    return up


def upload_via_messages(path):
    """fix58: загрузка через сервер фото для сообщений. Этот путь работает
    с ключом сообщества — те же два секрета, что и для текста."""
    last = None
    for params in ({}, {'peer_id': 0}):
        try:
            srv = vk('photos.getMessagesUploadServer', params, token)
            break
        except Exception as e:
            last = e
    else:
        raise RuntimeError(f'сервер загрузки для сообщений не выдан: {last}')
    up = send_file(srv['upload_url'], path)
    saved = vk('photos.saveMessagesPhoto', {
        'photo': up['photo'], 'server': up['server'], 'hash': up['hash'],
    }, token)
    ph = saved[0]
    att = f"photo{ph['owner_id']}_{ph['id']}"
    if ph.get('access_key'):
        att += '_' + ph['access_key']
    print('   фото загружено через сервер сообщений ключом VK_ACCESS_TOKEN')
    return att


_tokens_checked = False


def upload_photo(path):
    """Загрузка картинки на стену: получить сервер, отправить файл, сохранить.
    Методы фото работают только с ключом пользователя-администратора,
    поэтому пробуем по очереди все заданные ключи."""
    global _tokens_checked
    cands = photo_tokens()
    if not _tokens_checked:
        _tokens_checked = True
        for name, tok in cands:
            print(f'   {name}: {token_kind(tok)}')

    errors = []
    for name, tok in cands:
        try:
            srv = vk('photos.getWallUploadServer', {'group_id': group_id}, tok)
        except Exception as e:
            errors.append(f'{name}: {e}')
            continue
        up = send_file(srv['upload_url'], path)

        saved = vk('photos.saveWallPhoto', {
            'group_id': group_id,
            'photo': up['photo'],
            'server': up['server'],
            'hash': up['hash'],
        }, tok)
        ph = saved[0]
        print(f'   фото загружено ключом {name}')
        return f"photo{ph['owner_id']}_{ph['id']}"

    try:
        return upload_via_messages(path)
    except Exception as e:
        errors.append(f'сервер сообщений: {e}')
    raise RuntimeError('картинку загрузить не удалось — ' + ' | '.join(errors))




def remember(day, folder, num):
    global last_at
    st = states.setdefault(folder, {'published': [], 'last_at': None})
    st['published'].append(num)
    last_at = datetime.now(msk)
    st['last_at'] = last_at.isoformat()
    write_state(folder, st)
    log.setdefault(today_key, []).append(
        {'date': day.strftime('%d.%m.%Y'), 'num': num, 'at': last_at.isoformat()})
    keep = sorted(log)[-30:]
    with open(LOG_FILE, 'w') as fh:
        json.dump({k: log[k] for k in keep}, fh, ensure_ascii=False, indent=2)


def publish(md_path, num, folder, day):
    """Публикует один пост в сообщество."""
    text, comment_text, image = prepare(md_path, num, folder)
    label = f'{day:%d.%m} №{num}'
    print(f'— пост {label}: {"картинка " + image.name if image else "без картинки"}'
          f'{", ссылка комментарием" if comment_text else ""}')

    attachment = None
    if image:
        try:
            attachment = upload_photo(image)
            print(f'   картинка загружена: {attachment}')
        except Exception as e:
            print(f'::warning title=VK Posting::пост {label} выходит без картинки — {e}')
            print(f'   ⚠ картинку загрузить не удалось, публикую без неё: {e}')

    params = {
        'owner_id': '-' + group_id,
        'from_group': 1,
        'message': text,
    }
    if attachment:
        params['attachments'] = attachment

    try:
        res = vk('wall.post', params)
    except Exception as e:
        if not attachment:
            raise
        print(f'::warning title=VK Posting::стена не приняла картинку к посту {label} — {e}')
        print(f'   ⚠ стена не приняла картинку, публикую без неё: {e}')
        params.pop('attachments', None)
        res = vk('wall.post', params)
    post_id = res.get('post_id')
    print(f'   опубликовано: https://vk.com/wall-{group_id}_{post_id}')

    if comment_text:
        errs = []
        for name, tok in photo_tokens()[::-1]:   # сначала VK_ACCESS_TOKEN, потом VK_USER_TOKEN
            try:
                vk('wall.createComment', {
                    'owner_id': '-' + group_id,
                    'post_id': post_id,
                    'from_group': group_id,
                    'message': comment_text,
                }, tok)
                print(f'   ссылка добавлена комментарием (ключ {name})')
                break
            except Exception as e:
                errs.append(f'{name}: {e}')
        else:
            print(f'::warning title=VK Posting::комментарий к посту {label} не добавлен — ' + ' | '.join(errs))
            print('   ⚠ комментарий не добавлен: ' + ' | '.join(errs))


sent = 0
try:
    for i, (day, folder, md_path, num) in enumerate(to_publish):
        if i:
            time.sleep(GAP_SEC)
        publish(md_path, num, folder, day)
        remember(day, folder, num)      # пишем после каждого — прогон может прерваться
        sent += 1
    print(f'Success! Опубликовано за прогон: {sent}. Сегодня всего: {published_today + sent}. '
          f'В очереди осталось: {len(queue) - sent}')
except Exception as e:
    print(f'::error title=VK Posting::{e}')
    print(f'Error: {e}')
    if sent:
        print(f'Успело выйти постов: {sent}, состояние сохранено')
    exit(1)
