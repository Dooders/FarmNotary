from pathlib import Path


def test_action_yml_contract():
    text = Path("action.yml").read_text(encoding="utf-8")
    assert "name: farm-notary-action" in text
    assert "phase:" in text
    assert "run-dir:" in text
    assert "pin-remote:" in text
    assert "profile:" in text
    assert "identity-key:" in text
    assert "--profile" in text
    assert "default: ots" in text
    assert "default: notarize" in text
    assert "farm-notary verify" in text
    assert "actions/upload-artifact@v4" in text
    assert "manifest.json" in text
    assert "manifest.ots" in text
    assert "precommit" in text
    # Verify must fail the job: the notarize step has no continue-on-error.
    notarize_block = text.split("id: notarize", 1)[1].split("- name:", 1)[0]
    assert "continue-on-error" not in notarize_block
    assert "farm-notary verify --run-dir" in notarize_block
