/* ==========================================================================
   Switch 游戏收藏管家 — 交互脚本 v3
   无任何第三方依赖，全部功能渐进增强：
   主题切换 / 提示气泡 / 弹窗 / 星级评分 / 快捷操作 / OCR / 笔记自动保存 /
   灯箱 / 视图切换 / 键盘快捷键 / 删除确认 / 贝塞尔折线图 / 指针柔光
   ========================================================================== */
(function () {
  'use strict';

  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── 通用请求 ───────────────────────────────────────────────────────── */
  function apiPost(url, payload) {
    var body = new FormData();
    Object.keys(payload || {}).forEach(function (key) { body.append(key, payload[key]); });
    return fetch(url, {
      method: 'POST',
      body: body,
      headers: { 'X-CSRF-Token': CSRF, 'X-Requested-With': 'fetch' },
      credentials: 'same-origin'
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok || data.ok === false) {
          throw new Error(data.error || ('请求失败（HTTP ' + res.status + '）'));
        }
        return data;
      });
    });
  }

  /* ── 主题 ───────────────────────────────────────────────────────────── */
  var THEME_COLOR = { light: '#eef6f1', dark: '#04050d' };
  var THEME_ICON = { light: '☀', dark: '☾' };

  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  }

  function applyTheme(theme) {
    var root = document.documentElement;
    // 切换主题时给全站挂一个短暂的过渡类，让颜色交叉淡入而不是硬跳
    root.classList.add('theme-switching');
    window.clearTimeout(applyTheme._timer);
    applyTheme._timer = window.setTimeout(function () {
      root.classList.remove('theme-switching');
    }, 480);

    root.setAttribute('data-theme', theme);
    var meta = $('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', THEME_COLOR[theme] || THEME_COLOR.dark);
    var toggle = $('#themeToggle');
    if (toggle) {
      var icon = $('.theme-icon', toggle);
      if (icon) icon.textContent = THEME_ICON[theme] || THEME_ICON.dark;
      toggle.title = theme === 'dark' ? '切换到明亮模式（白绿科技）' : '切换到黑夜模式（赛博朋克）';
    }
    try { localStorage.setItem('sw:theme', theme); } catch (e) {}
  }

  function initTheme() {
    applyTheme(currentTheme());
    var toggle = $('#themeToggle');
    if (toggle) {
      toggle.addEventListener('click', function () {
        applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
      });
    }
  }

  /* ── 提示气泡 ───────────────────────────────────────────────────────── */
  var TOAST_ICON = { ok: '✓', warn: '!', error: '✕', info: 'i' };

  function toast(message, type, timeout) {
    if (!message) return;
    type = type || 'info';
    var wrap = $('#toasts');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'toasts';
      wrap.id = 'toasts';
      wrap.setAttribute('aria-live', 'polite');
      document.body.appendChild(wrap);
    }
    var el = document.createElement('div');
    el.className = 'toast toast-' + type;
    el.setAttribute('role', 'status');

    var icon = document.createElement('span');
    icon.className = 'toast-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = TOAST_ICON[type] || 'i';

    var text = document.createElement('span');
    text.className = 'toast-text';
    text.textContent = message;

    var close = document.createElement('button');
    close.className = 'toast-close';
    close.type = 'button';
    close.setAttribute('aria-label', '关闭提示');
    close.textContent = '✕';

    el.appendChild(icon);
    el.appendChild(text);
    el.appendChild(close);
    wrap.appendChild(el);

    var timer = setTimeout(dismiss, timeout || (type === 'error' ? 7000 : 4200));
    close.addEventListener('click', dismiss);

    function dismiss() {
      clearTimeout(timer);
      el.classList.add('is-hiding');
      setTimeout(function () { el.remove(); }, REDUCED ? 0 : 260);
    }
  }

  function initToasts() {
    $$('#toasts .toast').forEach(function (el) {
      var close = $('.toast-close', el);
      function dismiss() {
        el.classList.add('is-hiding');
        setTimeout(function () { el.remove(); }, REDUCED ? 0 : 260);
      }
      if (close) close.addEventListener('click', dismiss);
      setTimeout(dismiss, el.classList.contains('toast-error') ? 8000 : 5200);
    });
  }

  /* ── 弹窗 ───────────────────────────────────────────────────────────── */
  function openDialog(id) {
    var dlg = typeof id === 'string' ? document.getElementById(id) : id;
    if (!dlg || typeof dlg.showModal !== 'function') return;
    if (!dlg.open) {
      dlg.showModal();
      // 通知各模块「弹窗已打开」，便于按需加载数据
      dlg.dispatchEvent(new CustomEvent('dialog:open'));
    }
  }

  function initDialogs() {
    $$('[data-open]').forEach(function (btn) {
      btn.addEventListener('click', function () { openDialog(btn.dataset.open); });
    });

    $$('dialog').forEach(function (dlg) {
      $$('[data-close]', dlg).forEach(function (btn) {
        btn.addEventListener('click', function () { dlg.close(); });
      });
      dlg.addEventListener('click', function (event) {
        // 点击遮罩层（dialog 自身）关闭
        if (event.target === dlg) dlg.close();
      });
    });

    function openFromHash() {
      var id = (location.hash || '').replace('#', '');
      if (id && document.getElementById(id) && document.getElementById(id).tagName === 'DIALOG') {
        openDialog(id);
      }
    }
    openFromHash();
    window.addEventListener('hashchange', openFromHash);
  }

  /* ── 删除确认 ───────────────────────────────────────────────────────── */
  function initConfirm() {
    var modal = $('#confirmModal');
    var message = $('#confirmMessage');
    var okBtn = $('#confirmOk');
    if (!modal || !okBtn) return;
    var pending = null;

    $$('form[data-confirm]').forEach(function (form) {
      form.addEventListener('submit', function (event) {
        if (form.dataset.confirmed === '1') return;
        event.preventDefault();
        pending = form;
        if (message) message.textContent = form.dataset.confirm || '确定执行该操作吗？';
        modal.showModal();
      });
    });

    okBtn.addEventListener('click', function () {
      var form = pending;
      pending = null;
      modal.close();
      if (form) {
        form.dataset.confirmed = '1';
        if (typeof form.requestSubmit === 'function') form.requestSubmit();
        else form.submit();
      }
    });

    modal.addEventListener('close', function () { pending = null; });
  }

  /* ── 星级评分控件 ───────────────────────────────────────────────────── */
  function initRating(root, onChange) {
    var stars = $$('.star', root);
    if (!stars.length) return;
    var hidden = $('input[type="hidden"]', root);
    var hint = $('[data-rating-hint]', root);
    var value = stars.filter(function (s) { return s.classList.contains('on'); }).length;

    function paint(next) {
      value = next;
      root.dataset.value = String(next);
      stars.forEach(function (star, index) {
        star.classList.toggle('on', index < next);
        star.setAttribute('aria-pressed', index < next ? 'true' : 'false');
      });
      if (hidden) hidden.value = String(next);
      if (hint) hint.textContent = next ? next + ' 星' : '未评分';
    }

    paint(value);
    stars.forEach(function (star) {
      star.addEventListener('click', function () {
        var picked = Number(star.dataset.value);
        var next = picked === value ? 0 : picked;   // 再点同一颗 = 取消评分
        paint(next);
        if (onChange) onChange(next);
      });
    });
  }

  /* ── 详情页快捷操作 ─────────────────────────────────────────────────── */
  function initQuickActions() {
    var page = $('.detail-page');
    if (!page) return;
    var gameId = page.dataset.gameId;
    if (!gameId) return;

    var price = parseFloat(page.dataset.price || '0') || 0;
    var playtimeEl = $('[data-playtime]', page);
    var costEl = $('[data-cost]', page);
    var statusBadge = $('[data-status-badge]', page);
    var ratingBadge = $('[data-rating-badge]', page);
    var statusButtons = $$('[data-quick-status] [data-status]', page);

    function hoursText(hours) {
      var rounded = Math.abs(hours - Math.round(hours)) < 0.05;
      return (rounded ? Math.round(hours) : hours.toFixed(1)) + '<small> 小时</small>';
    }

    function refreshDerived(hours) {
      if (playtimeEl) playtimeEl.innerHTML = hoursText(hours);
      if (costEl) {
        costEl.textContent = (price > 0 && hours > 0) ? '¥' + (price / hours).toFixed(1) : '—';
      }
    }

    // +N 小时
    $$('[data-quick-playtime]', page).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var amount = btn.dataset.quickPlaytime;
        btn.disabled = true;
        apiPost('/game/' + gameId + '/quick', { action: 'playtime_inc', amount: amount })
          .then(function (data) {
            refreshDerived(Number(data.playtime_hours) || 0);
            toast('已记录 ' + amount + ' 小时，累计 ' + data.playtime_hours + ' 小时', 'ok', 2600);
          })
          .catch(function (err) { toast(err.message, 'error'); })
          .finally(function () { btn.disabled = false; });
      });
    });

    // 评分
    var ratingRoot = $('[data-quick-rating]', page);
    if (ratingRoot) {
      initRating(ratingRoot, function (value) {
        apiPost('/game/' + gameId + '/quick', { action: 'rating', value: value })
          .then(function () {
            if (ratingBadge) {
              ratingBadge.textContent = value ? value + ' 星' : '';
              ratingBadge.hidden = !value;
            }
            toast(value ? '已评 ' + value + ' 星' : '已取消评分', 'ok', 2200);
          })
          .catch(function (err) { toast(err.message, 'error'); });
      });
    }

    // 状态
    statusButtons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var next = btn.dataset.status;
        apiPost('/game/' + gameId + '/quick', { action: 'status', value: next })
          .then(function (data) {
            statusButtons.forEach(function (b) { b.classList.toggle('is-active', b.dataset.status === next); });
            if (statusBadge) {
              statusBadge.className = 'badge badge-status status-' + next;
              statusBadge.textContent = data.status_label || btn.textContent.trim();
            }
            toast('状态已更新为「' + (data.status_label || '') + '」', 'ok', 2200);
          })
          .catch(function (err) { toast(err.message, 'error'); });
      });
    });
  }

  /* ── OCR ────────────────────────────────────────────────────────────── */
  function bindOcr(box) {
    var input = $('[data-ocr-input]', box);
    var result = $('.ocr-result', box);
    var dropzone = $('[data-dropzone]', box);
    var form = box.closest('form');
    if (!input || !result || !form) return;
    var lastData = null;

    function field(name) { return form.querySelector('[name="' + name + '"]'); }

    function fill(name, value) {
      var el = field(name);
      if (!el || el.value) return false;   // 不覆盖用户已填内容
      el.value = value;
      return true;
    }

    function renderCandidates(label, candidates, onPick, current) {
      if (!candidates || candidates.length < 2) return '';
      var html = '<div class="ocr-candidates" data-role="' + label + '">';
      candidates.forEach(function (item) {
        html += '<button type="button" class="candidate' +
                (item.value === current ? ' is-selected' : '') +
                '" data-pick="' + item.value + '">' + item.value + '</button>';
      });
      html += '</div>';
      return html;
    }

    function render(data) {
      lastData = data;
      var html = '';
      var gotDate = false, gotPrice = false;

      if (data.date) {
        gotDate = true;
        html += '<div class="line ok">📅 识别到购买日期：<b>' + data.date + '</b>' +
                (fill('purchase_date', data.date) ? '（已填入）' : '（字段已有内容，未覆盖）') + '</div>';
      }
      if (data.price !== null && data.price !== undefined) {
        gotPrice = true;
        html += '<div class="line ok">💰 识别到金额：<b>¥' + data.price + '</b>' +
                (fill('purchase_price', data.price) ? '（已填入）' : '（字段已有内容，未覆盖）') + '</div>';
      }
      if (!gotDate && !gotPrice) {
        html += '<div class="line miss">没能自动识别出日期或金额，请在下方原文中手动确认。</div>';
      }

      html += renderCandidates('日期候选', data.date_candidates,
                               null, data.date ? data.date.value : null);
      html += renderCandidates('金额候选', data.price_candidates,
                               null, data.price);

      if (data.raw) {
        html += '<details><summary>查看识别原文（' + (data.lines || []).length + ' 行）</summary><pre>' +
                escapeHtml(data.raw) + '</pre></details>';
      }
      result.innerHTML = html;
      result.hidden = false;

      $$('[data-role="日期候选"] .candidate', result).forEach(function (chip) {
        chip.addEventListener('click', function () {
          field('purchase_date').value = chip.dataset.pick;
          $$('.candidate', chip.parentNode).forEach(function (c) { c.classList.remove('is-selected'); });
          chip.classList.add('is-selected');
        });
      });
      $$('[data-role="金额候选"] .candidate', result).forEach(function (chip) {
        chip.addEventListener('click', function () {
          field('purchase_price').value = chip.dataset.pick;
          $$('.candidate', chip.parentNode).forEach(function (c) { c.classList.remove('is-selected'); });
          chip.classList.add('is-selected');
        });
      });
    }

    function run(file) {
      if (!file) return;
      result.hidden = false;
      result.innerHTML = '<div class="line"><span class="spinner"></span> 正在识别…（首次使用需加载模型，约几秒）</div>';
      var body = new FormData();
      body.append('screenshot', file);
      fetch('/ocr', {
        method: 'POST',
        body: body,
        headers: { 'X-CSRF-Token': CSRF },
        credentials: 'same-origin'
      })
        .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, data: d }; }); })
        .then(function (out) {
          if (!out.ok || out.data.error) {
            result.innerHTML = '<div class="line err">OCR 失败：' + escapeHtml(out.data.error || '未知错误') + '</div>';
            return;
          }
          render(out.data);
        })
        .catch(function (err) {
          result.innerHTML = '<div class="line err">识别出错：' + escapeHtml(err.message) + '</div>';
        });
    }

    if (input) input.addEventListener('change', function () { run(input.files[0]); });

    if (dropzone) {
      ['dragenter', 'dragover'].forEach(function (type) {
        dropzone.addEventListener(type, function (e) {
          e.preventDefault();
          dropzone.classList.add('is-dragover');
        });
      });
      ['dragleave', 'drop'].forEach(function (type) {
        dropzone.addEventListener(type, function () { dropzone.classList.remove('is-dragover'); });
      });
      dropzone.addEventListener('drop', function (e) {
        e.preventDefault();
        var files = e.dataTransfer && e.dataTransfer.files;
        if (!files || !files.length) return;
        try {
          var dt = new DataTransfer();
          dt.items.add(files[0]);
          input.files = dt.files;
          run(files[0]);
        } catch (err) { /* 浏览器不支持时忽略拖拽 */ }
      });
    }
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/[&<>"']/g, function (ch) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
      });
  }

  /* ── 笔记自动保存 ───────────────────────────────────────────────────── */
  function initNotes() {
    var ta = $('#notes');
    var status = $('#notesStatus');
    var counter = $('#notesCount');
    var page = $('.detail-page');
    if (!ta || !status || !page) return;
    var gameId = page.dataset.gameId;
    var timer = null;
    var dirty = false;
    var lastSaved = ta.value;

    function count() { if (counter) counter.textContent = String(ta.value.length); }
    count();

    function setStatus(text, cls) {
      status.textContent = text;
      status.className = 'notes-status' + (cls ? ' ' + cls : '');
    }

    function save() {
      clearTimeout(timer);
      setStatus('保存中…');
      apiPost('/game/' + gameId + '/notes', { notes: ta.value })
        .then(function (data) {
          dirty = false;
          lastSaved = ta.value;
          setStatus('✓ 已保存 ' + (data.saved_at || ''));
        })
        .catch(function (err) { setStatus('保存失败：' + err.message, 'is-error'); });
    }

    ta.addEventListener('input', function () {
      count();
      dirty = ta.value !== lastSaved;
      setStatus(dirty ? '未保存…' : '✓ 已保存', dirty ? 'is-dirty' : '');
      clearTimeout(timer);
      timer = setTimeout(save, 900);
    });

    ta.addEventListener('blur', function () { if (dirty) save(); });

    document.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        if (dirty) save();
      }
    });

    window.addEventListener('beforeunload', function (e) {
      if (dirty) {
        e.preventDefault();
        e.returnValue = '';
      }
    });

    setStatus('✓ 已自动保存');
  }

  /* ── 灯箱 ───────────────────────────────────────────────────────────── */
  function initLightbox() {
    var box = $('#lightbox');
    var img = $('#lbImage');
    var caption = $('#lbCaption');
    if (!box || !img) return;
    var items = [];
    var index = 0;

    function collect() {
      items = $$('[data-lightbox]').map(function (el) {
        return { src: el.dataset.lightbox, caption: el.dataset.caption || '' };
      });
    }

    function show(next) {
      if (!items.length) return;
      index = (next + items.length) % items.length;
      img.src = items[index].src;
      img.alt = items[index].caption;
      if (caption) caption.textContent = items[index].caption;
    }

    function open(el) {
      collect();
      var position = 0;
      for (var i = 0; i < items.length; i++) {
        if (items[i].src === el.dataset.lightbox) { position = i; break; }
      }
      box.hidden = false;
      document.body.classList.add('no-scroll');
      show(position);
    }

    function close() {
      box.hidden = true;
      img.src = '';
      document.body.classList.remove('no-scroll');
    }

    document.addEventListener('click', function (e) {
      var trigger = e.target.closest ? e.target.closest('[data-lightbox]') : null;
      if (trigger) { e.preventDefault(); open(trigger); }
    });

    $$('[data-lb-close]', box).forEach(function (b) { b.addEventListener('click', close); });
    $$('[data-lb-prev]', box).forEach(function (b) {
      b.addEventListener('click', function (e) { e.stopPropagation(); show(index - 1); });
    });
    $$('[data-lb-next]', box).forEach(function (b) {
      b.addEventListener('click', function (e) { e.stopPropagation(); show(index + 1); });
    });
    box.addEventListener('click', function (e) { if (e.target === box) close(); });

    document.addEventListener('keydown', function (e) {
      if (box.hidden) return;
      if (e.key === 'Escape') close();
      else if (e.key === 'ArrowLeft') show(index - 1);
      else if (e.key === 'ArrowRight') show(index + 1);
    });
  }

  /* ── 视图切换 ───────────────────────────────────────────────────────── */
  function initViewToggle() {
    var grid = $('#gameGrid');
    var btn = $('#viewToggle');
    if (!grid || !btn) return;
    var saved = 'grid';
    try { saved = localStorage.getItem('sw:view') || 'grid'; } catch (e) {}

    function apply(view) {
      grid.classList.toggle('view-list', view === 'list');
      btn.querySelector('span').textContent = view === 'list' ? '▤' : '▦';
      btn.title = view === 'list' ? '切换到网格视图' : '切换到列表视图';
      try { localStorage.setItem('sw:view', view); } catch (e) {}
    }

    apply(saved);
    btn.addEventListener('click', function () {
      apply(grid.classList.contains('view-list') ? 'grid' : 'list');
    });
  }

  /* ── 杂项：下拉自动提交 / 文件名字 / 快捷键 ─────────────────────────── */
  function initMisc() {
    $$('[data-autosubmit]').forEach(function (el) {
      el.addEventListener('change', function () { el.form.submit(); });
    });

    $$('[data-file-input]').forEach(function (input) {
      var label = input.closest('[data-dropzone]');
      var text = label ? $('[data-file-label]', label) : null;
      if (!text) return;
      input.addEventListener('change', function () {
        var n = input.files.length;
        text.textContent = n ? ('已选择 ' + n + ' 张照片') : '点击或拖拽照片到此处（可多选）';
      });
      if (label) {
        ['dragenter', 'dragover'].forEach(function (t) {
          label.addEventListener(t, function (e) { e.preventDefault(); label.classList.add('is-dragover'); });
        });
        ['dragleave', 'drop'].forEach(function (t) {
          label.addEventListener(t, function () { label.classList.remove('is-dragover'); });
        });
        label.addEventListener('drop', function (e) {
          e.preventDefault();
          var files = e.dataTransfer && e.dataTransfer.files;
          if (!files || !files.length) return;
          try {
            var dt = new DataTransfer();
            Array.prototype.forEach.call(files, function (f) {
              if (f.type.indexOf('image/') === 0) dt.items.add(f);
            });
            input.files = dt.files;
            input.dispatchEvent(new Event('change'));
          } catch (err) {}
        });
      }
    });

    document.addEventListener('keydown', function (e) {
      var tag = (e.target.tagName || '').toLowerCase();
      var typing = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable;

      if (e.key === '/' && !typing) {
        var search = $('#searchInput');
        if (search) { e.preventDefault(); search.focus(); search.select(); }
      }
      if (e.key === 'Escape' && typing && tag === 'input') e.target.blur();
    });
  }

  /* ── 贝塞尔折线图 ───────────────────────────────────────────────────── */
  /* Catmull-Rom 样条转三次贝塞尔：让折线变成平滑曲线 */
  function bezierPath(points, tension) {
    if (points.length < 2) return '';
    var t = tension === undefined ? 1 : tension;
    var d = 'M ' + points[0].x.toFixed(2) + ' ' + points[0].y.toFixed(2);
    for (var i = 0; i < points.length - 1; i++) {
      var p0 = points[i - 1] || points[i];
      var p1 = points[i];
      var p2 = points[i + 1];
      var p3 = points[i + 2] || p2;
      var c1x = p1.x + (p2.x - p0.x) / 6 * t;
      var c1y = p1.y + (p2.y - p0.y) / 6 * t;
      var c2x = p2.x - (p3.x - p1.x) / 6 * t;
      var c2y = p2.y - (p3.y - p1.y) / 6 * t;
      d += ' C ' + c1x.toFixed(2) + ' ' + c1y.toFixed(2) + ', ' +
                   c2x.toFixed(2) + ' ' + c2y.toFixed(2) + ', ' +
                   p2.x.toFixed(2) + ' ' + p2.y.toFixed(2);
    }
    return d;
  }

  function svgEl(name) {
    return document.createElementNS('http://www.w3.org/2000/svg', name);
  }

  function initChart() {
    var root = $('[data-chart]');
    if (!root) return;
    var svg = $('.chart-svg', root);
    var line = $('.chart-line', root);
    var area = $('.chart-area', root);
    var dots = $('.chart-dots', root);
    if (!svg || !line || !area || !dots) return;

    var values = (root.dataset.points || '').split(',').map(function (v) {
      var n = parseFloat(v);
      return isNaN(n) ? 0 : n;
    });
    if (values.length < 2) return;

    function render() {
      var w = Math.max(svg.clientWidth || root.clientWidth || 320, 140);
      var h = svg.clientHeight || 104;
      svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);

      var padX = 7, padTop = 11, padBottom = 9;
      var max = Math.max.apply(null, values);
      var span = h - padTop - padBottom;
      var step = (w - padX * 2) / (values.length - 1);

      var pts = values.map(function (v, i) {
        var ratio = max > 0 ? v / max : 0;
        return { x: padX + step * i, y: padTop + (1 - ratio) * span, value: v };
      });

      var d = bezierPath(pts, 1);
      line.setAttribute('d', d);
      area.setAttribute('d',
        d + ' L ' + pts[pts.length - 1].x.toFixed(2) + ' ' + h +
            ' L ' + pts[0].x.toFixed(2) + ' ' + h + ' Z');

      var peak = max > 0 ? values.indexOf(max) : -1;
      while (dots.firstChild) dots.removeChild(dots.firstChild);
      pts.forEach(function (p, i) {
        var c = svgEl('circle');
        c.setAttribute('cx', p.x.toFixed(2));
        c.setAttribute('cy', p.y.toFixed(2));
        c.setAttribute('r', i === peak ? '4.4' : '3');
        c.setAttribute('class', 'chart-dot' + (i === peak ? ' is-peak' : ''));
        var tip = svgEl('title');
        tip.textContent = p.value > 0 ? ('¥' + p.value) : '当月没有支出';
        c.appendChild(tip);
        dots.appendChild(c);
      });

      var grid = $('.chart-grid', root);
      if (grid) {
        grid.setAttribute('x1', '0');
        grid.setAttribute('x2', String(w));
        grid.setAttribute('y1', String(padTop));
        grid.setAttribute('y2', String(padTop));
      }
    }

    render();

    if (window.ResizeObserver) {
      var ro = new ResizeObserver(function () { render(); });
      ro.observe(svg);
    } else {
      window.addEventListener('resize', render);
    }
  }

  /* ── 指针柔光：跟着光标走的径向渐变 ─────────────────────────────────── */
  function initPointerGlow() {
    if (REDUCED) return;
    if (!window.matchMedia || !window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
    var root = document.documentElement;
    var raf = null, mx = 0, my = 0;

    document.addEventListener('pointermove', function (event) {
      mx = event.clientX;
      my = event.clientY;
      if (raf) return;
      raf = window.requestAnimationFrame(function () {
        raf = null;
        root.style.setProperty('--mx', mx + 'px');
        root.style.setProperty('--my', my + 'px');
      });
    }, { passive: true });
  }

  /* ── 丝滑：数字滚动 + 入场错峰 ──────────────────────────────────────── */
  /* 格式化规则必须与服务端模板一致，否则动画结束会改变显示值 */
  function formatCount(value, mode) {
    if (mode === 'hours') {
      return Math.abs(value - Math.round(value)) < 0.05
        ? String(Math.round(value))
        : value.toFixed(1);
    }
    if (mode === 'money') {
      return Math.abs(value - Math.round(value)) < 0.005
        ? Math.round(value).toLocaleString('zh-CN')
        : value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    return String(Math.round(value));
  }

  function animateCount(el) {
    var target = parseFloat(el.dataset.count);
    if (isNaN(target)) return;
    var mode = el.dataset.format || 'int';
    var duration = 900;
    var startTime = null;
    var finished = false;

    function finish() {
      if (finished) return;
      finished = true;
      el.textContent = formatCount(target, mode);
    }

    // 兜底：若 rAF 没有推进（后台标签页、无头环境等），也要保证最终值正确
    var guard = window.setTimeout(finish, duration + 500);

    function frame(now) {
      if (finished) return;
      if (startTime === null) {
        startTime = now;
        el.textContent = formatCount(0, mode);
      }
      var progress = Math.min(1, (now - startTime) / duration);
      var eased = 1 - Math.pow(1 - progress, 3);   // 平滑收尾
      el.textContent = formatCount(target * eased, mode);
      if (progress < 1) {
        window.requestAnimationFrame(frame);
      } else {
        window.clearTimeout(guard);
        finish();
      }
    }
    window.requestAnimationFrame(frame);
  }

  function initCounters() {
    var nodes = $$('[data-count]');
    if (!nodes.length || REDUCED) return;   // 关闭动效时保留服务端渲染的数字
    if (!window.IntersectionObserver) {
      nodes.forEach(animateCount);
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        io.unobserve(entry.target);
        animateCount(entry.target);
      });
    }, { threshold: 0.35 });
    nodes.forEach(function (el) { io.observe(el); });
  }

  function initReveal() {
    if (REDUCED || !window.IntersectionObserver) return;
    var targets = $$('.card, .stat, .panel');
    if (!targets.length) return;

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        io.unobserve(el);
        el.style.animationDelay = Math.min(Number(el.dataset.revealIndex || 0) * 45, 360) + 'ms';
        el.classList.add('is-entering');
        // 动画结束后移除类，避免 fill 模式压住 hover 的 transform
        el.addEventListener('animationend', function () {
          el.classList.remove('is-entering');
          el.style.animationDelay = '';
        }, { once: true });
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -36px 0px' });

    targets.forEach(function (el, index) {
      el.dataset.revealIndex = String(index % 12);
      io.observe(el);
    });
  }

  /* ── 同步游玩时长：截图识别 + 任天堂账号 ─────────────────────────────── */
  function apiPostJson(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': CSRF,
        'X-Requested-With': 'fetch'
      },
      credentials: 'same-origin',
      body: JSON.stringify(payload || {})
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok || data.ok === false) {
          throw new Error(data.error || ('请求失败（HTTP ' + res.status + '）'));
        }
        return data;
      });
    });
  }

  function initSync() {
    var modal = $('#syncModal');
    if (!modal) return;

    var form = $('.modal-form', modal);
    var resultBox = $('#syncResult');
    var hint = $('#syncHint');
    var applyBtn = $('#syncApply');

    var library = null;      // 按需从 /library 拉取，避免把整库标题塞进首页 HTML
    var rows = [];

    function ensureLibrary() {
      if (library) return Promise.resolve(library);
      return fetch('/library', { credentials: 'same-origin' })
        .then(function (res) { return res.json(); })
        .then(function (data) { library = data.games || []; return library; })
        .catch(function () { library = []; return library; });
    }

    /* 标签切换 */
    $$('[data-sync-tab]', modal).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var target = btn.dataset.syncTab;
        $$('[data-sync-tab]', modal).forEach(function (other) {
          other.classList.toggle('is-active', other === btn);
        });
        $$('[data-sync-pane]', modal).forEach(function (pane) {
          pane.hidden = pane.dataset.syncPane !== target;
        });
        if (target === 'nintendo') refreshNintendo();
      });
    });

    /* ---------- 候选表格 ---------- */
    function gameOptions(selectedId) {
      var html = '<option value="">— 不写入 —</option>';
      library.forEach(function (game) {
        html += '<option value="' + game.id + '"' +
                (String(game.id) === String(selectedId) ? ' selected' : '') + '>' +
                escapeHtml(game.title) + '（当前 ' + game.hours + 'h）</option>';
      });
      return html;
    }

    function renderRows() {
      if (!rows.length) {
        resultBox.hidden = true;
        applyBtn.disabled = true;
        hint.textContent = '';
        return;
      }
      var matched = rows.filter(function (row) { return row.gameId; }).length;
      hint.textContent = '识别 ' + rows.length + ' 条 · 已匹配 ' + matched + ' 条';

      var html = '<table class="sync-table"><thead><tr>' +
        '<th class="col-check"><input type="checkbox" data-sync-all checked aria-label="全选"></th>' +
        '<th>识别到的标题</th><th class="col-hours">时长(h)</th>' +
        '<th>写入到</th><th class="col-mode">方式</th></tr></thead><tbody>';

      rows.forEach(function (row, index) {
        html += '<tr class="' + (row.gameId ? '' : 'is-unmatched') + '">' +
          '<td class="col-check"><input type="checkbox" data-row-check="' + index + '"' +
            (row.checked ? ' checked' : '') + ' aria-label="选中"></td>' +
          '<td><span class="sync-title">' + escapeHtml(row.raw_title) + '</span>' +
            (row.note ? '<span class="sync-note">' + escapeHtml(row.note) + '</span>' : '') + '</td>' +
          '<td class="col-hours"><input class="input input-sm" type="number" min="0" step="0.1" ' +
            'value="' + row.hours + '" data-row-hours="' + index + '"></td>' +
          '<td><select class="input input-sm" data-row-game="' + index + '">' +
            gameOptions(row.gameId) + '</select></td>' +
          '<td class="col-mode"><select class="input input-sm" data-row-mode="' + index + '">' +
            '<option value="set"' + (row.mode === 'set' ? ' selected' : '') + '>覆盖</option>' +
            '<option value="add"' + (row.mode === 'add' ? ' selected' : '') + '>累加</option>' +
            '</select></td></tr>';
      });
      html += '</tbody></table>';

      var unmatched = rows.length - matched;
      if (unmatched > 0) {
        html += '<p class="sync-note-line">有 ' + unmatched +
                ' 条没能在库里找到对应游戏，可用「写入到」下拉手动指定。</p>';
      }
      resultBox.innerHTML = html;
      resultBox.hidden = false;
      applyBtn.disabled = false;
      bindRowEvents();
    }

    function bindRowEvents() {
      var all = $('[data-sync-all]', resultBox);
      if (all) {
        all.addEventListener('change', function () {
          rows.forEach(function (row) { row.checked = all.checked; });
          $$('[data-row-check]', resultBox).forEach(function (box) { box.checked = all.checked; });
        });
      }
      $$('[data-row-check]', resultBox).forEach(function (box) {
        box.addEventListener('change', function () {
          rows[Number(box.dataset.rowCheck)].checked = box.checked;
        });
      });
      $$('[data-row-hours]', resultBox).forEach(function (input) {
        input.addEventListener('input', function () {
          var value = parseFloat(input.value);
          rows[Number(input.dataset.rowHours)].hours = isNaN(value) ? 0 : value;
        });
      });
      $$('[data-row-game]', resultBox).forEach(function (select) {
        select.addEventListener('change', function () {
          var row = rows[Number(select.dataset.rowGame)];
          row.gameId = select.value ? Number(select.value) : null;
          select.closest('tr').classList.toggle('is-unmatched', !row.gameId);
        });
      });
      $$('[data-row-mode]', resultBox).forEach(function (select) {
        select.addEventListener('change', function () {
          rows[Number(select.dataset.rowMode)].mode = select.value;
        });
      });
    }

    function setRows(candidates) {
      rows = candidates.map(function (item) {
        return {
          raw_title: item.raw_title,
          note: item.note || '',
          hours: item.hours,
          gameId: item.match ? item.match.game_id : null,
          mode: item.source === 'nintendo' ? 'add' : 'set',
          checked: !!item.match      // 没匹配到的默认不勾选，避免误写
        };
      });
      ensureLibrary().then(renderRows);
    }

    /* ---------- 截图识别 ---------- */
    var shotInput = $('#playtimeShot', modal);
    if (shotInput) {
      var runScan = function (file) {
        if (!file) return;
        resultBox.hidden = false;
        resultBox.innerHTML = '<div class="line"><span class="spinner"></span> 正在识别游玩记录…</div>';
        var body = new FormData();
        body.append('screenshot', file);
        fetch('/playtime/scan', {
          method: 'POST', body: body,
          headers: { 'X-CSRF-Token': CSRF }, credentials: 'same-origin'
        })
          .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, data: d }; }); })
          .then(function (out) {
            if (!out.ok || !out.data.ok) {
              resultBox.innerHTML = '<div class="line err">识别失败：' +
                escapeHtml(out.data.error || '未知错误') + '</div>';
              return;
            }
            if (!out.data.candidates.length) {
              resultBox.innerHTML = '<div class="line miss">没识别到「游戏名 + 时长」，' +
                '请确认截图里包含游玩时长文字。</div>';
              return;
            }
            setRows(out.data.candidates);
          })
          .catch(function (err) {
            resultBox.innerHTML = '<div class="line err">识别出错：' + escapeHtml(err.message) + '</div>';
          });
      };

      shotInput.addEventListener('change', function () { runScan(shotInput.files[0]); });

      var zone = shotInput.closest('[data-dropzone]');
      if (zone) {
        ['dragenter', 'dragover'].forEach(function (type) {
          zone.addEventListener(type, function (e) { e.preventDefault(); zone.classList.add('is-dragover'); });
        });
        ['dragleave', 'drop'].forEach(function (type) {
          zone.addEventListener(type, function () { zone.classList.remove('is-dragover'); });
        });
        zone.addEventListener('drop', function (e) {
          e.preventDefault();
          var files = e.dataTransfer && e.dataTransfer.files;
          if (files && files.length) runScan(files[0]);
        });
      }
    }

    /* ---------- 任天堂账号 ---------- */
    var ntStatus = $('#ntStatus', modal);
    var ntLinkBox = $('#ntLinkBox', modal);
    var ntSyncBox = $('#ntSyncBox', modal);

    function refreshNintendo() {
      fetch('/nintendo', { credentials: 'same-origin' })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data.linked) {
            var account = data.account || {};
            var device = data.device || {};
            ntStatus.innerHTML =
              '<span class="nt-badge ok">已绑定</span>' +
              '<div class="nt-rows">' +
              '<div><span>账号</span><b>' + escapeHtml(account.nickname || '—') + '</b></div>' +
              '<div><span>主机</span><b>' + escapeHtml(device.name || device.id || '—') + '</b></div>' +
              '<div><span>上次同步</span><b>' + escapeHtml(data.last_sync || '尚未同步') + '</b></div>' +
              '</div>';
            ntLinkBox.hidden = true;
            ntSyncBox.hidden = false;
          } else {
            ntStatus.innerHTML =
              '<span class="nt-badge">未绑定</span>' +
              '<p class="modal-note">绑定后可以直接从任天堂拉取每日游玩时长，无需截图。</p>' +
              '<button class="btn btn-soft btn-sm" type="button" id="ntStartLink">获取登录链接</button>';
            ntLinkBox.hidden = true;
            ntSyncBox.hidden = true;
            var start = $('#ntStartLink', ntStatus);
            if (start) {
              start.addEventListener('click', function () {
                start.disabled = true;
                apiPost('/nintendo/link', {})
                  .then(function (out) {
                    $('#ntUrl', modal).textContent = out.url;
                    ntLinkBox.hidden = false;
                    start.disabled = false;
                  })
                  .catch(function (err) { toast(err.message, 'error'); start.disabled = false; });
              });
            }
          }
        })
        .catch(function () { ntStatus.textContent = '无法读取绑定状态'; });
    }

    var copyBtn = $('#ntCopy', modal);
    if (copyBtn) {
      copyBtn.addEventListener('click', function () {
        var text = $('#ntUrl', modal).textContent;
        if (navigator.clipboard) {
          navigator.clipboard.writeText(text)
            .then(function () { toast('登录链接已复制', 'ok', 2000); })
            .catch(function () { toast('复制失败，请手动选中复制', 'warn'); });
        } else {
          toast('请手动选中链接复制', 'warn');
        }
      });
    }

    var completeBtn = $('#ntComplete', modal);
    if (completeBtn) {
      completeBtn.addEventListener('click', function () {
        var value = ($('#ntCallback', modal).value || '').trim();
        if (!value) { toast('请先粘贴回调链接', 'warn'); return; }
        completeBtn.disabled = true;
        apiPost('/nintendo/complete', { redirect_url: value })
          .then(function (out) {
            toast('绑定成功：' + ((out.account || {}).nickname || ''), 'ok');
            $('#ntCallback', modal).value = '';
            refreshNintendo();
          })
          .catch(function (err) { toast(err.message, 'error', 9000); })
          .finally(function () { completeBtn.disabled = false; });
      });
    }

    var syncBtn = $('#ntSync', modal);
    if (syncBtn) {
      syncBtn.addEventListener('click', function () {
        var days = $('#ntRange', modal).value || '0';
        syncBtn.disabled = true;
        resultBox.hidden = false;
        resultBox.innerHTML = '<div class="line"><span class="spinner"></span> 正在从任天堂拉取游玩时长…</div>';
        apiPost('/nintendo/sync', { days: days })
          .then(function (out) {
            if (!out.candidates.length) {
              resultBox.innerHTML = '<div class="line miss">这段时间没有可用的游玩记录。</div>';
              return;
            }
            setRows(out.candidates);
            var meta = out.stats || {};
            if (meta.sources && meta.sources.length) {
              toast('已拉取 ' + meta.total + ' 条（' + meta.sources.join('、') + '）', 'ok', 5000);
            }
          })
          .catch(function (err) {
            resultBox.innerHTML = '<div class="line err">同步失败：' + escapeHtml(err.message) + '</div>';
          })
          .finally(function () { syncBtn.disabled = false; });
      });
    }

    var unlinkBtn = $('#ntUnlink', modal);
    if (unlinkBtn) {
      unlinkBtn.addEventListener('click', function () {
        if (!window.confirm('解除绑定会删除本机保存的登录令牌，确定吗？')) return;
        apiPost('/nintendo/unlink', {})
          .then(function () { toast('已解除绑定', 'ok'); refreshNintendo(); })
          .catch(function (err) { toast(err.message, 'error'); });
      });
    }

    // 打开弹窗时就先读一次绑定状态，避免切到该页签才闪一下「读取中」
    $$('[data-open="syncModal"]').forEach(function (btn) {
      btn.addEventListener('click', function () { refreshNintendo(); });
    });
    modal.addEventListener('dialog:open', refreshNintendo);
    if (modal.open) refreshNintendo();   // 通过 #syncModal 直接打开的情况

    /* ---------- 写入 ---------- */
    applyBtn.addEventListener('click', function () {
      var items = rows
        .filter(function (row) { return row.checked && row.gameId; })
        .map(function (row) { return { game_id: row.gameId, hours: row.hours, mode: row.mode }; });
      if (!items.length) { toast('没有勾选任何要写入的条目', 'warn'); return; }
      applyBtn.disabled = true;
      apiPostJson('/playtime/apply', { items: items })
        .then(function (out) {
          toast('已更新 ' + out.count + ' 款游戏的时长' +
                (out.errors && out.errors.length ? '；' + out.errors.join('；') : ''), 'ok', 4200);
          setTimeout(function () { window.location.reload(); }, 900);
        })
        .catch(function (err) { toast(err.message, 'error'); applyBtn.disabled = false; });
    });

    modal.addEventListener('close', function () {
      rows = [];
      resultBox.hidden = true;
      applyBtn.disabled = true;
      hint.textContent = '';
    });
  }

  /* ── 启动 ───────────────────────────────────────────────────────────── */
  function boot() {
    initTheme();
    initToasts();
    initDialogs();
    initConfirm();
    initQuickActions();
    initNotes();
    initLightbox();
    initViewToggle();
    initMisc();
    initChart();
    initPointerGlow();
    initCounters();
    initReveal();
    initSync();

    $$('[data-ocr]').forEach(bindOcr);
    $$('[data-rating]').forEach(function (root) { initRating(root); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.SW = { toast: toast, escapeHtml: escapeHtml };
})();
