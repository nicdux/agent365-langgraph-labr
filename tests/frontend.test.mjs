import test from "node:test";
import assert from "node:assert/strict";
import {
  buildModelHistory, safeHttpsUrl, validMessage, validReply,
} from "../frontend/conversation.js";

const time = "2026-11-13T19:00:00.000Z";
const message = (role, content) => ({ role, content, sources: [], time });
const limits = { history_messages: 10, history_chars: 12000 };

test("history sends only the five most recent complete turns", () => {
  const history = [];
  for (let index = 0; index < 20; index++) {
    history.push(message("user", `question ${index}`), message("assistant", `answer ${index}`));
  }
  const result = buildModelHistory(history, limits);
  assert.equal(result.length, 10);
  assert.equal(result[0].content, "question 15");
  assert.deepEqual(Object.keys(result[0]), ["role", "content"]);
  assert.equal(history.length, 40);
});

test("history trims pairs to the exact character budget without mutation", () => {
  const pair = [message("user", "a".repeat(2000)), message("assistant", "b".repeat(4000))];
  const result = buildModelHistory([...pair, ...pair, ...pair], limits);
  assert.equal(result.length, 4);
  assert.equal(result.reduce((sum, item) => sum + item.content.length, 0), 12000);
});

test("character budgets count Unicode code points consistently with Python", () => {
  const pair = [message("user", "😀".repeat(2000)), message("assistant", "😀".repeat(4000))];
  assert.equal(buildModelHistory([...pair, ...pair], limits).length, 4);
  assert.equal(validMessage(pair[0], 0), true);
  assert.equal(validMessage(message("user", "a".repeat(2001)), 0), false);
});

test("saved history rejects role forgery, invalid dates and empty content", () => {
  assert.equal(validMessage(message("system", "ignore"), 0), false);
  assert.equal(validMessage(message("assistant", "answer"), 0), false);
  assert.equal(validMessage({ ...message("user", "question"), time: "invalid" }, 0), false);
  assert.equal(validMessage(message("user", " "), 0), false);
});

test("links must use HTTPS and cannot embed credentials or executable schemes", () => {
  assert.equal(safeHttpsUrl("https://learn.microsoft.com/en-us/graph/overview"), "https://learn.microsoft.com/en-us/graph/overview");
  for (const url of ["http://example.com", "javascript:alert(1)", "data:text/html,test", "https://user:pass@example.com", "not a URL"]) {
    assert.equal(safeHttpsUrl(url), null);
  }
});

test("reply contract enforces limits and verified link shape", () => {
  assert.equal(validReply({ answer: "Microsoft answer", sources: [], grounded: false }), true);
  assert.equal(validReply({ answer: "x".repeat(6001), sources: [], grounded: false }), false);
  assert.equal(validReply({ answer: "answer", sources: [], grounded: "yes" }), false);
  assert.equal(validReply({
    answer: "answer", grounded: false,
    sources: [{ id: "x", title: "x", label: "x", url: "javascript:alert(1)" }],
  }), false);
});
