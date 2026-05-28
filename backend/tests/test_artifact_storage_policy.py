from services.artifact_storage_policy import resolve_artifact_storage_policy


def test_test_reports_are_versioned_outputs():
    policy = resolve_artifact_storage_policy(
        "reports/tests/20260522T143122004981Z--task-45--backend-pytest.md"
    )

    assert policy.artifact_class == "Test"
    assert policy.storage_mode == "versioned-report"


def test_semantic_prd_documents_are_singleton_outputs():
    policy = resolve_artifact_storage_policy("docs/prd/project-browser-artifact-lifecycle.md")

    assert policy.artifact_class == "PRD"
    assert policy.storage_mode == "singleton-semantic"


def test_semantic_adr_documents_are_singleton_outputs():
    policy = resolve_artifact_storage_policy("docs/adr/ADR-024-artifact-naming-and-storage.md")

    assert policy.artifact_class == "ADR"
    assert policy.storage_mode == "singleton-semantic"
    assert policy.canonical_directories == ("docs/adr/",)


def test_release_documents_use_release_directory():
    policy = resolve_artifact_storage_policy("reports/releases/changelog.md")

    assert policy.artifact_class == "Release"
    assert policy.storage_mode == "singleton-semantic"
    assert policy.canonical_directories == ("reports/releases/",)


def test_non_artifacts_do_not_get_document_storage_policy():
    policy = resolve_artifact_storage_policy("src/app.py")

    assert policy.artifact_class is None
    assert policy.storage_mode == "non-artifact"
