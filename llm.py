"""Model layer: a LangChain-compatible chat model (a real OpenAI-compatible
endpoint, or a scripted fake), with an on-disk response cache and token/cost
accounting.

    model = LLM()                       # LLM_MODEL selects the model; "fake" by default
    model.invoke(messages)              # -> AIMessage
    model.bind_tools(schemas)           # -> a model that may emit tool calls

Scripted fakes (selected by ``LLM_MODEL``), all implementing ``bind_tools`` /
``invoke`` and returning an ``AIMessage``:

* ``fake``   first turn calls ``search`` with the question verbatim; second turn
             joins the first sentence of the top two results, each tagged ``[doc_id]``.
             A question containing "repeat" echoes the previous assistant answer.
* ``oracle`` used by the evaluation only: it is told each case's gold first, then
             opens and cites it (answer cases) or emits the refusal (deny cases).
* ``leaky``  used by the evaluation only: cites a visible document *and* a sentence
             carrying an invisible document's canary, to measure the leak rate.

Token counts come from ``usage_metadata``; cost is tokens x
``LLM_PRICE_IN_PER_M`` / ``LLM_PRICE_OUT_PER_M`` (recorded as None when unset).
"""
import hashlib
import json
import os
import re
import sqlite3

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

FAKE_MODELS = ("fake", "oracle", "leaky")


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


def _as_dict(message):
    if isinstance(message, dict):
        return message
    out = {"type": getattr(message, "type", "?"), "content": getattr(message, "content", "")}
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        out["tool_calls"] = [{"name": tc["name"], "args": tc["args"]} for tc in tool_calls]
    if isinstance(message, ToolMessage):
        out["tool_call_id"] = message.tool_call_id
    return out


def _first_sentence(text):
    return re.split(r"(?<=[.!?])\s+", (text or "").strip())[0]


class _FakeModel:
    """Scripted model implementing bind_tools() / invoke() -> AIMessage."""

    def __init__(self, kind):
        self.kind = kind
        self.case = None      # oracle/leaky configuration, set by the evaluation
        self.tools = None

    def configure(self, **kwargs):
        if "case" in kwargs:
            self.case = kwargs["case"]

    def bind_tools(self, tools):
        clone = _FakeModel(self.kind)
        clone.tools = tools
        clone.case = self.case
        return clone

    def invoke(self, messages):
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        if self.kind == "oracle":
            return self._oracle(tool_messages)
        if self.kind == "leaky":
            return self._leaky(tool_messages)
        return self._fake(messages, tool_messages)

    # ------------------------------------------------------------- scripted
    def _question(self, messages):
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return message.content or ""
        return ""

    def _fake(self, messages, tool_messages):
        question = self._question(messages)
        if not tool_messages:
            if "repeat" in question.lower():
                previous = next((m.content for m in reversed(messages)
                                 if isinstance(m, AIMessage) and m.content), "")
                return AIMessage(content=previous or "")
            return AIMessage(content="", tool_calls=[
                {"name": "search", "args": {"query": question}, "id": "call_search_1"}])
        try:
            payload = json.loads(tool_messages[-1].content)
        except (ValueError, TypeError):
            payload = {}
        parts = []
        for result in (payload.get("results") or [])[:2]:
            snippet = (result.get("snippet") or "").strip()
            if snippet:
                # echo the head of the snippet so the answer actually carries the
                # retrieved content (enough for the multi-turn revocation demo)
                parts.append(f"{snippet[:220]} [{result.get('doc_id')}]")
        return AIMessage(content=" ".join(parts))

    def _oracle(self, tool_messages):
        case = self.case or {}
        from guard import REFUSAL_EN
        if case.get("expect") == "deny":
            return AIMessage(content=REFUSAL_EN)
        gold = list(case.get("gold") or [])
        if not tool_messages:
            return AIMessage(content="", tool_calls=[
                {"name": "open_doc", "args": {"doc_id": g}, "id": f"oracle_{i}"}
                for i, g in enumerate(gold)])
        return AIMessage(content=" ".join(f"The document states the relevant facts. [{g}]" for g in gold))

    def _leaky(self, tool_messages):
        case = self.case or {}
        canary = case.get("canary", "")
        if not tool_messages:
            if case.get("cite"):        # a document guaranteed visible to the user
                return AIMessage(content="", tool_calls=[
                    {"name": "open_doc", "args": {"doc_id": case["cite"]}, "id": "leak_1"}])
            return AIMessage(content="", tool_calls=[
                {"name": "search", "args": {"query": case.get("question", "")}, "id": "leak_1"}])
        try:
            payload = json.loads(tool_messages[-1].content)
        except (ValueError, TypeError):
            payload = {}
        if "results" in payload:                                  # after a search
            results = payload.get("results") or []
            visible = results[0] if results else {}
            head = f"{_first_sentence(visible.get('snippet', ''))} [{visible.get('doc_id')}]"
        elif payload.get("doc_id"):                               # after open_doc
            head = f"The document states the relevant facts. [{payload['doc_id']}]"
        else:
            head = ""
        # the citation is legitimate; the canary is smuggled in as prose (the guard cannot see it)
        return AIMessage(content=f"{head} For completeness, the value is {canary}.")


