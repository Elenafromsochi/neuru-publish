import os, re, json, time, mimetypes, uuid, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

msk = timezone(timedelta(hours=3))
date_str = datetime.now(msk).strftime('%d.%m.%Y')
# Telegram и Дзен разведены на отдельные площадки: в канал уходит короткий
# пост картинкой с подписью, а для Дзена пишется длинная статья и публикуется
# вручную. Поэтому читаем папку tg. Папка dzen поддерживается как запасной
# вариант: пока в ней ещё лежат публикации, сделанные до разделения.
PLATFORM = os.environ.get('POST_PLATFORM', 'tg').strip() or 'tg'
folder = Path(f'social/posting/{date_str}/{PLATFORM}')
if not folder.exists() and PLATFORM == 'tg':
    legacy = Path(f'social/posting/{date_str}/dzen')
    if legacy.exists():
        print(f'Папки {folder} нет, беру прежнюю {legacy}')
        folder = legacy
        PLATFORM = 'dzen'
state_file = folder / f'state-{PLATFORM}.json'

CHAT_ID = '@HotelAI_ru'
CAPTION_LIMIT = 1024   # лимит подписи к фото в Telegram
TEXT_LIMIT = 4096      # лимит обычного сообщения

# Расписание публикаций: один пост в час начиная с FIRST_HOUR по Москве.
# GitHub запускает cron когда придётся и часть окон пропускает — 13.09
# из одиннадцати запланированных прогонов случилось четыре. Поэтому скрипт
# не «публикует один пост за прогон», а считает, сколько постов должно было
# выйти к текущему часу, и догоняет отставание.
FIRST_HOUR = int(os.environ.get('POST_FIRST_HOUR', '12'))
GAP_SEC = int(os.environ.get('POST_GAP_SEC', '20'))   # пауза между догоняющими

if not folder.exists():
    print(f'No folder: {folder}')
    exit(0)

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

# Сколько постов должно было выйти к этому часу
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
    print(f'Отставание {behind} постов — публикую их за этот прогон '
          f'с паузой {GAP_SEC} с')
print('К публикации: ' + ', '.join('#' + str(n) for _, n in to_publish))
def prepare(md_path, num):
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
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, 'w') as f:
        json.dump({'date': date_str, 'published': sorted(published)}, f,
                  ensure_ascii=False, indent=2)


def publish(md_path, num):
    """Публикует один пост одним сообщением. Возвращает True при успехе."""
    text, image = prepare(md_path, num)
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


sent = 0
try:
    for i, (md_path, num) in enumerate(to_publish):
        if i:
            time.sleep(GAP_SEC)     # не сыпем постами подряд в одну секунду
        if not publish(md_path, num):
            break                   # дальше не идём: сначала разобраться с ошибкой
        published.append(num)
        save_state()                # пишем после каждого — прогон может прерваться
        sent += 1

    if sent:
        print(f'Success! Опубликовано за прогон: {sent}. '
              f'Всего за {date_str}: {len(published)} из {len(all_posts)}')
    else:
        print('Ничего не опубликовано')
        exit(1)

except Exception as e:
    print(f'Error: {e}')
    if sent:
        print(f'Успело выйти постов: {sent}, состояние сохранено')
    exit(1)
