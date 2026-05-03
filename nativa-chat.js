(function () {
  "use strict";

  var cfg = window.NativaChat || {};
  var API_URL = (cfg.apiUrl || "").replace(/\/$/, "");
  var COLOR = cfg.color || "#2D6A4F";
  var COLOR_DARK = cfg.colorDark || "#1a4a30";
  var BOT_NAME = cfg.botName || "Nati";

  if (!API_URL) {
    console.warn("[NativaChat] Falta window.NativaChat.apiUrl");
    return;
  }

  /* ── Estilos ─────────────────────────────────────────────────────────────── */
  var css = `
    #nc-btn {
      position: fixed; bottom: 90px; right: 24px; z-index: 99999;
      width: 56px; height: 56px; border-radius: 50%;
      background: ${COLOR}; border: none; cursor: pointer;
      box-shadow: 0 4px 16px rgba(0,0,0,.25);
      display: flex; align-items: center; justify-content: center;
      transition: transform .2s, background .2s;
    }
    #nc-btn:hover { background: ${COLOR_DARK}; transform: scale(1.08); }
    #nc-btn svg { width: 26px; height: 26px; fill: #fff; }

    #nc-window {
      position: fixed; bottom: 158px; right: 24px; z-index: 99998;
      width: 360px; max-height: 540px;
      background: #fff; border-radius: 16px;
      box-shadow: 0 8px 32px rgba(0,0,0,.18);
      display: flex; flex-direction: column;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      font-size: 14px; overflow: hidden;
      transform: scale(.9) translateY(12px); opacity: 0;
      pointer-events: none;
      transition: transform .22s ease, opacity .22s ease;
    }
    #nc-window.nc-open {
      transform: scale(1) translateY(0); opacity: 1;
      pointer-events: all;
    }

    #nc-header {
      background: ${COLOR}; color: #fff; padding: 14px 16px;
      display: flex; align-items: center; gap: 10px; flex-shrink: 0;
    }
    #nc-header .nc-avatar {
      width: 34px; height: 34px; border-radius: 50%;
      background: rgba(255,255,255,.25);
      display: flex; align-items: center; justify-content: center;
      font-size: 17px; flex-shrink: 0;
    }
    #nc-header .nc-info { flex: 1; }
    #nc-header .nc-name { font-weight: 600; font-size: 15px; }
    #nc-header .nc-status { font-size: 11px; opacity: .85; margin-top: 1px; }
    #nc-close {
      background: none; border: none; color: #fff; cursor: pointer;
      padding: 4px; line-height: 1; font-size: 20px; opacity: .8;
    }
    #nc-close:hover { opacity: 1; }

    #nc-messages {
      flex: 1; overflow-y: auto; padding: 14px 12px;
      display: flex; flex-direction: column; gap: 10px;
      scroll-behavior: smooth;
    }
    #nc-messages::-webkit-scrollbar { width: 4px; }
    #nc-messages::-webkit-scrollbar-thumb { background: #ddd; border-radius: 2px; }

    .nc-msg { display: flex; flex-direction: column; max-width: 82%; }
    .nc-msg.nc-user { align-self: flex-end; align-items: flex-end; }
    .nc-msg.nc-bot  { align-self: flex-start; align-items: flex-start; }

    .nc-bubble {
      padding: 9px 13px; border-radius: 16px; line-height: 1.45;
      word-break: break-word; white-space: pre-wrap;
    }
    .nc-user .nc-bubble {
      background: ${COLOR}; color: #fff;
      border-bottom-right-radius: 4px;
    }
    .nc-bot .nc-bubble {
      background: #f1f3f5; color: #1a1a1a;
      border-bottom-left-radius: 4px;
    }

    .nc-escalate {
      background: #fff8e1; border: 1px solid #ffe082;
      border-radius: 12px; padding: 12px 14px; max-width: 280px;
      margin-top: 4px;
    }
    .nc-escalate p { margin: 0 0 10px; color: #555; font-size: 13px; }
    .nc-escalate-btns { display: flex; flex-direction: column; gap: 7px; }
    .nc-escalate-btns a {
      display: flex; align-items: center; gap: 8px;
      padding: 9px 13px; border-radius: 8px;
      font-size: 13px; font-weight: 500; text-decoration: none;
      transition: opacity .15s;
    }
    .nc-escalate-btns a:hover { opacity: .88; }
    .nc-btn-wsp { background: #25D366; color: #fff; }
    .nc-btn-mail { background: ${COLOR}; color: #fff; }
    .nc-escalate-btns svg { width: 16px; height: 16px; fill: #fff; flex-shrink: 0; }

    .nc-typing {
      display: flex; align-items: center; gap: 4px;
      padding: 10px 14px; background: #f1f3f5;
      border-radius: 16px; border-bottom-left-radius: 4px;
      width: fit-content;
    }
    .nc-dot {
      width: 7px; height: 7px; border-radius: 50%;
      background: #999; animation: nc-bounce .9s infinite;
    }
    .nc-dot:nth-child(2) { animation-delay: .15s; }
    .nc-dot:nth-child(3) { animation-delay: .30s; }
    @keyframes nc-bounce {
      0%, 60%, 100% { transform: translateY(0); }
      30% { transform: translateY(-5px); }
    }

    #nc-input-area {
      padding: 10px 12px; border-top: 1px solid #eee;
      display: flex; gap: 8px; flex-shrink: 0;
    }
    #nc-input {
      flex: 1; border: 1px solid #ddd; border-radius: 22px;
      padding: 9px 14px; font-size: 14px; outline: none;
      resize: none; font-family: inherit; line-height: 1.4;
      max-height: 80px; overflow-y: auto;
    }
    #nc-input:focus { border-color: ${COLOR}; }
    #nc-send {
      width: 38px; height: 38px; border-radius: 50%; flex-shrink: 0;
      background: ${COLOR}; border: none; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      transition: background .15s;
    }
    #nc-send:hover { background: ${COLOR_DARK}; }
    #nc-send svg { width: 18px; height: 18px; fill: #fff; }
    #nc-send:disabled { opacity: .5; cursor: not-allowed; }

    @media (max-width: 420px) {
      #nc-window { width: calc(100vw - 24px); right: 12px; bottom: 148px; }
      #nc-btn { right: 12px; bottom: 82px; }
    }
  `;

  var styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  /* ── HTML ────────────────────────────────────────────────────────────────── */
  var html = `
    <button id="nc-btn" aria-label="Abrir chat">
      <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
    </button>

    <div id="nc-window" role="dialog" aria-label="Chat con ${BOT_NAME}">
      <div id="nc-header">
        <div class="nc-avatar">🌿</div>
        <div class="nc-info">
          <div class="nc-name">${BOT_NAME} · Nativa Elements</div>
          <div class="nc-status">En línea · Responde al instante</div>
        </div>
        <button id="nc-close" aria-label="Cerrar chat">✕</button>
      </div>
      <div id="nc-messages"></div>
      <div id="nc-input-area">
        <textarea id="nc-input" placeholder="Escribe tu mensaje..." rows="1"></textarea>
        <button id="nc-send" aria-label="Enviar">
          <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
        </button>
      </div>
    </div>
  `;

  var wrapper = document.createElement("div");
  wrapper.innerHTML = html;
  document.body.appendChild(wrapper);

  /* ── Referencias ─────────────────────────────────────────────────────────── */
  var btn     = document.getElementById("nc-btn");
  var win     = document.getElementById("nc-window");
  var closeBtn= document.getElementById("nc-close");
  var msgs    = document.getElementById("nc-messages");
  var input   = document.getElementById("nc-input");
  var sendBtn = document.getElementById("nc-send");

  /* ── Estado ──────────────────────────────────────────────────────────────── */
  var history  = [];
  var loading  = false;
  var greeted  = false;

  /* ── Helpers ─────────────────────────────────────────────────────────────── */
  function scrollDown() {
    msgs.scrollTop = msgs.scrollHeight;
  }

  function addMessage(role, text) {
    var div = document.createElement("div");
    div.className = "nc-msg nc-" + role;
    var bubble = document.createElement("div");
    bubble.className = "nc-bubble";
    bubble.textContent = text;
    div.appendChild(bubble);
    msgs.appendChild(div);
    scrollDown();
    return div;
  }

  function addEscalation(text, wsp, email) {
    var div = document.createElement("div");
    div.className = "nc-msg nc-bot";

    var card = document.createElement("div");
    card.className = "nc-escalate";
    card.innerHTML = `
      <p>${text}</p>
      <div class="nc-escalate-btns">
        <a href="https://wa.me/${wsp}?text=Hola%2C+necesito+ayuda+con+mi+pedido" target="_blank" class="nc-btn-wsp">
          <svg viewBox="0 0 24 24"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
          WhatsApp
        </a>
        <a href="mailto:${email}?subject=Consulta%20Nativa%20Elements" class="nc-btn-mail">
          <svg viewBox="0 0 24 24"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
          ${email}
        </a>
      </div>
    `;
    div.appendChild(card);
    msgs.appendChild(div);
    scrollDown();
  }

  function showTyping() {
    var div = document.createElement("div");
    div.className = "nc-msg nc-bot";
    div.id = "nc-typing-indicator";
    div.innerHTML = '<div class="nc-typing"><div class="nc-dot"></div><div class="nc-dot"></div><div class="nc-dot"></div></div>';
    msgs.appendChild(div);
    scrollDown();
  }

  function removeTyping() {
    var el = document.getElementById("nc-typing-indicator");
    if (el) el.remove();
  }

  /* ── Envío de mensaje ────────────────────────────────────────────────────── */
  async function send(text) {
    if (!text.trim() || loading) return;
    loading = true;
    sendBtn.disabled = true;

    addMessage("user", text);
    input.value = "";
    input.style.height = "auto";

    showTyping();

    try {
      var res = await fetch(API_URL + "/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: history }),
      });

      if (!res.ok) throw new Error("Error " + res.status);
      var data = await res.json();

      removeTyping();

      history.push({ role: "user", content: text });

      if (data.action === "escalate") {
        addMessage("bot", data.reply);
        addEscalation(data.reply, data.wsp, data.email);
        history.push({ role: "assistant", content: data.reply });
      } else {
        addMessage("bot", data.reply);
        history.push({ role: "assistant", content: data.reply });
      }
    } catch (e) {
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
      setTimeout(function () {
        addMessage("bot", "Hola! Soy Nati, la asistente virtual de Nativa Elements. ¿En qué te puedo ayudar hoy? Puedo orientarte sobre productos, stock, envíos o el estado de tu pedido.");
      }, 300);
    }
    input.focus();
  }

  function closeChat() {
    win.classList.remove("nc-open");
    btn.innerHTML = '<svg viewBox="0 0 24 24" style="fill:#fff;width:26px;height:26px"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>';
  }

  /* ── Eventos ─────────────────────────────────────────────────────────────── */
  btn.addEventListener("click", function () {
    win.classList.contains("nc-open") ? closeChat() : openChat();
  });

  closeBtn.addEventListener("click", closeChat);

  sendBtn.addEventListener("click", function () {
    send(input.value);
  });

  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input.value);
    }
  });

  input.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 80) + "px";
  });
})();