def _build(model_name, base_url, api_key, temperature):
    if model_name in FAKE_MODELS:
        return _FakeModel(model_name)
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=model_name, base_url=base_url, api_key=api_key, temperature=temperature)


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
        self._impl = None
        self._db = None

    @property
    def is_fake(self):
        return self.model in FAKE_MODELS

    def _model(self):
        if self._impl is None:
            self._impl = _build(self.model, self.base_url, self.api_key, self.temperature)
        return self._impl

    def configure(self, **kwargs):
        """Configure the scripted fakes (oracle case / leaky canary)."""
        self._model().configure(**kwargs)

    # ---------------------------------------------------------------- caching
    def _cache(self):
        if self.cache_path is None:          # evaluation: no cache, so usage is accurate
            return None
        if self._db is None:
            directory = os.path.dirname(self.cache_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._db = sqlite3.connect(self.cache_path)
            self._db.execute("create table if not exists llm (k text primary key, v text)")
        return self._db

    # ------------------------------------------------------------------ invoke
    def invoke(self, messages, tools=None):
        """One completion; cached by sha256(model, messages, tools). Returns an AIMessage."""
        key = _sha(self.model, [_as_dict(m) for m in messages], tools, self.temperature)
        db = self._cache()
        if db is not None:
            row = db.execute("select v from llm where k=?", (key,)).fetchone()
            if row:
                self.cache_hits += 1
                return _ai_from_dict(json.loads(row[0]))
        model = self._model()
        if tools:
            model = model.bind_tools(tools)
        ai = model.invoke(messages)
        self._account(ai)
        if db is not None:
            db.execute("insert or replace into llm values (?, ?)",
                       (key, json.dumps(_ai_to_dict(ai), ensure_ascii=False)))
            db.commit()
        return ai

    def _account(self, ai):
        self.calls += 1
        usage = getattr(ai, "usage_metadata", None) or {}
        prompt = usage.get("input_tokens", 0) or 0
        completion = usage.get("output_tokens", 0) or 0
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        if self.price_in is not None and self.price_out is not None:
            self.cost += prompt / 1e6 * self.price_in + completion / 1e6 * self.price_out


def _ai_to_dict(ai):
    return {"content": ai.content,
            "tool_calls": [{"name": tc["name"], "args": tc["args"], "id": tc["id"]}
                           for tc in (getattr(ai, "tool_calls", None) or [])],
            "usage": getattr(ai, "usage_metadata", None)}


def _ai_from_dict(data):
    kwargs = {"content": data.get("content", "")}
    if data.get("tool_calls"):
        kwargs["tool_calls"] = data["tool_calls"]
    if data.get("usage"):
        kwargs["usage_metadata"] = data["usage"]
    return AIMessage(**kwargs)
