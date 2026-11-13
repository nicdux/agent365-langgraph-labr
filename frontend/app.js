import {
  createAvatar, createComposer, createLoading, createMessage,
  createSuggestions, element, safeHttpsUrl,
} from "./components.js";

const STORAGE_KEY = "eduardo-professional-chat-v1";
const QUESTIONS = [
  "Qual é a trajetória profissional do Eduardo?",
  "Quais são as principais áreas de experiência dele?",
  "Em quais temas de tecnologia e liderança ele atua?",
  "Quais experiências profissionais estão disponíveis no perfil?",
  "Como a experiência dele se relaciona com transformação digital?",
];
const messages = document.getElementById("messages");
const emptyState = document.getElementById("empty-state");
const errorBanner = document.getElementById("error-banner");
const errorMessage = document.getElementById("error-message");
const reconnect = document.getElementById("reconnect");
const announcer = document.getElementById("announcer");
const status = document.getElementById("connection-status");
let history = [];
let session = null;
let requestController = null;
let sequence = 0;
let busy = false;

function showError(message, canReconnect = false) {
  errorMessage.textContent = message;
  errorBanner.hidden = false;
  reconnect.hidden = !canReconnect;
}

function clearError() {
  errorBanner.hidden = true;
  reconnect.hidden = true;
}

function storageFailure() {
  showError("O navegador não permitiu guardar o histórico nesta aba. A conversa continua disponível enquanto esta página estiver aberta.");
}

function storeHistory() {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(history));
  } catch (error) {
    if (["SecurityError", "QuotaExceededError"].includes(error.name)) storageFailure();
    else throw error;
  }
}

function validMessage(message, index) {
  const expectedRole = index % 2 === 0 ? "user" : "assistant";
  return message && message.role === expectedRole
    && typeof message.content === "string" && Array.from(message.content).length <= 6000
    && typeof message.time === "string" && Number.isFinite(Date.parse(message.time))
    && Array.isArray(message.sources) && message.sources.length <= 3
    && message.sources.every((source) => source && typeof source.id === "string"
      && typeof source.title === "string" && typeof source.label === "string"
      && (source.url === null || typeof source.url === "string"));
}

function restoreHistory() {
  try {
    const stored = sessionStorage.getItem(STORAGE_KEY);
    if (!stored) return;
    const parsed = JSON.parse(stored);
    if (!Array.isArray(parsed) || parsed.length > 40 || parsed.length % 2
      || !parsed.every(validMessage)) {
      throw new SyntaxError("Invalid conversation format");
    }
    history = parsed;
    messages.replaceChildren(...history.map(createMessage));
    updateEmptyState();
  } catch (error) {
    if (error instanceof SyntaxError) {
      showError("O histórico salvo não pôde ser recuperado. Uma nova conversa foi iniciada.");
      storeHistory();
    } else if (error.name === "SecurityError") storageFailure();
    else throw error;
  }
}

function updateEmptyState() {
  const hasMessages = messages.childElementCount > 0;
  emptyState.hidden = hasMessages;
  messages.hidden = !hasMessages;
}

function scrollMessages() {
  messages.scrollTop = messages.scrollHeight;
}

function modelHistory() {
  const selected = history.slice(-session.limits.history_messages);
  while (selected.reduce((sum, item) => sum + Array.from(item.content).length, 0)
    > session.limits.history_chars) selected.splice(0, 2);
  return selected.map(({ role, content }) => ({ role, content }));
}

class ApiError extends Error {
  constructor(message, statusCode = 0) {
    super(message);
    this.statusCode = statusCode;
  }
}

async function requestJson(path, options) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  let body;
  try {
    body = await response.json();
  } catch (error) {
    if (error instanceof SyntaxError) {
      throw new ApiError("O servidor retornou uma resposta inesperada. Tente reconectar.", response.status);
    }
    throw error;
  }
  if (!response.ok) {
    throw new ApiError(body.error?.message || "Não foi possível concluir a consulta.", response.status);
  }
  return body;
}

function friendlyError(error) {
  if (error instanceof ApiError) return error.message;
  if (error.name === "TimeoutError") return "A consulta demorou mais que o esperado. Tente novamente.";
  return "Não foi possível conectar ao assistente. Verifique a conexão e tente novamente.";
}

