def test_temp_db_path_is_fresh(temp_db_path):
    assert not temp_db_path.exists()
    assert temp_db_path.name == "agent_hub.db"
