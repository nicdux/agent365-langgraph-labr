export function safeHttpsUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch (error) {
    if (error instanceof TypeError) return null;
    throw error;
  }
}

export function validMessage(message, index) {
  const expectedRole = index % 2 === 0 ? "user" : "assistant";
  const limit = expectedRole === "user" ? 2000 : 6000;
  return Boolean(message && message.role === expectedRole
    && typeof message.content === "string" && message.content.trim()
    && Array.from(message.content).length <= limit
    && typeof message.time === "string" && Number.isFinite(Date.parse(message.time))
    && Array.isArray(message.sources) && message.sources.length <= 3
    && message.sources.every((source) => source && typeof source.id === "string"
      && typeof source.title === "string" && typeof source.label === "string"
      && (source.url === null || (typeof source.url === "string" && safeHttpsUrl(source.url)))));
}

export function validReply(data) {
  return Boolean(data && typeof data.grounded === "boolean" && validMessage({
    role: "assistant", content: data.answer, sources: data.sources,
    time: new Date().toISOString(),
  }, 1));
}

export function buildModelHistory(history, limits) {
  const selected = history.slice(-limits.history_messages);
  while (selected.reduce((sum, item) => sum + Array.from(item.content).length, 0)
    > limits.history_chars) selected.splice(0, 2);
  return selected.map(({ role, content }) => ({ role, content }));
}
