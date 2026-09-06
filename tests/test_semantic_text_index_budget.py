from __future__ import annotations

import numpy as np

from services.multimodal_retriever.vector_store import PersistentVectorStore
from services.multimodal_retriever.worker_wemm import ScoreItem
from services.multimodal_retriever.worker_wemm_hierarchical import HierarchicalWeMMRuntime


def test_online_text_cold_build_is_partial_but_preindex_can_complete(monkeypatch, tmp_path):
    source = tmp_path / "runtime.log"
    source.write_text(
        "\n".join(f"event={index} shield lifecycle state transition" for index in range(3000)),
        encoding="utf-8",
    )

    runtime = HierarchicalWeMMRuntime()
    runtime.vector_store = PersistentVectorStore(tmp_path / "wemm.sqlite3")
    runtime.text_chunk_chars = 1000
    runtime.text_overlap_chars = 0
    runtime.online_text_max_chunks = 3
    runtime.text_max_chunks = 8
    runtime.text_index_batch = 2

    def fake_encode(samples):
        rows = []
        for index, _sample in enumerate(samples):
            vector = np.array([1.0, float(index + 1)], dtype=np.float32)
            vector /= np.linalg.norm(vector)
            rows.append(vector)
        return np.vstack(rows)

    monkeypatch.setattr(runtime, "_encode", fake_encode)
    item = ScoreItem(
        key="log-1",
        path=str(source),
        mime="text/plain",
        name="runtime.log",
        modality="text_file",
    )
    source_fp = runtime._source_fingerprint(item)
    assert source_fp is not None

    partial = runtime._ensure_text_index(item, source_fp, full_build=False)
    assert 1 <= len(partial) <= runtime.online_text_max_chunks

    config = f"{runtime.text_chunk_chars}:{runtime.text_overlap_chars}:{runtime.text_max_chunks}"
    complete_key = f"complete:{runtime.backend_name}:text:{item.key}:{source_fp}:{config}"
    assert runtime.vector_store.get_meta(complete_key) is None

    complete = runtime._ensure_text_index(item, source_fp, full_build=True)
    assert len(complete) > len(partial)
    assert len(complete) <= runtime.text_max_chunks
    assert runtime.vector_store.get_meta(complete_key) == str(len(complete))
