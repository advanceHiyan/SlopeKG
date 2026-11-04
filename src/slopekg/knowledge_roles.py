"""Bilingual wire labels for the existing role contract, without role inference."""
ALIASES={'现状':'observation','观测':'observation','实测':'observation',
         '设计':'design','建议':'recommendation','计算':'calculation',
         '预测':'prediction','模拟':'simulation','引用':'reference','未知':'unknown'}


def canonical_role(value):
    """Translate only unambiguous enum labels; unknown labels stay invalid."""
    return ALIASES.get(value.strip(),value.strip().casefold())
