# Lingjing Multimodal Retrieval Sidecar

This directory is an **optional, independently deployable** retrieval stack for the
ContextOS experiment. It is deliberately outside the main WorldForge runtime environment:
GPU embedding dependencies must not increase API worker memory, startup time, or failure
surface.

## Architecture

```text
WorldForge API
  |
  | POST /v1/rank
  v
CPU retrieval coordinator
  |-----------------------------|
  v                             v
WeMM visual/video worker        LCO acoustic worker
GPU 0                           GPU 1 (optional)
image + video                   audio + video audio
256-d Matryoshka                specialist path only
```

The main application remains fail-open. If the coordinator or either worker is absent,
slow, or unhealthy, deterministic ContextOS retrieval continues to work.

## Why two specialist workers

The current WeMM-Embedding release is particularly strong for text/image/video/visual-doc
retrieval and officially supports Matryoshka dimensions. Its published reference code uses
`SentenceTransformer.encode()` for text, images, and videos. It does **not** support audio.

LCO-Embedding-Omni supports audio as well as video through Qwen2.5-Omni. It is much heavier,
so the coordinator only calls it when the query contains an acoustic intent such as sound,
speech, voice, music, or audio. This keeps ordinary image/video turns on the lower-cost
WeMM path.

## 1. Start the WeMM worker

Use an isolated environment whose Torch build matches the host CUDA runtime.

```bash
python -m venv .venv-wemm
source .venv-wemm/bin/activate
pip install -r services/multimodal_retriever/requirements-wemm.txt

WEMM_MODEL=tencent/WeMM-Embedding-2B \
WEMM_DIMENSION=256 \
WEMM_DEVICE=cuda \
uvicorn services.multimodal_retriever.worker_wemm:app \
  --host 0.0.0.0 --port 8911 --workers 1
```

Important defaults:

- `WEMM_DIMENSION=256` keeps the ANN/vector-memory footprint small.
- `WEMM_ASSET_CACHE=8192` caches embeddings by asset id + file size + mtime.
- One process owns one GPU lane. Scale with separate replicas/GPU assignment instead of
  allowing uncontrolled concurrent `model.encode()` calls in one process.

## 2. Start the optional LCO audio worker

Use a second environment/GPU when acoustic retrieval matters.

```bash
python -m venv .venv-lco
source .venv-lco/bin/activate
pip install -r services/multimodal_retriever/requirements-lco.txt

CUDA_VISIBLE_DEVICES=1 \
LCO_MODEL=LCO-Embedding/LCO-Embedding-Omni-7B \
LCO_DEVICE=cuda \
uvicorn services.multimodal_retriever.worker_lco:app \
  --host 0.0.0.0 --port 8912 --workers 1
```

Long video acoustics are bounded by `LCO_VIDEO_MAX_FRAMES`, `LCO_VIDEO_FPS`, and
`LCO_VIDEO_MAX_PIXELS`. The worker is only queried on acoustic-intent turns.

## 3. Start the CPU coordinator

```bash
python -m venv .venv-mm-coordinator
source .venv-mm-coordinator/bin/activate
pip install -r services/multimodal_retriever/requirements-coordinator.txt

LINGJING_WEMM_WORKER_URL=http://127.0.0.1:8911 \
LINGJING_LCO_WORKER_URL=http://127.0.0.1:8912 \
uvicorn services.multimodal_retriever.app:app \
  --host 127.0.0.1 --port 8910 --workers 1
```

The coordinator performs parallel worker calls and score fusion. If both workers fail, it
still returns lexical/identifier ranking rather than failing the product request.

## 4. Point WorldForge at the coordinator

```bash
export LINGJING_MM_RETRIEVER_URL=http://127.0.0.1:8910
export LINGJING_MM_RETRIEVER_TIMEOUT_MS=1200
```

No environment variable means **zero network calls and zero GPU-sidecar dependency**.

## Worker protocol

Coordinator -> worker:

```json
{
  "query": "37 秒附近的技能音效是否重复触发",
  "items": [
    {
      "key": "asset-id",
      "path": "/shared/assets/run.mp4",
      "mime": "video/mp4",
      "modality": "video"
    }
  ]
}
```

Worker -> coordinator:

```json
{
  "backend": "wemm:tencent/WeMM-Embedding-2B:256d",
  "scores": [
    {
      "key": "asset-id",
      "score": 0.83,
      "modality": "video",
      "evidence_ref": "asset:asset-id"
    }
  ]
}
```

The protocol is intentionally small so future WorldMM/M3-Agent/segment-level retrievers can
replace either worker without changing the product API.

## Production notes

- Put API and workers on a shared read-only media volume, or replace the path field with an
  internal object-store reference adapter.
- Do not expose workers directly to the public internet. They accept trusted materialized
  media paths.
- Keep the coordinator timeout below the WorldForge sidecar timeout so it can return partial
  results rather than being cancelled by the caller.
- Embedding caches are **derived state**. They may be deleted at any time; raw assets remain
  authoritative.
- For high throughput, batch ingestion/indexing asynchronously and use the online worker for
  cache misses and newly uploaded assets. The current reference implementation keeps an LRU
  cache in memory to validate retrieval quality before adding a persistent ANN layer.
