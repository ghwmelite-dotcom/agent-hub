from agent_hub.worktree_manager import branch_slug


def test_branch_slug_simple_title():
    assert branch_slug(42, "add health endpoint") == "task/42-add-health-endpoint"


def test_branch_slug_lowercases():
    assert branch_slug(7, "Add Login") == "task/7-add-login"


def test_branch_slug_strips_special_chars():
    assert branch_slug(1, "Fix bug: @user/path!") == "task/1-fix-bug-user-path"


def test_branch_slug_collapses_whitespace():
    assert branch_slug(1, "  many   spaces  ") == "task/1-many-spaces"


def test_branch_slug_truncates_long_titles():
    long_title = "a" * 200
    slug = branch_slug(99, long_title)
    parts = slug.split("/", 1)
    assert parts[0] == "task"
    rest = parts[1]
    title_portion = rest.split("-", 1)[1]
    assert len(title_portion) <= 60


def test_branch_slug_unicode_falls_back_to_id():
    assert branch_slug(5, "🎉🎊") == "task/5"
    assert branch_slug(6, "") == "task/6"
