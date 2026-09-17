import os, re, json, time, mimetypes, uuid, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Автопостинг в Telegram-канал @HotelAI_ru.
#
# fix62 — изменены только расписание и очередь, подготовка текста,
# подпись к фото и отправка остались прежними:
# — Один пост в день в POST_HOUR по Москве (12:00). Если GitHub запустил
#   прогон позже, пост выходит при первом запуске после 12:00, до 23:59.
# — Общая очередь: неопубликованные посты из social/posting/<дата>/tg
#   за сегодня и за прошлые дни (POST_BACKLOG_DAYS, по умолчанию 14),
#   от старой даты к новой, внутри дня — по номеру.
# — Запасной папки dzen больше нет: статьи Дзена в канал не идут.
# — Отметки, как и раньше, в state-tg.json папки дня.
#   Сколько постов вышло за день — в social/posting/tg-log.json.
# — DRY_RUN=true: показать, что вышло бы, ничего не отправляя.
# — Если пост не публикуется, причина видна в Annotations.

msk = timezone(timedelta(hours=3))
now = datetime.now(msk)
today = now.date()
date_str = now.strftime('%d.%m.%Y')
ROOT = Path('social/posting')
LOG_FILE = ROOT / 'tg-log.json'
PLATFORM = 'tg'

CHAT_ID = '@HotelAI_ru'
CAPTION_LIMIT = 1024   # лимит подписи к фото в Telegram
TEXT_LIMIT = 4096      # лимит обычного сообщения

POST_HOUR = int(os.environ.get('POST_HOUR', '12'))
POSTS_PER_DAY = max(1, int(os.environ.get('POSTS_PER_DAY', '1')))
BACKLOG_DAYS = max(0, int(os.environ.get('POST_BACKLOG_DAYS', '14')))
GAP_SEC = int(os.environ.get('POST_GAP_SEC', '20'))
DRY_RUN = os.environ.get('DRY_RUN', '').strip().lower() in ('1', 'true', 'yes', 'да')


def skip(msg):
    """Пост в этом прогоне не публикуется: причина видна в Annotations."""
    print(msg)
    print('::notice title=Telegram Posting — пост не опубликован::' + msg.replace('\n', ' '))
    exit(0)


def read_published(folder):
    f = folder / f'state-{PLATFORM}.json'
    if not f.exists():
        return []
    with open(f) as fh:
        return json.load(fh).get('published', [])


# ── Очередь за сегодня и прошлые дни ──
folders = []
if ROOT.exists():
    for d in ROOT.iterdir():
        try:
            day = datetime.strptime(d.name, '%d.%m.%Y').date()
        except ValueError:
            continue
        if (d / PLATFORM).is_dir() and 0 <= (today - day).days <= BACKLOG_DAYS:
            folders.append((day, d / PLATFORM))
folders.sort()

published_by = {}
queue = []
for day, folder in folders:
    pub = read_published(folder)
    published_by[folder] = pub
    for p in sorted(folder.glob('post-*.md'), key=lambda x: int(x.stem.split('-')[1])):
        n = int(p.stem.split('-')[1])
        if n not in pub:
            queue.append((day, folder, p, n))

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
    # До появления журнала посты выходили только из папки сегодняшнего дня
    today_folder = ROOT / date_str / PLATFORM
    published_today = len(published_by.get(today_folder, []))

old = [q for q in queue if q[0] < today]
print(f'Очередь Telegram: {len(queue)} постов'
      + (f', из них за прошлые дни {len(old)} ('
         + ', '.join(sorted({q[0].strftime("%d.%m") for q in old})) + ')' if old else '')
      + f'. Сегодня уже вышло: {published_today}, в день выходит {POSTS_PER_DAY}'
      + (' · ПРОБНЫЙ РЕЖИМ, ничего не отправляется' if DRY_RUN else ''))
if queue:
    print('Порядок очереди: ' + ', '.join(f'{q[0]:%d.%m} №{q[3]}' for q in queue[:15])
          + (' …' if len(queue) > 15 else ''))

if not queue:
    skip('Неопубликованных постов нет. Одобрите следующие в ИИ-Копирайтере-Публицисте на вкладке Telegram.')
if published_today >= POSTS_PER_DAY:
    skip(f'Пост дня уже вышел. Следующий — завтра в {POST_HOUR}:00.')
if now.hour < POST_HOUR:
    skip(f'Рано: пост дня выходит в {POST_HOUR}:00 по Москве.')

to_publish = queue[:POSTS_PER_DAY - published_today]
print('К публикации: ' + ', '.join(f'{q[0]:%d.%m} №{q[3]}' for q in to_publish))

