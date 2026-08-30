from app.volume_title import arabic_volume_digits, cn_int


def test_cn_int_covers_textbook_volumes() -> None:
    assert cn_int("一") == 1
    assert cn_int("十") == 10
    assert cn_int("十一") == 11
    assert cn_int("二十") == 20
    assert cn_int("两") == 2


def test_arabic_volume_digits_rewrites_ce_hao() -> None:
    assert arabic_volume_digits("普通高中教科书·英语 必修 第一册") == "普通高中教科书·英语 必修 第1册"
    assert arabic_volume_digits("数学必修第三册") == "数学必修第3册"
    assert arabic_volume_digits("选择性必修 第十册") == "选择性必修 第10册"
    assert arabic_volume_digits("第1册") == "第1册"


def test_arabic_volume_digits_leaves_century_alone() -> None:
    assert arabic_volume_digits("二十一世纪英语") == "二十一世纪英语"
