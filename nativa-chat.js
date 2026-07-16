(function () {
  "use strict";

  var cfg = window.NativaChat || {};
  var API_URL = (cfg.apiUrl || "").replace(/\/$/, "");
  var COLOR = cfg.color || "#2D6A4F";
  var COLOR_DARK = cfg.colorDark || "#1a4a30";
  var BOT_NAME = cfg.botName || "Nati";
  var PAGE_TYPE = cfg.pageType || "general";
  var PRODUCT_NAME = cfg.productName || null;
  var STORAGE_KEY = "nc_history_v2";

  if (!API_URL) { console.warn("[NativaChat] Falta window.NativaChat.apiUrl"); return; }

  /* ── CSS ─────────────────────────────────────────────────────────────────── */
  var css = `
    #nc-btn {
      position:fixed; bottom:90px; right:24px; z-index:99999;
      width:56px; height:56px; border-radius:50%;
      background:${COLOR}; border:none; cursor:pointer;
      box-shadow:0 4px 16px rgba(0,0,0,.28);
      display:flex; align-items:center; justify-content:center;
      transition:transform .2s,background .2s;
    }
    #nc-btn:hover{background:${COLOR_DARK};transform:scale(1.08)}
    #nc-btn svg{width:26px;height:26px;fill:#fff}

    #nc-window {
      position:fixed; bottom:158px; right:24px; z-index:99998;
      width:370px; max-height:560px;
      background:#fff; border-radius:18px;
      box-shadow:0 8px 40px rgba(0,0,0,.18);
      display:flex; flex-direction:column;
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      font-size:14px; overflow:hidden;
      transform:scale(.9) translateY(14px); opacity:0;
      pointer-events:none;
      transition:transform .22s ease,opacity .22s ease;
    }
    #nc-window.nc-open{transform:scale(1) translateY(0);opacity:1;pointer-events:all}

    #nc-header{
      background:${COLOR}; color:#fff; padding:14px 16px;
      display:flex; align-items:center; gap:10px; flex-shrink:0;
    }
    .nc-avatar{
      width:36px;height:36px;border-radius:50%;
      background:rgba(255,255,255,.22);
      display:flex;align-items:center;justify-content:center;
      font-size:18px;flex-shrink:0;
    }
    .nc-hinfo{flex:1}
    .nc-hname{font-weight:600;font-size:15px}
    .nc-hstatus{font-size:11px;opacity:.82;margin-top:1px}
    #nc-close{background:none;border:none;color:#fff;cursor:pointer;padding:4px;font-size:20px;opacity:.75;line-height:1}
    #nc-close:hover{opacity:1}

    #nc-messages{
      flex:1;overflow-y:auto;padding:14px 12px;
      display:flex;flex-direction:column;gap:10px;
      scroll-behavior:smooth;
    }
    #nc-messages::-webkit-scrollbar{width:4px}
    #nc-messages::-webkit-scrollbar-thumb{background:#ddd;border-radius:2px}

    .nc-msg{display:flex;flex-direction:column;max-width:84%}
    .nc-msg.nc-user{align-self:flex-end;align-items:flex-end}
    .nc-msg.nc-bot{align-self:flex-start;align-items:flex-start}

    .nc-bubble{
      padding:9px 13px;border-radius:16px;line-height:1.5;
      word-break:break-word;white-space:pre-wrap;
    }
    .nc-user .nc-bubble{background:${COLOR};color:#fff;border-bottom-right-radius:4px}
    .nc-bot .nc-bubble{background:#f1f3f5;color:#1a1a1a;border-bottom-left-radius:4px}
    .nc-bot .nc-bubble a{color:${COLOR};text-decoration:underline;word-break:break-all}

    /* Sugerencias rápidas */
    #nc-suggestions{
      padding:0 12px 10px;display:flex;flex-wrap:wrap;gap:6px;flex-shrink:0;
    }
    .nc-suggestion{
      background:#fff;border:1.5px solid ${COLOR};color:${COLOR};
      border-radius:20px;padding:6px 13px;font-size:12.5px;
      cursor:pointer;font-family:inherit;transition:background .15s,color .15s;
      white-space:nowrap;
    }
    .nc-suggestion:hover{background:${COLOR};color:#fff}

    /* Escalación */
    .nc-escalate{
      background:#fff8e1;border:1px solid #ffe082;
      border-radius:12px;padding:12px 14px;max-width:290px;margin-top:4px;
    }
    .nc-escalate p{margin:0 0 10px;color:#555;font-size:13px}
    .nc-escalate-btns{display:flex;flex-direction:column;gap:7px}
    .nc-escalate-btns a{
      display:flex;align-items:center;gap:8px;
      padding:9px 13px;border-radius:8px;
      font-size:13px;font-weight:500;text-decoration:none;
      transition:opacity .15s;
    }
    .nc-escalate-btns a:hover{opacity:.88}
    .nc-btn-mail{background:${COLOR};color:#fff}
    .nc-escalate-btns svg{width:16px;height:16px;fill:#fff;flex-shrink:0}

    /* Typing */
    .nc-typing{
      display:flex;align-items:center;gap:4px;
      padding:10px 14px;background:#f1f3f5;
      border-radius:16px;border-bottom-left-radius:4px;width:fit-content;
    }
    .nc-dot{width:7px;height:7px;border-radius:50%;background:#999;animation:nc-bounce .9s infinite}
    .nc-dot:nth-child(2){animation-delay:.15s}
    .nc-dot:nth-child(3){animation-delay:.30s}
    @keyframes nc-bounce{0%,60%,100%{transform:translateY(0)}30%{transform:translateY(-5px)}}

    /* Input */
    #nc-input-area{
      padding:10px 12px;border-top:1px solid #eee;
      display:flex;gap:8px;flex-shrink:0;align-items:flex-end;
    }
    #nc-input{
      flex:1;border:1.5px solid #ddd;border-radius:22px;
      padding:9px 14px;font-size:14px;outline:none;
      resize:none;font-family:inherit;line-height:1.4;
      max-height:80px;overflow-y:auto;
    }
    #nc-input:focus{border-color:${COLOR}}
    #nc-send{
      width:38px;height:38px;border-radius:50%;flex-shrink:0;
      background:${COLOR};border:none;cursor:pointer;
      display:flex;align-items:center;justify-content:center;
      transition:background .15s;
    }
    #nc-send:hover{background:${COLOR_DARK}}
    #nc-send svg{width:18px;height:18px;fill:#fff}
    #nc-send:disabled{opacity:.45;cursor:not-allowed}

    #nc-footer{
      text-align:center;font-size:10px;color:#bbb;
      padding:4px 0 8px;flex-shrink:0;
    }

    @media(max-width:420px){
      #nc-window{width:calc(100vw - 24px);right:12px;bottom:148px}
      #nc-btn{right:12px;bottom:82px}
    }
  `;

  var styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  /* ── HTML ────────────────────────────────────────────────────────────────── */
  var wrapper = document.createElement("div");
  wrapper.innerHTML = `
    <button id="nc-btn" aria-label="Abrir chat con Nati">
      <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
    </button>
    <div id="nc-window" role="dialog" aria-label="Chat Nativa Elements">
      <div id="nc-header">
        <div class="nc-avatar">🌿</div>
        <div class="nc-hinfo">
          <div class="nc-hname">${BOT_NAME} · Nativa Elements</div>
          <div class="nc-hstatus">En línea · Responde al instante</div>
        </div>
        <button id="nc-close" aria-label="Cerrar">✕</button>
      </div>
      <div id="nc-messages"></div>
      <div id="nc-suggestions"></div>
      <div id="nc-input-area">
        <textarea id="nc-input" placeholder="Escribe tu mensaje..." rows="1"></textarea>
        <button id="nc-send" aria-label="Enviar">
          <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
        </button>
      </div>
      <div id="nc-footer">Asistente IA · Nativa Elements</div>
    </div>
  `;
  document.body.appendChild(wrapper);

  /* ── Referencias ─────────────────────────────────────────────────────────── */
  var btn       = document.getElementById("nc-btn");
  var win       = document.getElementById("nc-window");
  var closeBtn  = document.getElementById("nc-close");
  var msgs      = document.getElementById("nc-messages");
  var suggsEl   = document.getElementById("nc-suggestions");
  var input     = document.getElementById("nc-input");
  var sendBtn   = document.getElementById("nc-send");

  /* ── Estado ──────────────────────────────────────────────────────────────── */
  var history  = loadHistory();
  var loading  = false;
  var greeted  = false;

  /* ── Persistencia ────────────────────────────────────────────────────────── */
  function loadHistory() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch { return []; }
  }
  function saveHistory() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(-24))); } catch {}
  }

  /* ── Renderizado ─────────────────────────────────────────────────────────── */
  function scrollDown() { msgs.scrollTop = msgs.scrollHeight; }

  function renderText(text) {
    var s = text.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    // Markdown básico
    s = s.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\*(.*?)\*/g, "<em>$1</em>");
    // URLs clickeables
    s = s.replace(/(https?:\/\/[^\s<]+)/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
    return s;
  }

  function addMessage(role, text) {
    var div = document.createElement("div");
    div.className = "nc-msg nc-" + role;
    var bubble = document.createElement("div");
    bubble.className = "nc-bubble";
    bubble.innerHTML = role === "bot" ? renderText(text) : escapeHtml(text);
    div.appendChild(bubble);
    msgs.appendChild(div);
    scrollDown();
    return div;
  }

  function escapeHtml(t) {
    var d = document.createElement("div");
    d.textContent = t;
    return d.innerHTML;
  }

  function addEscalation(text, email) {
    var div = document.createElement("div");
    div.className = "nc-msg nc-bot";
    var card = document.createElement("div");
    card.className = "nc-escalate";
    card.innerHTML = `
      <p>${escapeHtml(text)}</p>
      <div class="nc-escalate-btns">
        <a href="mailto:${escapeHtml(email)}?subject=Consulta%20Nativa%20Elements" class="nc-btn-mail">
          <svg viewBox="0 0 24 24"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
          ${escapeHtml(email)}
        </a>
      </div>`;
    div.appendChild(card);
    msgs.appendChild(div);
    scrollDown();
  }

  function showTyping() {
    var d = document.createElement("div");
    d.className = "nc-msg nc-bot"; d.id = "nc-typing";
    d.innerHTML = '<div class="nc-typing"><div class="nc-dot"></div><div class="nc-dot"></div><div class="nc-dot"></div></div>';
    msgs.appendChild(d); scrollDown();
  }
  function removeTyping() { var e = document.getElementById("nc-typing"); if(e) e.remove(); }

  /* ── Sugerencias rápidas ─────────────────────────────────────────────────── */
  var SUGGESTIONS = {
    product: [
      { label: "¿Qué tallas hay?", msg: "¿Qué tallas están disponibles?" },
      { label: "Guía de cuidado", msg: "¿Cómo cuido esta prenda?" },
      { label: "¿Cuánto demora el envío?", msg: "¿Cuánto demora el despacho?" },
      { label: "Hablar con alguien", msg: "Quiero hablar con una persona" },
    ],
    cart: [
      { label: "¿Cómo pago?", msg: "¿Qué métodos de pago aceptan?" },
      { label: "¿Cuánto llega el envío?", msg: "¿Cuánto cuesta el envío a mi dirección?" },
      { label: "Cambios y devoluciones", msg: "¿Cuál es la política de cambios?" },
      { label: "Hablar con alguien", msg: "Quiero hablar con una persona" },
    ],
    general: [
      { label: "Ver productos", msg: "¿Qué productos tienen disponibles?" },
      { label: "Info de envíos", msg: "¿Cuánto cuesta el envío y cuánto demora?" },
      { label: "Cambios y devoluciones", msg: "¿Cómo funciona la política de cambios?" },
      { label: "Hablar con alguien", msg: "Quiero hablar con una persona" },
    ],
  };

  function showSuggestions() {
    suggsEl.innerHTML = "";
    var list = SUGGESTIONS[PAGE_TYPE] || SUGGESTIONS.general;
    list.forEach(function(s) {
      var btn = document.createElement("button");
      btn.className = "nc-suggestion";
      btn.textContent = s.label;
      btn.addEventListener("click", function() {
        hideSuggestions();
        send(s.msg);
      });
      suggsEl.appendChild(btn);
    });
  }
  function hideSuggestions() { suggsEl.innerHTML = ""; }

  /* ── Envío ───────────────────────────────────────────────────────────────── */
  async function send(text) {
    text = text.trim();
    if (!text || loading) return;
    loading = true;
    sendBtn.disabled = true;
    hideSuggestions();

    addMessage("user", text);
    input.value = "";
    input.style.height = "auto";
    showTyping();

    try {
      var res = await fetch(API_URL + "/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          history: history,
          page_type: PAGE_TYPE,
          product_name: PRODUCT_NAME,
        }),
      });
      if (!res.ok) throw new Error(res.status);
      var data = await res.json();
      removeTyping();

      history.push({ role: "user", content: text });

      if (data.action === "escalate") {
        addMessage("bot", data.reply);
        addEscalation(data.reply, data.email);
        history.push({ role: "assistant", content: data.reply });
      } else {
        addMessage("bot", data.reply);
        history.push({ role: "assistant", content: data.reply });
      }
      saveHistory();
    } catch(e) {
      removeTyping();
      addMessage("bot", "Lo siento, tuve un problema de conexión. Intenta de nuevo en un momento.");
    }

    loading = false;
    sendBtn.disabled = false;
    input.focus();
  }

  /* ── Abrir / cerrar ──────────────────────────────────────────────────────── */
  function openChat() {
    win.classList.add("nc-open");
    btn.innerHTML = '<svg viewBox="0 0 24 24" style="fill:#fff;width:22px;height:22px"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>';

    if (!greeted) {
      greeted = true;
      // Renderizar historial previo si existe
      if (history.length > 0) {
        history.forEach(function(m) {
          addMessage(m.role === "user" ? "user" : "bot", m.content);
        });
      } else {
        // Saludo contextual según página
        setTimeout(function() {
          var greeting = "Hola, soy Nati, la asistente de Nativa Elements. ¿En qué te puedo ayudar hoy?";
          if (PAGE_TYPE === "product" && PRODUCT_NAME) {
            greeting = "Hola, soy Nati. Veo que estás mirando " + PRODUCT_NAME + ". ¿Te ayudo con la talla, el material o alguna duda?";
          } else if (PAGE_TYPE === "cart") {
            greeting = "Hola, soy Nati. ¿Tienes alguna duda antes de finalizar tu compra? Estoy aquí para ayudarte.";
          }
          addMessage("bot", greeting);
          history.push({ role: "assistant", content: greeting });
          saveHistory();
          showSuggestions();
        }, 280);
      }
      if (history.length > 0) showSuggestions();
    }
    input.focus();
  }

  function closeChat() {
    win.classList.remove("nc-open");
    btn.innerHTML = '<svg viewBox="0 0 24 24" style="fill:#fff;width:26px;height:26px"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>';
  }

  /* ── Eventos ─────────────────────────────────────────────────────────────── */
  btn.addEventListener("click", function() { win.classList.contains("nc-open") ? closeChat() : openChat(); });
  closeBtn.addEventListener("click", closeChat);
  sendBtn.addEventListener("click", function() { send(input.value); });
  input.addEventListener("keydown", function(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input.value); }
  });
  input.addEventListener("input", function() {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 80) + "px";
  });
})();
