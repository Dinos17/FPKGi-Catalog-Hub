import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import catalog_pipeline


def write_catalogs(root, categories):
    (root / "config").mkdir()
    (root / "config" / "sources.json").write_text(
        json.dumps(categories), encoding="utf-8"
    )
    for filename in catalog_pipeline.expected_catalogs(root):
        catalog_pipeline.catalog_path(root, filename).parent.mkdir(parents=True, exist_ok=True)
        catalog_pipeline.catalog_path(root, filename).write_text(
            json.dumps({"DATA": {"https://example.com/a.pkg": {"name": filename}}}),
            encoding="utf-8",
        )


def test_prepare_and_apply_catalog_artifact_round_trip(tmp_path):
    root = tmp_path / "repo"
    artifact = tmp_path / "artifact"
    root.mkdir()

    write_catalogs(root, {"games": [], "apps": []})
    catalog_pipeline.prepare_catalog_artifact(root, artifact)

    assert sorted(
        p.relative_to(artifact).as_posix() for p in artifact.rglob("*.json")
    ) == sorted(catalog_pipeline.expected_catalogs(root))

    for path in (root / "ps4").glob("*.json"):
        path.write_text(json.dumps({"DATA": {}}), encoding="utf-8")

    catalog_pipeline.apply_catalog_artifact(root, artifact)

    for filename in catalog_pipeline.expected_catalogs(root):
        assert catalog_pipeline.catalog_path(root, filename).read_text(encoding="utf-8") == (
            artifact / filename
        ).read_text(encoding="utf-8")


def test_prepare_fails_when_expected_catalog_is_missing(tmp_path):
    root = tmp_path / "repo"
    artifact = tmp_path / "artifact"
    root.mkdir()
    write_catalogs(root, {"games": []})
    catalog_pipeline.catalog_path(root, "games.json").unlink()

    try:
        catalog_pipeline.prepare_catalog_artifact(root, artifact)
    except FileNotFoundError as exc:
        assert "games.json" in str(exc)
    else:
        raise AssertionError("Expected missing generated catalog to fail")


def test_apply_fails_when_expected_artifact_is_missing(tmp_path):
    root = tmp_path / "repo"
    artifact = tmp_path / "artifact"
    root.mkdir()
    write_catalogs(root, {"games": []})
    catalog_pipeline.prepare_catalog_artifact(root, artifact)
    catalog_pipeline.catalog_path(artifact, "ps5-games.json").unlink()

    try:
        catalog_pipeline.apply_catalog_artifact(root, artifact)
    except FileNotFoundError as exc:
        assert "ps5-games.json" in str(exc)
    else:
        raise AssertionError("Expected missing artifact catalog to fail")


def test_apply_removes_only_catalogs_recorded_in_manifest(tmp_path):
    root = tmp_path / "repo"
    artifact = tmp_path / "artifact"
    root.mkdir()

    write_catalogs(root, {"games": []})
    stale = catalog_pipeline.catalog_path(root, "legacy-category.json")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(json.dumps({"DATA": {"stale": {}}}), encoding="utf-8")
    unrelated = root / "project-data.json"
    unrelated.write_text(json.dumps({"keep": True}), encoding="utf-8")
    (root / "config" / "generated_catalogs.json").write_text(
        json.dumps(catalog_pipeline.expected_catalogs(root) + ["ps4/legacy-category.json"]),
        encoding="utf-8",
    )

    catalog_pipeline.prepare_catalog_artifact(root, artifact)
    removed = catalog_pipeline.apply_catalog_artifact(root, artifact)

    assert removed == ["ps4/legacy-category.json"]
    assert not stale.exists()
    assert unrelated.exists()
    assert json.loads((root / "config" / "generated_catalogs.json").read_text(encoding="utf-8")) == sorted(
        catalog_pipeline.expected_catalogs(root)
    )
