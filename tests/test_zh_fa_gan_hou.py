import pytest

from tinglongwu.translate import zh_convert

CASES = [
    ("被发明", "被發明"),  # issue #240: was 被髮明
    ("被发现", "被發現"),
    ("被发射", "被發射"),
    ("被发布", "被發布"),
    ("头发", "頭髮"),
    ("理发", "理髮"),
    ("白发", "白髮"),
    ("头发很长", "頭髮很長"),
    ("发明家", "發明家"),
    ("发展", "發展"),
    ("干净", "乾淨"),
    ("饼干", "餅乾"),
    ("树干", "樹幹"),
    ("干部", "幹部"),
    ("以后", "以後"),
    ("后面", "後面"),
    ("后天", "後天"),
    ("皇后", "皇后"),
    ("王后", "王后"),
]


@pytest.mark.parametrize("simp,trad", CASES)
def test_zh_convert_fa_gan_hou(simp, trad):
    assert zh_convert(simp, "tw") == trad