def prepare(md_path, num, folder):
    """Готовит текст и находит картинку для одного поста."""
    text = md_path.read_text(encoding='utf-8')

    # ── Служебные поля копирайтера не публикуем ──
    text = re.sub(r'^[ \t]*(?:\*\*)?Промпт\s+для\s+картинки(?:\*\*)?\s*:[\s\S]*?(?=\n[ \t]*\n|\Z)',
                  '', text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r'^[ \t]*(?:\*\*)?Знаков(?:\*\*)?\s*:[^\n]*$', '', text,
                  flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r'\n*-{3,}\n*\s*Иллюстрация\s*:.*$', '', text,
                  flags=re.IGNORECASE | re.DOTALL)

    # Заголовок публикации сохраняем: первая строка «# …» станет жирной
    text = text.strip()
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'^#{1,3}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'^\-\s+', '• ', text, flags=re.MULTILINE)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
    # Слово «Хэштеги:» — служебная метка, в канале нужны сами теги
    text = re.sub(r'^[ \t]*(?:<b>)?Хэштеги(?:</b>)?\s*:\s*', '', text,
                  flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    image = None
    for ext in ('jpg', 'jpeg', 'png', 'webp'):
        cand = folder / f'post-{num}.{ext}'
        if cand.exists():
            image = cand
            break
    return text, image


def fit_caption(t, limit=CAPTION_LIMIT):
    """Укладывает текст в подпись к фото, сохраняя концовку.

    Telegram разрешает 1024 символа подписи — это предел, а не настройка.
    Публикации бывают длиннее, поэтому подрезаем середину, но ссылку
    и хэштеги оставляем: без них пост теряет смысл. Резать стараемся
    по границе абзаца, в крайнем случае — по концу предложения.
    """
    if len(t) <= limit:
        return t, 0

    lines = t.split('\n')
    tail_lines = []
    # Собираем концовку: хэштеги и абзац со ссылкой
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        if last.startswith('#') or 'http' in last:
            tail_lines.insert(0, lines.pop())
            continue
        break
    tail = '\n\n'.join(x.strip() for x in tail_lines if x.strip())
    body = '\n'.join(lines).strip()

    room = limit - len(tail) - 4      # место под тело, «…» и переводы строк
    if room < 200:
        # Концовка сама почти целиком занимает подпись — режем её как есть
        return t[:limit - 1].rstrip() + '…', len(t) - limit + 1

    if len(body) > room:
        cut = body[:room]
        # по границе абзаца
        para = cut.rfind('\n\n')
        if para > room * 0.5:
            body = cut[:para].rstrip()
        else:
            # по концу предложения
            dot = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
            body = (cut[:dot + 1] if dot > room * 0.5 else cut).rstrip()
        body += ' …'

    out = body + ('\n\n' + tail if tail else '')
    return out, len(t) - len(out)


token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
if not token:
    print('Не задан секрет TELEGRAM_BOT_TOKEN')
    exit(1)


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


def publish(md_path, num, folder):
    """Публикует один пост одним сообщением. Возвращает True при успехе."""
    text, image = prepare(md_path, num, folder)
    print(f'— пост #{num}: {"картинка " + image.name if image else "без картинки"}')

    if image:
        # Одно сообщение: фото с подписью. Так картинка выглядит картинкой,
        # а не превью ссылки, и пост в канале остаётся единым.
        caption, cut = fit_caption(text)
        if cut:
            print(f'   текст {len(text)} симв. не влез в подпись ({CAPTION_LIMIT}) — '
                  f'подрезал на {cut}, ссылка и хэштеги сохранены')
        result = api_photo(image, caption=caption)
    else:
        body = text[:TEXT_LIMIT - 6] + '...' if len(text) > TEXT_LIMIT else text
        result = api('sendMessage', {'chat_id': CHAT_ID, 'text': body,
                                     'parse_mode': 'HTML',
                                     'link_preview_options': {'is_disabled': True}})

    if not result.get('ok'):
        print(f'   Telegram error: {result}')
        return False
    print('   опубликовано')
    return True


def save_state(day, folder):
    with open(folder / f'state-{PLATFORM}.json', 'w') as f:
        json.dump({'date': day.strftime('%d.%m.%Y'), 'published': sorted(published_by[folder])}, f,
                  ensure_ascii=False, indent=2)
    log.setdefault(today_key, []).append(
        {'date': day.strftime('%d.%m.%Y'), 'num': published_by[folder][-1],
         'at': datetime.now(msk).isoformat()})
    keep = sorted(log)[-30:]
    with open(LOG_FILE, 'w') as f:
        json.dump({k: log[k] for k in keep}, f, ensure_ascii=False, indent=2)


if DRY_RUN:
    for day, folder, md_path, num in to_publish:
        text, image = prepare(md_path, num, folder)
        print(f'— пост {day:%d.%m} №{num}: {"картинка " + image.name if image else "без картинки"}, '
              f'{len(text)} симв.')
        print('   начало: ' + re.sub(r'<[^>]+>', '', text)[:200].replace('\n', ' '))
    print('Пробный режим: ничего не отправлено и не сохранено')
    exit(0)

sent = 0
try:
    for i, (day, folder, md_path, num) in enumerate(to_publish):
        if i:
            time.sleep(GAP_SEC)     # не сыпем постами подряд в одну секунду
        if not publish(md_path, num, folder):
            break                   # дальше не идём: сначала разобраться с ошибкой
        published_by[folder].append(num)
        save_state(day, folder)     # пишем после каждого — прогон может прерваться
        sent += 1

    if sent:
        print(f'Success! Опубликовано за прогон: {sent}. В очереди осталось: {len(queue) - sent}')
    else:
        print('::error title=Telegram Posting::Ничего не опубликовано — ошибка Telegram выше в журнале')
        print('Ничего не опубликовано')
        exit(1)

except Exception as e:
    print(f'::error title=Telegram Posting::{e}')
    print(f'Error: {e}')
    if sent:
        print(f'Успело выйти постов: {sent}, состояние сохранено')
    exit(1)