async function bootstrap() {
  clearError();
  composer.setState("loading");
  status.textContent = "Conectando com segurança";
  suggestionButtons.forEach((button) => { button.disabled = true; });
  try {
    const data = await requestJson("/api/session", { signal: AbortSignal.timeout(10000) });
    if (typeof data.csrf_token !== "string" || !data.profile
      || typeof data.profile.name !== "string" || !data.limits
      || data.limits.message_chars !== 2000 || data.limits.history_messages !== 10
      || data.limits.history_chars !== 12000 || data.limits.session_messages !== 40) {
      throw new ApiError("A configuração do assistente não pôde ser validada.");
    }
    session = data;
    document.getElementById("profile-name").textContent = data.profile.name;
    const reference = safeHttpsUrl(data.profile.reference_url);
    if (reference) document.getElementById("profile-link").href = reference;
    document.getElementById("knowledge-notice").hidden = data.approved_entries > 0;
    status.replaceChildren(element("span", "status-dot"), document.createTextNode(
      data.approved_entries ? "Pronto · fontes autorizadas" : "Pronto · conteúdo em preparação",
    ));
    composer.setState("ready");
    suggestionButtons.forEach((button) => { button.disabled = false; });
  } catch (error) {
    session = null;
    composer.setState("unavailable");
    status.textContent = "Conexão indisponível";
    showError(friendlyError(error), true);
  }
}

async function sendQuestion(question) {
  if (busy || !session) return;
  clearError();
  busy = true;
  const currentSequence = ++sequence;
  const controller = new AbortController();
  requestController = controller;
  composer.setValue("");
  composer.setState("loading");
  suggestionButtons.forEach((button) => { button.disabled = true; });
  const userMessage = { role: "user", content: question, sources: [], time: new Date().toISOString() };
  const userNode = createMessage(userMessage);
  const loading = createLoading();
  messages.append(userNode, loading);
  updateEmptyState();
  scrollMessages();
  try {
    const data = await requestJson("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf_token },
      body: JSON.stringify({ message: question, history: modelHistory() }),
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(65000)]),
    });
    if (currentSequence !== sequence) return;
    if (typeof data.answer !== "string" || !data.answer || Array.from(data.answer).length > 6000
      || !Array.isArray(data.sources) || data.sources.length > 3
      || !validMessage({
        role: "assistant", content: data.answer, sources: data.sources,
        time: new Date().toISOString(),
      }, 1)) {
      throw new ApiError("Não foi possível validar a resposta recebida.");
    }
    const answer = {
      role: "assistant", content: data.answer, sources: data.sources,
      grounded: data.grounded, time: new Date().toISOString(),
    };
    loading.remove();
    history.push(userMessage, answer);
    const excess = history.length - session.limits.session_messages;
    if (excess > 0) {
      history.splice(0, excess);
      for (let index = 0; index < excess; index++) messages.firstElementChild?.remove();
    }
    messages.append(createMessage(answer));
    storeHistory();
    scrollMessages();
  } catch (error) {
    if (currentSequence !== sequence) return;
    userNode.remove();
    loading.remove();
    updateEmptyState();
    composer.setValue(question);
    const expired = error instanceof ApiError && [401, 403].includes(error.statusCode);
    if (expired) {
      session = null;
      status.textContent = "Sessão expirada · reconecte";
    }
    showError(`${friendlyError(error)} Sua pergunta foi preservada no campo.`, expired);
  } finally {
    if (currentSequence === sequence) {
      busy = false;
      requestController = null;
      composer.setState(session ? "ready" : "unavailable");
      suggestionButtons.forEach((button) => { button.disabled = !session; });
      if (session) composer.focus();
    }
  }
}

const composer = createComposer(sendQuestion);
document.getElementById("composer").append(composer.node);
composer.setState("loading");
const suggestionButtons = createSuggestions(QUESTIONS, (question) => {
  composer.setValue(question);
  composer.focus();
  sendQuestion(question);
});
document.getElementById("suggestions").append(...suggestionButtons);

for (const slot of document.querySelectorAll("[data-avatar]")) {
  slot.append(createAvatar(slot.dataset.avatar));
}

document.getElementById("clear-conversation").addEventListener("click", () => {
  sequence++;
  requestController?.abort();
  requestController = null;
  busy = false;
  history = [];
  messages.replaceChildren();
  updateEmptyState();
  clearError();
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch (error) {
    if (error.name === "SecurityError") storageFailure();
    else throw error;
  }
  composer.setValue("");
  composer.setState(session ? "ready" : "unavailable");
  suggestionButtons.forEach((button) => { button.disabled = !session; });
  announcer.textContent = "Conversa limpa. Você pode começar novamente.";
  if (session) composer.focus();
});

const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
function selectTab(selected, focus = false) {
  for (const tab of tabs) {
    const active = tab === selected;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    document.getElementById(tab.getAttribute("aria-controls")).hidden = !active;
  }
  if (focus) selected.focus();
}
tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    let target;
    if (event.key === "ArrowRight") target = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") target = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") target = 0;
    else if (event.key === "End") target = tabs.length - 1;
    if (target !== undefined) {
      event.preventDefault();
      selectTab(tabs[target], true);
    }
  });
});
document.getElementById("open-eduardo").addEventListener("click", () => {
  selectTab(document.getElementById("tab-eduardo"), true);
});
reconnect.addEventListener("click", async () => {
  await bootstrap();
  if (session) composer.focus();
});
bootstrap();
restoreHistory();
