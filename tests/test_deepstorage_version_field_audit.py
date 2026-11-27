import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import deepstorage_version_field_audit as audit


def test_filename_version_is_extracted_from_final_bracket_tag():
    assert audit.filename_version("RESOGUN - [US] [EN] [1.10].pkg") == "1.10"
    assert audit.filename_version("No version.pkg") is None


def test_audit_has_exactly_the_22_blocked_sync_targets():
    assert len(audit.TARGETS) == 22
    assert len({url for _, url in audit.TARGETS}) == 22
    for name, url in audit.TARGETS:
        audit.parser.validate_source_url(url)
        assert name.endswith(".pkg")
