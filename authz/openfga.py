"""OpenFGA authorization backend (optional, verified).

A drop-in alternative to ``LocalAuthz`` that speaks to a running OpenFGA server
via ``openfga-sdk``: it reads ``authz/model.json`` (the compiled form of
``authz/model.fga``) and the same relationship tuples, then answers ``check`` /
``list_objects``. The consistency test (tests/test_stage_c_authz.py) asserts that
this backend and ``LocalAuthz`` agree for every user x document pair; it is
skipped unless the server has been bootstrapped.

Setup (see scripts/openfga_bootstrap.py and tools/openfga/):
    openfga run                                   # in-memory server on :8080
    python scripts/openfga_bootstrap.py           # create store + model + tuples
    set AUTHZ_BACKEND=openfga

The SDK is asynchronous; this adapter keeps one event loop and one client alive
and drives them synchronously so the rest of the code (tools, kb) stays sync.
"""
import asyncio
import json
import os

from . import state
from . import tuples as tuples_mod
from .core import Decision, Index, platform_viewer

TYPES = ("confluence_page", "jira_issue", "slack_thread", "gdrive_file")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDS_PATH = os.path.join(ROOT, ".cache", "openfga.json")


class OpenFGAAuthz:
    def __init__(self, api_url=None):
        self.api_url = api_url or os.environ.get("OPENFGA_API_URL") or "http://localhost:8080"
        ids = self._ids()
        if ids is None:
            raise RuntimeError("OpenFGA not bootstrapped; run scripts/openfga_bootstrap.py "
                               "(.cache/openfga.json is missing)")
        self.store_id = ids["store_id"]
        self.model_id = ids["model_id"]
        self.api_url = ids.get("api_url", self.api_url)
        self._loop = None
        self._client = None

    def _ids(self):
        if not os.path.exists(IDS_PATH):
            return None
        with open(IDS_PATH, encoding="utf-8") as f:
            return json.load(f)

    # ----------------------------------------------------------------- plumbing
    def _run(self, coro):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coro)

    async def _client_async(self):
        if self._client is None:
            from openfga_sdk import OpenFgaClient, ClientConfiguration
            self._client = OpenFgaClient(ClientConfiguration(
                api_url=self.api_url, store_id=self.store_id,
                authorization_model_id=self.model_id))
            await self._client.__aenter__()
        return self._client

    # ---------------------------------------------------------------- interface
    async def _check_async(self, user_id, doc):
        from openfga_sdk.client.models import ClientCheckRequest
        client = await self._client_async()
        obj = tuples_mod.object_id(doc)
        viewer = await client.check(ClientCheckRequest(
            user=f"user:{user_id}", relation="platform_viewer", object=obj))
        if not viewer.allowed:
            return Decision(False, "platform", f"platform ACL denies access to {obj}")
        can_read = await client.check(ClientCheckRequest(
            user=f"user:{user_id}", relation="can_read", object=obj))
        if not can_read.allowed:
            return Decision(False, "policy", "organisation policy denies access")
        return Decision(True, "platform", f"platform ACL grants access to {obj}")

    def check(self, user_id, doc_id):
        doc = state.documents().get(doc_id)
        if doc is None or user_id not in state.directory().get("users", {}):
            return Decision(False, "platform", "unknown subject or object")
        return self._run(self._check_async(user_id, doc))

    async def _list_async(self, user_id):
        from openfga_sdk.client.models import ClientListObjectsRequest
        client = await self._client_async()
        out = set()
        for kind in TYPES:
            resp = await client.list_objects(ClientListObjectsRequest(
                user=f"user:{user_id}", relation="can_read", type=kind))
            out.update(o.split(":", 1)[1] for o in (resp.objects or []))
        return out

    def list_readable(self, user_id):
        if user_id not in state.directory().get("users", {}):
            return set()
        return self._run(self._list_async(user_id))

    def platform_readers(self, doc_id):
        """OpenFGA has no reverse index for this, so evaluate the same tuples with
        the local reference evaluator (used only by the over-sharing scan)."""
        doc = state.documents().get(doc_id)
        if doc is None:
            return set()
        index = Index(tuples_mod.current())
        return {uid for uid in state.directory().get("users", {}) if platform_viewer(index, doc, uid)}

    async def _write_async(self, add, remove):
        from openfga_sdk.client.models import ClientTuple
        client = await self._client_async()
        if add:
            await client.write_tuples([ClientTuple(user=t["user"], relation=t["relation"], object=t["object"])
                                       for t in add])
        if remove:
            await client.delete_tuples([ClientTuple(user=t["user"], relation=t["relation"], object=t["object"])
                                        for t in remove])

    def write(self, tuples_list):
        self._run(self._write_async(list(tuples_list), None))

    def delete(self, tuples_list):
        self._run(self._write_async(None, list(tuples_list)))

    def invalidate(self):
        pass

    def close(self):
        if self._client is not None and self._loop is not None:
            self._loop.run_until_complete(self._client.__aexit__(None, None, None))
            self._client = None
