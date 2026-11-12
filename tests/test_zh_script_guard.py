from tinglongwu.translate import zh_garbage, zh_guard


def test_and_life_mixed_script_output_falls_back_to_source():
    assert zh_garbage("臨Τネ")
    assert zh_guard("And life", "臨Τネ") == "And life"


def test_normal_zh_unaffected():
    assert not zh_garbage("生活就是這樣。")
    assert zh_guard("And life", "和生活") == "和生活"
