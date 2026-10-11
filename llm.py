"""LLM client: any OpenAI-compatible chat endpoint, with an on-disk response cache,
token accounting and cost, plus a scripted fake model for tests and demos that must
not call an external API.

Environment
-----------
LLM_BASE_URL, LLM_API_KEY, LLM_MODEL   endpoint and model (LLM_MODEL=fake selects the fake model)
LLM_PRICE_IN_PER_M / LLM_PRICE_OUT_PER_M   optional USD price per 1M tokens; when unset,
                                            cost is recorded as None (never invented)

The fake model is deliberately simple: on the first turn it calls ``search`` with the
user's question verbatim; on the second turn it takes the first sentence of the first
two search results and joins them, tagging each with ``[doc_id]``. That is enough to
exercise the whole loop, the guard and the audit without any network access.
"""
import hashlib
import json
import os
import re
import sqlite3


def _env_float(name):
    raw = os.environ.get(name)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _sha(*parts):
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class LLM:
    def __init__(self, model=None, base_url=None, api_key=None, temperature=0.0,
                 cache_path=".cache/llm.sqlite"):
        self.model = model or os.environ.get("LLM_MODEL") or "fake"
        self.base_url = base_url or os.environ.get("LLM_BASE_URL")
        self.api_key = api_key or os.environ.get("LLM_API_KEY")
        self.temperature = temperature
        self.cache_path = cache_path
        self.price_in = _env_float("LLM_PRICE_IN_PER_M")
        self.price_out = _env_float("LLM_PRICE_OUT_PER_M")
        self.calls = self.cache_hits = 0
        self.prompt_tokens = self.completion_tokens = 0
        self.cost = 0.0
        self._db = None
        self._client = None

    @property
    def is_fake(self):
        return self.model == "fake"

    # ---------------------------------------------------------------- caching
    def _cache(self):
        if self._db is None:
            directory = os.path.dirname(self.cache_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._db = sqlite3.connect(self.cache_path)
            self._db.execute("create table if not exists llm (k text primary key, v text)")
        return self._db

    # ------------------------------------------------------------------ chat
    def chat(self, messages, tools=None):
        """Return {"message", "usage", "cost", "model"} for one completion."""
        key = _sha(self.model, messages, tools, self.temperature)
        db = self._cache()
        row = db.execute("select v from llm where k=?", (key,)).fetchone()
        if row:
            self.cache_hits += 1
            return json.loads(row[0])
        response = self._fake_chat(messages, tools) if self.is_fake else self._remote_chat(messages, tools)
        self._account(response)
        db.execute("insert or replace into llm values (?, ?)", (key, json.dumps(response, ensure_ascii=False)))
        db.commit()
        return response

    def _remote_chat(self, messages, tools=None):
        from openai import OpenAI
        if self._client is None:
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        kwargs = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if tools:
            kwargs["tools"] = tools
        completion = self._client.chat.completions.create(**kwargs)
        message = completion.choices[0].message
        out = {"role": "assistant", "content": message.content}
        if message.tool_calls:
            out["tool_calls"] = [{"id": tc.id, "type": "function",
                                  "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                 for tc in message.tool_calls]
        usage = {"prompt_tokens": completion.usage.prompt_tokens,
                 "completion_tokens": completion.usage.completion_tokens}
        return {"message": out, "usage": usage, "model": self.model}

    def _fake_chat(self, messages, tools=None):
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        if not tool_messages:
            question = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
            return {"message": {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_search_1", "type": "function",
                         "function": {"name": "search", "arguments": json.dumps({"query": question})}}]},
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0}, "model": "fake"}
        try:
            payload = json.loads(tool_messages[-1]["content"])
        except (ValueError, TypeError):
            payload = {}
        parts = []
        for result in (payload.get("results") or [])[:2]:
            snippet = (result.get("snippet") or "").strip()
            if not snippet:
                continue
            first_sentence = re.split(r"(?<=[.!?])\s+", snippet)[0]
            parts.append(f"{first_sentence} [{result.get('doc_id')}]")
        return {"message": {"role": "assistant", "content": " ".join(parts)},
                "usage": {"prompt_tokens": 0, "completion_tokens": 0}, "model": "fake"}

    def _account(self, response):
        self.calls += 1
        usage = response.get("usage") or {}
        prompt = usage.get("prompt_tokens", 0) or 0
        completion = usage.get("completion_tokens", 0) or 0
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        cost = None
        if self.price_in is not None and self.price_out is not None:
            cost = prompt / 1e6 * self.price_in + completion / 1e6 * self.price_out
            self.cost += cost
        response["cost"] = cost
