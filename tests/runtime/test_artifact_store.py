from worldforge.runtime.artifact_store import ReplayArtifactStore


class DummyArtifact:
    pass


def test_artifact_store_module_has_storage_contract():
    store = ReplayArtifactStore()
    assert store.load("missing") is None


def test_artifact_store_can_verify_unknown_id():
    store = ReplayArtifactStore()
    assert store.verify_digest("missing") is False
