"""Load authz/model.json + the relationship tuples into a running OpenFGA server.

    python scripts/openfga_bootstrap.py        # uses OPENFGA_API_URL or localhost:8080

Creates a store, writes the authorization model, uploads the same tuples that
LocalAuthz evaluates, and records the store/model ids in state/openfga.json for
the OpenFGAAuthz backend to reuse. The server binary lives in tools/openfga/
(git-ignored); start it with `openfga run` (in-memory storage is fine for a demo).
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from authz import state, tuples  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "authz", "model.json")


async def run(api_url):
    from openfga_sdk import OpenFgaClient, ClientConfiguration
    from openfga_sdk.client.models import ClientTuple

    async with OpenFgaClient(ClientConfiguration(api_url=api_url)) as client:
        store = await client.create_store({"name": "aspire-kb"})
        store_id = store.id if hasattr(store, "id") else store["id"]
        client.set_store_id(store_id)

        with open(MODEL, encoding="utf-8") as f:
            model = json.load(f)
        resp = await client.write_authorization_model(model)
        model_id = resp.authorization_model_id
        client.set_authorization_model_id(model_id)

        built = tuples.build(state.directory(), state.document_list(), policy=tuples.policy_enabled())
        for start in range(0, len(built), 100):
            batch = [ClientTuple(user=t["user"], relation=t["relation"], object=t["object"])
                     for t in built[start:start + 100]]
            await client.write_tuples(batch)

        os.makedirs(os.path.join(ROOT, ".cache"), exist_ok=True)
        with open(os.path.join(ROOT, ".cache", "openfga.json"), "w", encoding="utf-8") as f:
            json.dump({"store_id": store_id, "model_id": model_id,
                       "api_url": api_url, "tuples": len(built)}, f, indent=1)
        print(f"store={store_id} model={model_id} tuples={len(built)}")


def main():
    asyncio.run(run(os.environ.get("OPENFGA_API_URL", "http://localhost:8080")))


if __name__ == "__main__":
    main()
