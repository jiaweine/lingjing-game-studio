from worldforge.context import ContextCompiler


def _message(index: int, content: str):
    return {
        "id": f"m-{index}",
        "role": "user" if index % 2 == 0 else "assistant",
        "content": content,
        "payload": {},
    }


def test_fast_index_reuses_same_postings_object_on_append():
    compiler = ContextCompiler(recent_messages=4, retrieved_messages=3)
    history = [
        _message(index, f"研发记录 {index} build 1.4.{index % 7} shield pipeline")
        for index in range(200)
    ]
    history[17] = _message(17, "已确认 XR-914-ZETA 对应 build 1.4.7 render_deadlock")

    first = compiler.compile("XR-914-ZETA render_deadlock", history)
    cache_key = history[0]["id"]
    postings_before = compiler._retrieval_cache[cache_key][2]

    extended = [*history, _message(200, "继续排查 XR-914-ZETA")]
    second = compiler.compile("XR-914-ZETA render_deadlock", extended)
    postings_after = compiler._retrieval_cache[cache_key][2]

    assert first.mode.startswith("active-task-state-")
    assert second.mode.startswith("active-task-state-")
    assert "context-compiler-v" in second.mode
    assert second.retrieval_cache_hit is True
    assert postings_after is postings_before
    assert any("XR-914-ZETA" in row["content"] for row in second.messages)
