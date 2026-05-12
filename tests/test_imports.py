"""Verify import path and that src modules can be imported."""


def test_import_auto_fixer():
    import auto_fixer

    assert hasattr(auto_fixer, "_run_self_review_phase")
    assert hasattr(auto_fixer, "_run_fix_phase")


def test_import_state_manager():
    import state_manager

    assert hasattr(state_manager, "load_state_comment")
    assert hasattr(state_manager, "append_refix_log_entry")


def test_import_prompt_builder():
    import prompt_builder

    assert hasattr(prompt_builder, "build_self_review_prompt")
    assert hasattr(prompt_builder, "build_fix_prompt")
    assert hasattr(prompt_builder, "parse_self_review_xml")
