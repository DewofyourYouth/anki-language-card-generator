"""Config loading."""

import pytest

from anki_cardgen.config import Config


def test_loads_the_shipped_config_yaml():
    config = Config.load("config.yaml")
    assert config.language == "shami"
    assert config.deck_name


def test_expands_home_in_registry_path():
    config = Config.from_dict(
        {"language": "shami", "deck_name": "D", "registry_db_path": "~/x/registry.db"}
    )
    assert "~" not in str(config.registry_db_path)
    assert config.registry_db_path.is_absolute()


def test_registry_path_defaults():
    config = Config.from_dict({"language": "shami", "deck_name": "D"})
    assert config.registry_db_path.name == "registry.db"


def test_missing_required_keys_raise():
    with pytest.raises(ValueError, match="deck_name"):
        Config.from_dict({"language": "shami"})


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Config.load(tmp_path / "nope.yaml")


def test_non_mapping_yaml_raises(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Config.load(path)
