/* НЭЙРУ · окно «Иллюстрация к публикации» · fix53
   Подключается в neuru-platform.html последней строкой перед </body>:
     <script src="neuru-illustrate.js?v=53"></script>
   Заменяет окно иллюстрации из fix51, остальная панель не меняется.

   Что нового:
   — фото-основа для всех трёх типов кадра: загружается с компьютера каждый раз
     заново, сжимается в браузере до 1536 px и уходит иллюстратору;
   — выбор формата кадра прямо в окне: вертикальный 2:3, горизонтальный 3:2, квадрат;
   — у обложки поля «Заголовок» и «Выделить на плашке»,
     у инфографики — плашка, заголовок, подзаголовок и карточки;
   — поле промпта для обложки и инфографики хранит только сюжет,
     оформление добавляет иллюстратор. Раньше обёртка собиралась и в окне,
     и в движке, и промпт уходил с двойным описанием стиля. */
(function(){
  'use strict';

  var ILX_WF = 'agents-illustrate.yml';

  var STYLES = [
    ['photo',  '📷 Фотосцена', 'Реалистичный кадр без текста. С фото-основой — Вы в кадре.'],
    ['cover',  '🏷 Обложка',   'Фото с Вами, крупный заголовок, выделение на плашке, голографические панели.'],
    ['scheme', '📊 Инфографика', 'Плашка, крупный заголовок, стеклянные карточки с роботами-помощниками.']
  ];
  var SIZES = [
    ['1024x1536', 'Вертикальный 2:3'],
    ['1536x1024', 'Горизонтальный 3:2'],
    ['1024x1024', 'Квадрат']
  ];

  function S(){ return window._imgStep || {}; }
  function esc(s){ return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function attr(s){ return esc(s).replace(/"/g,'&quot;'); }
  function el(id){ return document.getElementById(id); }

  function genCfg(){
    try { return JSON.parse(localStorage.getItem('neuru_imggen') || '{}') || {}; }
    catch(e){ return {}; }
  }
  function isOpenAI(){ return genCfg().provider === 'openai'; }

  // Сюжет без формата кадра и без запретов копирайтера
  function sceneOnly(p){
    var s = String(p || '').replace(/\s+/g, ' ').trim();
    s = s.replace(/^(Горизонтальн|Вертикальн|Квадратн)\S*\s+кадр[^,]*,\s*/i, '');
    var i = s.search(/,\s*без\s/i);
    if(i > 40) s = s.slice(0, i);
    return s.replace(/[.,\s]+$/, '').trim();
  }

  function pubTitle(){
    try { return (window.imgPubTitle ? window.imgPubTitle() : '') || ''; } catch(e){ return ''; }
  }
  function guessAccent(t){
    var m = String(t || '').match(/\d[\d\s,.]*\s*[А-Яа-яЁёA-Za-z%₽]+/);
    return m ? m[0].trim() : '';
  }

  function cardsToText(cards){
    return (cards || []).map(function(c){
      return [c.title || '', c.text || '', c.pill || ''].join(' | ').replace(/(\s\|\s)+$/,'');
    }).join('\n');
  }
  function textToCards(t){
    return String(t || '').split('\n').map(function(line){
      var p = line.split('|').map(function(x){ return x.trim(); });
      return { title: p[0] || '', text: p[1] || '', pill: p[2] || '' };
    }).filter(function(c){ return c.title; }).slice(0, 4);
  }

  // ── Состояние окна ──

  window.openImageStep = function(text, platform, dateStr, num, btn){
    var full = (window.imgPromptFromText ? window.imgPromptFromText(text) : '') || '';
    var cfg = genCfg();
    var title = '';
    window._imgStep = {
      text: text, platform: platform, dateStr: dateStr, num: num, btn: btn,
      imgData: '', imgExt: '', prompt: full,
      style: 'photo',
      size: cfg.provider === 'openai' ? (cfg.size || '1024x1536') : (cfg.size || '1024x1024'),
      scenePhoto: full,
      sceneLayout: sceneOnly(full),
      cover: {}, scheme: {},
      refData: '', refName: '',
      preview: '', status: ''
    };
    title = pubTitle();
    window._imgStep.cover = { title: title, accent: guessAccent(title) };
    window._imgStep.scheme = { badge: 'ИИ-ПОРТЬЕ', headline: '', subline: '', cardsText: '' };

    var ov = el('img-step-ov');
    if(!ov){
      ov = document.createElement('div');
      ov.id = 'img-step-ov';
      ov.style.cssText = 'position:fixed;inset:0;background:rgba(15,20,35,.55);z-index:9999;'
        + 'display:flex;align-items:flex-start;justify-content:center;padding:40px 16px;overflow:auto';
      ov.onclick = function(e){ if(e.target === ov) window.closeImageStep(); };
      document.body.appendChild(ov);
    }
    render();
    ov.style.display = 'flex';
    if(window.imgOnPaste) document.addEventListener('paste', window.imgOnPaste);
  };

  // Забираем из полей всё, что человек успел ввести, — перед перерисовкой
  function sync(){
    var s = S();
    var ta = el('img-prompt');
    if(ta){
      if(s.style === 'photo') s.scenePhoto = ta.value; else s.sceneLayout = ta.value;
    }
    if(el('ilx-cover-title')) s.cover.title = el('ilx-cover-title').value;
    if(el('ilx-cover-accent')) s.cover.accent = el('ilx-cover-accent').value;
    if(el('ilx-sch-badge')) s.scheme.badge = el('ilx-sch-badge').value;
    if(el('ilx-sch-head')) s.scheme.headline = el('ilx-sch-head').value;
    if(el('ilx-sch-sub')) s.scheme.subline = el('ilx-sch-sub').value;
    if(el('ilx-sch-cards')) s.scheme.cardsText = el('ilx-sch-cards').value;
    if(el('img-preview')) s.preview = el('img-preview').innerHTML;
    if(el('img-status')) s.status = el('img-status').innerHTML;
  }

  function currentLayout(){
    var s = S();
    if(s.style === 'cover'){
      return { title: (s.cover.title || '').trim(), accent: (s.cover.accent || '').trim() };
    }
    if(s.style === 'scheme'){
      return {
        badge: (s.scheme.badge || '').trim(),
        headline: (s.scheme.headline || '').trim(),
        subline: (s.scheme.subline || '').trim(),
        cards: textToCards(s.scheme.cardsText)
      };
    }
    return {};
  }

  window.ilxSetStyle = function(v){ sync(); S().style = v; render(); };
  window.ilxSetSize  = function(v){ sync(); S().size = v; render(); };
  window.imgSetStyle = window.ilxSetStyle;   // совместимость со старыми кнопками

  // ── Фото-основа ──

  function compress(file, cb){
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function(){
      var max = 1536, w = img.naturalWidth, h = img.naturalHeight;
      var k = Math.min(1, max / Math.max(w, h));
      var c = document.createElement('canvas');
      c.width = Math.round(w * k); c.height = Math.round(h * k);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      var d = c.toDataURL('image/jpeg', 0.9);
      URL.revokeObjectURL(url);
      cb(d);
    };
    img.onerror = function(){ URL.revokeObjectURL(url); cb(null); };
    img.src = url;
  }

  window.ilxPickRef = function(inp){
    var f = inp.files && inp.files[0];
    if(!f) return;
    sync();
    var s = S();
    s.status = 'Готовлю фото…';
    render();
    compress(f, function(d){
      if(!d){
        s.status = '<span style="color:#DC2626">Файл не открылся как изображение. Попробуйте JPG или PNG.</span>';
        render(); return;
      }
      s.refData = d; s.refName = f.name;
      s.status = 'Фото-основа готова. Оно будет загружено вместе с запуском генерации.';
      render();
    });
  };
  window.ilxDropRef = function(){ sync(); S().refData = ''; S().refName = ''; render(); };

  function refPath(){
    var s = S();
    return 'social/illustrations/refs/' + s.dateStr + '-' + s.platform + '-' + s.num + '.jpg';
  }

  // Запись двоичного файла в neuru-publish: свежий sha, при 409 — повтор
  function putBinary(path, b64, msg, cb, retry){
    var tok = getToken();
    var url = 'https://api.github.com/repos/' + PUB_REPO + '/contents/'
            + path.split('/').map(encodeURIComponent).join('/');
    var hdr = { 'Authorization': 'token ' + tok, 'Accept': 'application/vnd.github.v3+json' };
    fetch(url + '?ref=main&_=' + Date.now(), { headers: hdr })
      .then(function(r){ return r.status === 200 ? r.json() : null; })
      .then(function(cur){
        var body = { message: msg, content: b64, branch: 'main' };
        if(cur && cur.sha) body.sha = cur.sha;
        return fetch(url, { method: 'PUT',
          headers: Object.assign({}, hdr, { 'Content-Type': 'application/json' }),
          body: JSON.stringify(body) });
      })
      .then(function(r){
        if(r.status === 200 || r.status === 201){ cb(null); return; }
        if(r.status === 409 && !retry){ putBinary(path, b64, msg, cb, true); return; }
        return r.text().then(function(t){ cb('GitHub ответил ' + r.status + ': ' + t.slice(0, 160)); });
      })
      .catch(function(e){ cb('Сеть недоступна: ' + (e && e.message ? e.message : 'ошибка')); });
  }

  // ── Отрисовка окна ──

  function btnRow(list, current, fn){
    return list.map(function(x){
      var on = x[0] === current;
      return '<button onclick="' + fn + '(\'' + x[0] + '\')" style="background:' + (on ? 'var(--grad)' : 'none')
        + ';color:' + (on ? '#fff' : 'var(--text)') + ';border:1px solid ' + (on ? 'transparent' : 'var(--border)')
        + ';border-radius:8px;padding:8px 14px;font-size:12px;font-weight:' + (on ? '700' : '500')
        + ';cursor:pointer;font-family:inherit">' + x[1] + '</button>';
    }).join('');
  }

  function field(id, label, value, ph){
    return '<label style="display:block;font-size:11px;font-weight:600;color:var(--muted);margin:10px 0 4px">' + label + '</label>'
      + '<input id="' + id + '" type="text" value="' + attr(value) + '" placeholder="' + attr(ph || '') + '" '
      + 'style="width:100%;box-sizing:border-box;padding:9px 11px;border:1px solid var(--border);border-radius:9px;'
      + 'font-size:13px;font-family:inherit;background:var(--card);color:var(--text)">';
  }

  function render(){
    var s = S();
    var ov = el('img-step-ov');
    if(!ov) return;
    var card = 'background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 18px;margin-bottom:14px';
    var h3 = 'font-size:13px;font-weight:700;margin-bottom:8px;color:var(--text)';
    var cfg = genCfg();
    var prov = cfg.provider === 'openai' ? 'OpenAI' : 'YandexART';
    var st = STYLES.filter(function(x){ return x[0] === s.style; })[0] || STYLES[0];

    var html = '<div style="background:var(--bg);border-radius:18px;max-width:760px;width:100%;padding:22px 24px 26px;box-shadow:var(--shadow2)">'
      + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
      + '<div style="font-size:17px;font-weight:800;color:var(--text)">Иллюстрация к публикации ' + esc(s.num) + '</div>'
      + '<button onclick="closeImageStep()" style="background:none;border:none;font-size:20px;cursor:pointer;color:var(--muted)">×</button></div>'
      + '<div style="font-size:12px;color:var(--muted);margin-bottom:16px">' + esc(s.platform) + ' · ' + esc(s.dateStr)
      + '. Загрузите готовую картинку или сгенерируйте новую.</div>';

    // 1. Готовая картинка
    html += '<div style="' + card + '"><div style="' + h3 + '">1. Готовая картинка</div>'
      + '<input type="file" accept="image/*" onchange="imgPickFile(this)" style="font-size:12px;font-family:inherit">'
      + '<div style="font-size:11px;color:var(--muted);margin-top:6px">Публикуется как есть, без генерации.</div></div>';

    // 2. Генерация
    html += '<div style="' + card + '"><div style="' + h3 + '">2. Или сгенерировать</div>'
      + '<div style="font-size:12px;font-weight:600;color:var(--muted);margin-bottom:6px">Тип кадра</div>'
      + '<div style="display:flex;gap:8px;flex-wrap:wrap">' + btnRow(STYLES, s.style, 'ilxSetStyle') + '</div>'
      + '<div style="font-size:11px;color:var(--muted);margin:6px 0 12px">' + esc(st[2]) + '</div>'
      + '<div style="font-size:12px;font-weight:600;color:var(--muted);margin-bottom:6px">Формат кадра</div>'
      + '<div style="display:flex;gap:8px;flex-wrap:wrap">' + btnRow(SIZES, s.size, 'ilxSetSize') + '</div>';

    // Фото-основа
    html += '<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--border)">'
      + '<div style="font-size:12px;font-weight:600;color:var(--muted);margin-bottom:6px">Фото-основа — по желанию</div>';
    if(s.refData){
      html += '<div style="display:flex;gap:12px;align-items:center">'
        + '<img src="' + s.refData + '" style="width:84px;height:84px;object-fit:cover;border-radius:10px;border:1px solid var(--border)">'
        + '<div style="font-size:12px;color:var(--text);line-height:1.5">' + esc(s.refName)
        + '<br><button onclick="ilxDropRef()" style="margin-top:6px;background:none;border:1px solid var(--border);'
        + 'border-radius:7px;padding:5px 11px;font-size:11px;cursor:pointer;font-family:inherit;color:var(--text)">Убрать</button>'
        + ' <label style="margin-left:6px;font-size:11px;color:var(--blue);cursor:pointer">Заменить'
        + '<input type="file" accept="image/*" onchange="ilxPickRef(this)" style="display:none"></label></div></div>';
    } else {
      html += '<input type="file" accept="image/*" onchange="ilxPickRef(this)" style="font-size:12px;font-family:inherit">';
    }
    html += '<div style="font-size:11px;color:' + (cfg.provider === 'openai' ? 'var(--muted)' : '#B45309') + ';margin-top:6px;line-height:1.5">'
      + (cfg.provider === 'openai'
          ? 'Человек с фото появится в кадре и останется узнаваемым. Лучше портрет по пояс, при хорошем свете.'
          : 'Фото-основу учитывает только OpenAI. Сейчас выбран YandexART — фото будет пропущено. '
            + 'Сменить: Админ-панель → Картинки.')
      + '</div></div>';

    // Тексты для кадра
    if(s.style === 'cover'){
      html += '<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--border)">'
        + '<div style="font-size:12px;font-weight:600;color:var(--muted)">Текст на обложке</div>'
        + field('ilx-cover-title', 'Заголовок — 3–7 слов, будет на картинке дословно', s.cover.title, 'Как за 2 минуты сделать дашборд')
        + field('ilx-cover-accent', 'Выделить на плашке — 1–3 слова из заголовка', s.cover.accent, '2 минуты')
        + '</div>';
    }
    if(s.style === 'scheme'){
      html += '<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--border)">'
        + '<div style="font-size:12px;font-weight:600;color:var(--muted)">Текст инфографики</div>'
        + field('ilx-sch-badge', 'Плашка сверху', s.scheme.badge, 'ОТДЕЛ ПУБЛИКАЦИЙ')
        + field('ilx-sch-head', 'Крупный заголовок — 2–4 слова, число в начале выделится', s.scheme.headline, '4 ИИ-агента')
        + field('ilx-sch-sub', 'Подзаголовок', s.scheme.subline, 'которые создают контент, привлекающий клиентов')
        + '<label style="display:block;font-size:11px;font-weight:600;color:var(--muted);margin:10px 0 4px">'
        + 'Карточки — 3 или 4 строки: название | описание | плашка</label>'
        + '<textarea id="ilx-sch-cards" style="width:100%;box-sizing:border-box;min-height:92px;padding:9px 11px;'
        + 'border:1px solid var(--border);border-radius:9px;font-size:12px;line-height:1.6;font-family:inherit;'
        + 'background:var(--card);color:var(--text);resize:vertical" placeholder="ИИ-новостник | Следит за трендами и новостями ниши | Актуальный контент каждый день">'
        + esc(s.scheme.cardsText) + '</textarea>'
        + '<div style="font-size:11px;color:var(--muted);margin-top:4px">Пустые поля заполнит иллюстратор из текста публикации — '
        + 'кнопкой «Улучшить» или сам при генерации.</div></div>';
    }

    // Сюжет / промпт
    var isPhoto = s.style === 'photo';
    html += '<div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--border)">'
      + '<div style="font-size:12px;font-weight:600;color:var(--muted);margin-bottom:6px">'
      + (isPhoto ? 'Промпт для иллюстрации' : 'Сюжет и обстановка — оформление иллюстратор добавит сам') + '</div>'
      + '<textarea id="img-prompt" oninput="window.imgUpdateReady()" style="width:100%;box-sizing:border-box;min-height:' + (isPhoto ? 90 : 60) + 'px;'
      + 'font-family:inherit;font-size:12px;line-height:1.65;border:1px solid var(--border);border-radius:10px;padding:10px;'
      + 'background:var(--card);color:var(--text);resize:vertical">' + esc(isPhoto ? s.scenePhoto : s.sceneLayout) + '</textarea>'
      + '<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">'
      + '<button onclick="ilxImprove(this)" style="background:var(--grad);color:#fff;border:none;border-radius:8px;'
      + 'padding:8px 15px;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit">'
      + (isPhoto ? '✨ Улучшить промпт' : '✨ Заполнить из публикации') + '</button>'
      + '<button onclick="ilxGenerate(this)" style="background:#7C3AED;color:#fff;border:none;border-radius:8px;'
      + 'padding:8px 15px;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit">🎨 Сгенерировать картинку</button>'
      + '<button onclick="imgCopyPrompt()" style="background:none;border:1px solid var(--border);border-radius:8px;'
      + 'padding:8px 15px;font-size:12px;cursor:pointer;font-family:inherit;color:var(--text)">Скопировать</button>'
      + '</div>'
      + '<div style="font-size:11px;color:var(--muted);margin-top:8px">Рисует: ' + esc(prov + ' · ' + (cfg.model || 'по умолчанию'))
      + '. Качество и модель — Админ-панель → Картинки.'
      + (!isPhoto && cfg.provider !== 'openai' ? ' <span style="color:#B45309">Текст на кадре надёжно рисует только OpenAI.</span>' : '')
      + '</div></div>';

    html += '</div>';   // конец блока «Сгенерировать»

    html += '<div id="img-preview" style="margin-bottom:14px">' + (s.preview || '') + '</div>'
      + '<div id="img-status" style="font-size:12px;color:var(--muted);margin-bottom:12px">' + (s.status || '') + '</div>'
      + '<div style="display:flex;gap:9px;flex-wrap:wrap;align-items:center">'
      + '<button id="img-send" onclick="imgSaveToGit(this)" disabled style="background:#9AA3B8;color:#fff;border:none;border-radius:9px;'
      + 'padding:10px 20px;font-size:13px;font-weight:700;cursor:not-allowed;font-family:inherit">Отправить в постинг</button>'
      + '<button onclick="closeImageStep()" style="background:none;border:1px solid var(--border);border-radius:9px;'
      + 'padding:10px 20px;font-size:13px;cursor:pointer;font-family:inherit;color:var(--text)">Отмена</button>'
      + '<div id="img-hint" style="font-size:12px;color:var(--muted);flex-basis:100%"></div>'
      + '</div></div>';

    ov.innerHTML = html;
    if(window.imgUpdateReady) window.imgUpdateReady();
  }

  function setStatus(txt, color){
    var e = el('img-status');
    var v = color ? '<span style="color:' + color + '">' + esc(txt) + '</span>' : esc(txt);
    if(e) e.innerHTML = v;
    S().status = v;
  }

  // ── Запуск иллюстратора ──

  function imgInput(){
    var s = S(), c = genCfg();
    var o = { provider: c.provider || 'yandex', style: s.style, size: s.size };
    if(c.model) o.model = String(c.model);
    if(c.quality) o.quality = String(c.quality);
    return JSON.stringify(o);
  }

  function jsonPath(){
    var s = S();
    return 'social/illustrations/' + s.dateStr + '-' + s.platform + '-' + s.num + '.json';
  }

  function run(mode, promptText, ref, onDone, onFail){
    var tok = getToken();
    if(!tok){ onFail('Добавьте GitHub-токен в Админ-панели'); return; }
    var hdr = { 'Authorization': 'Bearer ' + tok, 'Accept': 'application/vnd.github+json' };
    var s = S();
    var layout = currentLayout();

    setStatus('Читаю текущее состояние…');
    illReadPub(jsonPath(), function(b64){
      var was = '';
      if(b64){ try { was = (JSON.parse(illB64ToText(b64)) || {}).updated || ''; } catch(e){} }
      var since = new Date(Date.now() - 60000).toISOString();

      setStatus('Запускаю иллюстратора…');
      fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/' + ILX_WF + '/dispatches', {
        method: 'POST',
        headers: Object.assign({}, hdr, { 'Content-Type': 'application/json' }),
        body: JSON.stringify({ ref: 'main', inputs: {
          mode: mode,
          date: String(s.dateStr),
          platform: String(s.platform),
          num: String(s.num),
          prompt: String(promptText || ''),
          img: imgInput(),
          ref: ref || '',
          layout: s.style === 'photo' ? '' : JSON.stringify(layout)
        } })
      }).then(function(r){
        if(r.status !== 204){
          return r.text().then(function(x){
            var hint = /Unexpected inputs/i.test(x)
              ? ' Похоже, в репозитории старый ' + ILX_WF + ' — установите новый из сборки fix53.'
              : ' Проверьте, что у токена есть доступ Actions: Read and write.';
            onFail('Не удалось запустить (' + r.status + ').' + hint + ' ' + x.slice(0, 160));
          });
        }
        var tries = 0, runId = null, failed = false;
        function findRun(){
          fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/' + ILX_WF
                + '/runs?per_page=5&created=%3E' + since, { headers: hdr })
            .then(function(r){ return r.ok ? r.json() : null; })
            .then(function(d){ var x = d && d.workflow_runs && d.workflow_runs[0]; if(x) runId = x.id; })
            .catch(function(){});
        }
        function checkRun(){
          if(!runId){ findRun(); return; }
          fetch('https://api.github.com/repos/' + REPO + '/actions/runs/' + runId, { headers: hdr })
            .then(function(r){ return r.ok ? r.json() : null; })
            .then(function(d){
              if(d && d.status === 'completed' && d.conclusion !== 'success'){
                failed = true;
                onFail('Иллюстратор упал (' + d.conclusion + '). Журнал: ' + d.html_url);
              }
            }).catch(function(){});
        }
        findRun();
        var maxTries = mode === 'image' ? 72 : 40;
        (function poll(){
          if(failed) return;
          tries++;
          if(tries > maxTries){
            onFail('Результат не появился за ' + Math.round(maxTries * 5 / 60) + ' мин. Посмотрите журнал в Actions.');
            return;
          }
          setStatus((mode === 'image'
              ? (genCfg().provider === 'openai' ? 'OpenAI рисует' : 'YandexART рисует')
              : 'Иллюстратор готовит тексты') + '… (' + (tries * 5) + ' с)');
          if(tries % 3 === 0) checkRun();
          illReadPub(jsonPath(), function(b){
            if(failed) return;
            if(!b){ setTimeout(poll, 5000); return; }
            var j = null;
            try { j = JSON.parse(illB64ToText(b)); } catch(e){}
            if(!j || !j.updated || j.updated === was){ setTimeout(poll, 5000); return; }
            onDone(j);
          });
        })();
      }).catch(function(e){ onFail('Сеть недоступна: ' + (e && e.message ? e.message : 'ошибка')); });
    });
  }

  // «Улучшить промпт» / «Заполнить из публикации»
  window.ilxImprove = function(btn){
    sync();
    var s = S();
    var ta = el('img-prompt');
    var old = btn.textContent;
    btn.textContent = 'Работаю…'; btn.disabled = true;
    function stop(){ btn.textContent = old; btn.disabled = false; }

    run('prompt', ta ? ta.value : '', '', function(j){
      stop(); sync();
      if(s.style === 'photo'){
        if(j.prompt) s.scenePhoto = j.prompt;
      } else {
        if(j.prompt) s.sceneLayout = j.prompt;
        var L = j.layout || {};
        if(s.style === 'cover'){
          if(L.title) s.cover.title = L.title;
          if(L.accent) s.cover.accent = L.accent;
        } else {
          if(L.badge) s.scheme.badge = L.badge;
          if(L.headline) s.scheme.headline = L.headline;
          if(L.subline) s.scheme.subline = L.subline;
          if(L.cards && L.cards.length) s.scheme.cardsText = cardsToText(L.cards);
        }
      }
      s.status = '<span style="color:#10B981">Готово. Проверьте тексты и нажмите «Сгенерировать картинку».</span>';
      render();
    }, function(msg){
      stop();
      setStatus(msg, '#DC2626');
    });
  };

  // «Сгенерировать картинку»
  window.ilxGenerate = function(btn){
    sync();
    var s = S();
    var ta = el('img-prompt');
    var text = ta ? ta.value.trim() : '';
    if(s.style === 'photo' && text.length < 20){
      alert('Для фотосцены нужен промпт — напишите его или нажмите «Улучшить промпт».');
      return;
    }
    if(s.refData && !isOpenAI()){
      if(!confirm('Фото-основу учитывает только OpenAI, а сейчас выбран YandexART. '
        + 'Нарисовать без фото? Чтобы использовать фото, отмените и переключите генератор в Админ-панели → Картинки.')) return;
    }
    var old = btn.textContent;
    btn.textContent = 'Рисую…'; btn.disabled = true;
    function stop(){ btn.textContent = old; btn.disabled = false; }
    function fail(msg){ stop(); setStatus(msg, '#DC2626'); }

    function go(ref){
      run('image', text, ref, function(j){
        if(!j.image){ fail('Прогон завершился, но картинки нет. Посмотрите журнал в Actions.'); return; }
        setStatus('Картинка готова, забираю…');
        illReadPub(j.image, function(b64, code){
          stop();
          if(!b64){ fail('Картинка записана в ' + j.image + ', но скачать её не удалось (код ' + code + ').'); return; }
          var ext = (j.image.split('.').pop() || 'png').toLowerCase();
          s.imgData = b64;
          s.imgExt = ext === 'jpg' || ext === 'jpeg' ? 'jpg' : (ext === 'webp' ? 'webp' : 'png');
          var made = (j.provider === 'openai' ? 'OpenAI' : 'YandexART')
            + (j.model ? ' · ' + j.model : '') + (j.size ? ' · ' + j.size : '')
            + (j.refUsed ? ' · по фото-основе' : '');
          var pv = el('img-preview');
          var html = '<div style="font-size:12px;color:var(--muted);margin-bottom:6px">Нарисовано: ' + esc(made) + '</div>'
            + '<img src="data:image/' + (s.imgExt === 'jpg' ? 'jpeg' : s.imgExt) + ';base64,' + b64
            + '" style="max-width:100%;border-radius:12px;border:1px solid var(--border)">';
          if(pv) pv.innerHTML = html;
          s.preview = html;
          setStatus('Готово — можно отправлять в постинг или сгенерировать заново.', '#10B981');
          if(window.imgUpdateReady) window.imgUpdateReady();
        });
      }, fail);
    }

    if(s.refData && isOpenAI()){
      setStatus('Загружаю фото-основу…');
      var path = refPath();
      putBinary(path, s.refData.split(',')[1],
        'иллюстратор: фото-основа ' + s.platform + ' пост ' + s.num + ' за ' + s.dateStr,
        function(err){
          if(err){ fail('Фото-основу загрузить не удалось: ' + err); return; }
          go(path);
        });
    } else {
      go('');
    }
  };

  // Отметка сборки внизу боковой панели
  function markBuild(){
    var b = el('build-ver');
    if(b) b.innerHTML = '&#10003; сборка 16.09 · fix53';
  }
  if(document.readyState !== 'loading') markBuild();
  else document.addEventListener('DOMContentLoaded', markBuild);
})();
