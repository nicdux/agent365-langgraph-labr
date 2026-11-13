import { marked } from "marked";
import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/core";
import powershell from "highlight.js/lib/languages/powershell";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import python from "highlight.js/lib/languages/python";
import csharp from "highlight.js/lib/languages/csharp";
import json from "highlight.js/lib/languages/json";
import sql from "highlight.js/lib/languages/sql";
import bash from "highlight.js/lib/languages/bash";
import xml from "highlight.js/lib/languages/xml";
import { safeHttpsUrl } from "./conversation.js";

for (const [name, language] of Object.entries({
  powershell, javascript, typescript, python, csharp, json, sql, bash, xml,
})) hljs.registerLanguage(name, language);

export function renderMarkdown(text, sources) {
  const container = document.createElement("div");
  container.className = "message-text markdown";
  const fragment = DOMPurify.sanitize(marked.parse(text, { async: false, gfm: true }), {
    RETURN_DOM_FRAGMENT: true,
    ALLOWED_TAGS: [
      "p", "br", "strong", "em", "s", "ul", "ol", "li", "blockquote", "pre",
      "code", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
      "table", "thead", "tbody", "tr", "th", "td", "a", "span",
    ],
    ALLOWED_ATTR: ["href", "title", "class"],
  });
  for (const node of fragment.querySelectorAll("[class]")) {
    const language = node.tagName === "CODE"
      ? [...node.classList].find((name) => /^language-[a-z0-9#+-]+$/i.test(name))
      : null;
    node.removeAttribute("class");
    if (language) node.classList.add(language);
  }
  const verified = new Set(sources.map((source) => source.url && safeHttpsUrl(source.url)));
  for (const link of fragment.querySelectorAll("a")) {
    const url = safeHttpsUrl(link.getAttribute("href"));
    if (!url || !verified.has(url)) {
      link.replaceWith(document.createTextNode(link.textContent));
      continue;
    }
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.referrerPolicy = "no-referrer";
  }
  for (const code of fragment.querySelectorAll("pre code")) {
    const language = [...code.classList].find((name) => name.startsWith("language-"))?.slice(9);
    const highlighted = language && hljs.getLanguage(language)
      ? hljs.highlight(code.textContent, { language, ignoreIllegals: true })
      : hljs.highlightAuto(code.textContent);
    code.replaceChildren(DOMPurify.sanitize(highlighted.value, {
      RETURN_DOM_FRAGMENT: true, ALLOWED_TAGS: ["span"], ALLOWED_ATTR: ["class"],
    }));
    code.classList.add("hljs");
    code.parentElement.tabIndex = 0;
    code.parentElement.setAttribute("aria-label", `Bloco de código${language ? ` em ${language}` : ""}`);
  }
  for (const table of fragment.querySelectorAll("table")) {
    const wrapper = document.createElement("div");
    wrapper.className = "table-scroll";
    wrapper.tabIndex = 0;
    wrapper.setAttribute("role", "region");
    wrapper.setAttribute("aria-label", "Tabela da resposta, com rolagem horizontal");
    table.replaceWith(wrapper);
    wrapper.append(table);
  }
  container.append(fragment);
  return container;
}
