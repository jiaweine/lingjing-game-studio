from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.benchmarks.multimodal_product_export import (
    attach_source_inventory,
    stage_product_assets,
)
from worldforge.benchmarks.multimodal_workspace import new_workspace
from worldforge.product.store import ConversationStore, DEMO_WORKSPACE_ID
from worldforge.storage import LocalObjectStorage


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lingjing-mm-product-export-") as tmp:
        root = Path(tmp)
        store = ConversationStore(root / "product.db", root / "legacy-assets", seed_dev_identity=True)
        conversation = store.create_conversation(
            "staging smoke",
            workspace_id=DEMO_WORKSPACE_ID,
        )
        storage = LocalObjectStorage(root / "objects")
        data = b"build=smoke-1\nframe hitch at 37s\n"
        key = f"{DEMO_WORKSPACE_ID}/assets/smoke/trace.log"
        storage.put_bytes(key, data, "text/plain")
        source = store.add_asset(
            conversation["id"],
            name="trace.log",
            mime="text/plain",
            path=key,
            size=len(data),
            meta={"kind": "text", "build_ref": "smoke-1"},
            workspace_id=DEMO_WORKSPACE_ID,
            storage_backend="local",
        )
        corpus = root / "corpus"
        inventory, export_report = stage_product_assets(
            store,
            storage,
            corpus_root=corpus,
            workspace_id=DEMO_WORKSPACE_ID,
            conversation_ids=[conversation["id"]],
        )
        workspace = attach_source_inventory(new_workspace(corpus), corpus_root=corpus)
        asset = workspace["asset_catalog"][0]
        provenance = asset["meta"]["source_provenance"]
        assert inventory["annotation_labels_emitted"] is False
        assert workspace["cases"] == []
        assert provenance["source_asset_id"] == source["id"]
        assert asset["meta"]["build_ref"] == "smoke-1"
        assert "_context" not in asset["meta"]
        print(
            json.dumps(
                {
                    "status": "ok",
                    "staged_assets": export_report["staged_assets"],
                    "source_inventory_digest": inventory["source_inventory_digest"],
                    "annotation_labels_emitted": False,
                    "evidence_claim": "synthetic-staging-protocol-smoke-not-quality-evidence",
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
