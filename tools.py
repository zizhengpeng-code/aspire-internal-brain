"""Tool layer.

``make_tools(session) -> (tool_schemas, call)``.

Every tool is a closure that binds ``session.user_id``; no tool accepts a user or a
role argument, so the model cannot act as anyone else. ``call(name, args)`` runs the
tool, writes the audit event (returned doc ids + the per-document allow/deny decisions)
and returns only the payload the model is allowed to see.

``search`` also performs a *shadow* retrieval over the whole corpus: the documents in
its top-k that the user may not see are recorded in the audit (doc id + decision) but
never returned to the model. This is a deliberate trade-off (see the README): it makes
the per-document allow/deny trail and the burst alert possible, at the cost of one
extra retrieval per query.
"""
import acl

SEARCH = {
    "type": "function",
    "function": {
        "name": "search",
        "description": ("Search the company knowledge base across Confluence, Jira, Slack and Google Drive. "
                        "Returns only documents the current user is allowed to see."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "natural-language search query"},
                "platforms": {"type": "array", "items": {"type": "string",
                              "enum": ["confluence", "jira", "slack", "gdrive"]},
                              "description": "optional platform filter"},
                "since": {"type": "string", "description": "optional ISO-8601 lower bound on the document update time"},
                "k": {"type": "integer", "description": "maximum number of documents to return (default 6)"},
            },
            "required": ["query"],
        },
    },
}

OPEN_DOC = {
    "type": "function",
    "function": {
        "name": "open_doc",
        "description": "Open one document in full by its id. Returns not_found when the document does not exist or is not visible to the user.",
        "parameters": {"type": "object", "properties": {"doc_id": {"type": "string"}}, "required": ["doc_id"]},
    },
}

FOLLOW_LINKS = {
    "type": "function",
    "function": {
        "name": "follow_links",
        "description": "List the documents linked from a document the user can open. Linked documents the user cannot see are omitted.",
        "parameters": {"type": "object", "properties": {"doc_id": {"type": "string"}}, "required": ["doc_id"]},
    },
}

QUERY_AUDIT = {
    "type": "function",
    "function": {
        "name": "query_audit",
        "description": ("Compliance only. Query the audit log: who accessed what, when, and the "
                        "per-document allow/deny decisions."),
        "parameters": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "filter by user id"},
                "doc": {"type": "string", "description": "filter by document id"},
                "location_prefix": {"type": "string", "description": "filter by location prefix, e.g. 'confluence:payment-gateway'"},
                "since": {"type": "string", "description": "ISO-8601 lower bound"},
                "until": {"type": "string", "description": "ISO-8601 upper bound"},
                "decision": {"type": "string", "enum": ["allow", "deny"]},
            },
        },
    },
}


def _decision(doc_id, doc, decision):
    return {"doc_id": doc_id, "location": acl.location(doc) if doc else None,
            "sensitivity": doc.get("sensitivity") if doc else None,
            "allow": decision.allow, "layer": decision.layer, "reason": decision.reason}


def make_tools(session):
    docs = session.docs

    def search(args):
        query = args.get("query", "")
        k = int(args.get("k") or 6)
        candidates = acl.visible_ids(session.user_id)
        platforms = set(args.get("platforms") or [])
        since = args.get("since")
        if platforms:
            candidates = {i for i in candidates if docs[i].get("source") in platforms}
        if since:
            candidates = {i for i in candidates if (docs[i].get("updated_at") or "") >= since}

        hits = session.kb.index.search(query, candidates)[:k]
        results, returned = [], []
        for hit in hits:
            if hit.doc in returned:
                continue
            returned.append(hit.doc)
            doc = docs[hit.doc]
            results.append({"doc_id": hit.doc, "chunk_id": hit.chunk, "platform": doc.get("source"),
                            "location": acl.location(doc), "title": doc["title"],
                            "updated_at": doc.get("updated_at"), "url": doc.get("url"),
                            "snippet": doc["text"][:320]})

        shadow = session.kb.index.search(query, None)[:k]
        decisions = []
        for doc_id in dict.fromkeys(h.doc for h in shadow):
            doc = docs.get(doc_id)
            if doc is None:
                continue
            decisions.append(_decision(doc_id, doc, acl.decide(session.user_id, doc)))
        return {"results": results}, {"returned": returned, "decisions": decisions}

    def open_doc(args):
        doc_id = args.get("doc_id")
        doc = docs.get(doc_id)
        if doc is None:
            decision = acl.Decision(False, "platform", "unknown document")
            return {"error": "not_found"}, {"returned": [], "decisions": [_decision(doc_id, None, decision)]}
        decision = acl.decide(session.user_id, doc)
        if not decision.allow:
            return {"error": "not_found"}, {"returned": [], "decisions": [_decision(doc_id, doc, decision)]}
        payload = {"doc_id": doc_id, "title": doc["title"], "platform": doc.get("source"),
                   "location": acl.location(doc), "updated_at": doc.get("updated_at"),
                   "url": doc.get("url"), "text": doc["text"], "links": doc.get("links", [])}
        return payload, {"returned": [doc_id], "decisions": [_decision(doc_id, doc, decision)]}

    def follow_links(args):
        doc_id = args.get("doc_id")
        doc = docs.get(doc_id)
        if doc is None or not acl.decide(session.user_id, doc).allow:
            decision = acl.Decision(False, "platform", "source document not visible")
            return {"error": "not_found"}, {"returned": [], "decisions": [_decision(doc_id, doc, decision)]}
        links, decisions, returned = [], [], []
        for link_id in doc.get("links", []):
            linked = docs.get(link_id)
            if linked is None:
                continue
            decision = acl.decide(session.user_id, linked)
            decisions.append(_decision(link_id, linked, decision))
            if decision.allow:
                returned.append(link_id)
                links.append({"doc_id": link_id, "title": linked["title"], "platform": linked.get("source"),
                              "location": acl.location(linked), "updated_at": linked.get("updated_at"),
                              "url": linked.get("url")})
        return {"doc_id": doc_id, "links": links}, {"returned": returned, "decisions": decisions}

    def query_audit(args):
        if "compliance_officer" not in session.groups:
            return {"error": "not_found"}, {"event": "audit_query", "returned": [], "decisions": []}
        records = session.audit.query(user=args.get("user"), doc=args.get("doc"),
                                      location_prefix=args.get("location_prefix"),
                                      since=args.get("since"), until=args.get("until"),
                                      decision=args.get("decision"))
        keys = ("seq", "ts", "event", "user_id", "question", "tool", "returned", "citations", "refused")
        trimmed = [{k: r[k] for k in keys if k in r} for r in records]
        return {"count": len(trimmed), "records": trimmed}, {"event": "audit_query", "returned": [], "decisions": []}

    functions = {"search": search, "open_doc": open_doc, "follow_links": follow_links, "query_audit": query_audit}
    schemas = [SEARCH, OPEN_DOC, FOLLOW_LINKS]
    if "compliance_officer" in session.groups:
        schemas.append(QUERY_AUDIT)

    def call(name, args):
        if name not in functions:
            raise KeyError(name)
        payload, meta = functions[name](args)
        event = {"event": meta.get("event", "tool_call"), "user_id": session.user_id,
                 "groups": session.groups, "ts": session.clock(), "tool": name, "args": args,
                 "returned": meta.get("returned", []), "decisions": meta.get("decisions", [])}
        session.audit.append(event)
        return payload

    return schemas, call
