const SVG_NS = "http://www.w3.org/2000/svg";

export function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

export function icon(name) {
  const paths = {
    arrow: "M7 17 17 7M7 7h10v10",
    send: "M12 19V5m-6 6 6-6 6 6",
    user: "M20 21v-2a7 7 0 0 0-14 0v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8",
    copy: "M9 9h11v12H9zM5 15H3V3h12v2",
  };
  const svg = document.createElementNS(SVG_NS, "svg");
  for (const [key, value] of Object.entries({
    width: "16", height: "16", viewBox: "0 0 24 24",
    fill: "none", stroke: "currentColor", "stroke-width": "1.7",
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
  })) svg.setAttribute(key, value);
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", paths[name] || paths.arrow);
  svg.append(path);
  return svg;
}

export function createAvatar(size = "small", initials = "EC") {
  const avatar = element("span", `avatar avatar-${size}`);
  avatar.setAttribute("role", "img");
  avatar.setAttribute("aria-label", "Avatar ilustrado do assistente de IA do Eduardo");
  const fallback = element("span", "", initials);
  fallback.setAttribute("aria-hidden", "true");
  const image = element("img");
  image.src = "/static/assets/eduardo-avatar.png";
  image.alt = "";
  image.width = 160;
  image.height = 160;
  image.addEventListener("error", () => image.remove(), { once: true });
  avatar.append(fallback, image);
  return avatar;
}

export function createSuggestions(questions, onSelect) {
  return questions.map((question) => {
    const button = element("button", "suggestion");
    button.type = "button";
    button.append(element("span", "", question), icon("arrow"));
    button.addEventListener("click", () => onSelect(question));
    return button;
  });
}

export function createMessage(message) {
  const user = message.role === "user";
  const article = element("article", `message message-${message.role}`);
  article.setAttribute("aria-label", user ? "Sua pergunta" : "Resposta do assistente de IA");
  if (user) {
    const avatar = element("span", "user-avatar");
    avatar.setAttribute("aria-hidden", "true");
    avatar.append(icon("user"));
    article.append(avatar);
  } else {
    const avatar = createAvatar("message");
    avatar.setAttribute("aria-hidden", "true");
    article.append(avatar);
  }
  const content = element("div", "message-content");
  const meta = element("div", "message-meta");
  meta.append(element("strong", "", user ? "Você" : "Assistente de IA"));
  const time = element("time", "", new Intl.DateTimeFormat("pt-BR", {
    hour: "2-digit", minute: "2-digit",
  }).format(new Date(message.time)));
  time.dateTime = message.time;
  meta.append(time);
  content.append(meta, user
    ? element("p", "message-text", message.content)
    : renderMarkdown(message.content, message.sources));
  if (!user && message.sources.length) {
    const details = element("details", "sources");
    details.append(element("summary", "", `${message.sources.length} referência(s) Microsoft Learn`));
    const list = element("ul");
    for (const source of message.sources) {
      const item = element("li");
      item.append(element("span", "source-title", source.title));
      const url = source.url ? safeHttpsUrl(source.url) : null;
      if (url) {
        const link = element("a", "", `${source.label} ↗`);
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.referrerPolicy = "no-referrer";
        link.setAttribute("aria-label", `${source.label} (abre em nova aba)`);
        item.append(link);
      } else {
        item.append(element("span", "", source.label));
      }
      list.append(item);
    }
    details.append(list);
    content.append(details);
  }
  if (!user) {
    const actions = element("div", "message-actions");
    const copy = element("button", "copy-button");
    copy.type = "button";
    copy.setAttribute("aria-label", "Copiar resposta");
    copy.append(icon("copy"), element("span", "", "Copiar"));
    const feedback = element("span", "copy-feedback");
    feedback.setAttribute("role", "status");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(message.content);
        feedback.textContent = "Resposta copiada.";
      } catch {
        feedback.textContent = "Não foi possível copiar. Selecione o texto da resposta.";
      }
    });
    actions.append(copy, feedback);
    content.append(actions);
  }
  article.append(content);
  return article;
}

export function createLoading() {
  const wrapper = element("div", "message loading-message");
  wrapper.setAttribute("role", "status");
  const avatar = createAvatar("message");
  avatar.setAttribute("aria-hidden", "true");
  const content = element("div", "message-content");
  const dots = element("span", "loading-dots");
  dots.setAttribute("aria-hidden", "true");
  dots.append(element("span"), element("span"), element("span"));
  content.append(dots, element("span", "loading-label", "Analisando a pergunta e validando a resposta…"));
  wrapper.append(avatar, content);
  return wrapper;
}

export function createComposer(onSubmit) {
  const form = element("form", "composer-form");
  const label = element("label", "sr-only", "Sua pergunta sobre tecnologias Microsoft");
  label.htmlFor = "question";
  const input = element("textarea");
  input.id = "question";
  input.name = "question";
  input.rows = 2;
  input.maxLength = 2000;
  input.required = true;
  input.placeholder = "Pergunte sobre tecnologias Microsoft…";
  input.setAttribute("aria-describedby", "composer-help character-count");
  const bottom = element("div", "composer-bottom");
  const help = element("p", "composer-help", "Enter para enviar · Shift + Enter para nova linha");
  help.id = "composer-help";
  const controls = element("div", "composer-controls");
  const count = element("span", "character-count", "0 / 2.000");
  count.id = "character-count";
  const send = element("button", "send-button");
  send.type = "submit";
  const sendLabel = element("span", "", "Enviar");
  send.append(sendLabel, icon("send"));
  controls.append(count, send);
  bottom.append(help, controls);
  form.append(label, input, bottom);
  let busy = true;

  function update() {
    const length = Array.from(input.value).length;
    count.textContent = `${length.toLocaleString("pt-BR")} / 2.000`;
    send.disabled = busy || !input.value.trim() || length > 2000;
  }
  input.addEventListener("input", update);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!send.disabled) form.requestSubmit();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!busy && input.value.trim()) onSubmit(input.value.trim());
  });

  return {
    node: form,
    setState(state) {
      busy = state !== "ready";
      input.disabled = busy;
      sendLabel.textContent = state === "loading" ? "Aguarde" : state === "ready" ? "Enviar" : "Indisponível";
      update();
    },
    setValue(value) { input.value = value; update(); },
    focus() { input.focus(); },
  };
}
import { renderMarkdown } from "./markdown.js";
import { safeHttpsUrl } from "./conversation.js";

export { safeHttpsUrl };
