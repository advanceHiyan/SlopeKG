"""Stable short names: 基读 Basic, 知联 Knowledge, 视读 Visual.

Legacy wire identifiers remain accepted; stored runs and model versions are not
renamed. Display names never become model instructions or cache identities.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RecognitionRoute:
    id: str
    chinese: str
    english: str
    aliases: tuple[str, ...] = ()

    @property
    def label(self):
        return f'{self.chinese} {self.english}'

    def payload(self):
        return {'id': self.id, 'chinese': self.chinese, 'english': self.english, 'label': self.label}


ROUTES = (
    RecognitionRoute('basic', '基读', 'Basic', ('legacy',)),
    RecognitionRoute('knowledge', '知联', 'Knowledge'),
    RecognitionRoute('visual', '视读', 'Visual', ('multimodal',)),
    RecognitionRoute('document', '整读', 'Document'),
    RecognitionRoute('agent', '自主', 'Agent'),
)
RECOGNIZER_CHOICES = tuple(name for route in ROUTES for name in (route.id, *route.aliases))


def recognition_route(value='basic'):
    for route in ROUTES:
        if value == route.id or value in route.aliases:
            return route
    raise ValueError('请选择基读 Basic、知联 Knowledge、视读 Visual、整读 Document 或自主 Agent')


def normalize_recognizer(value='basic'):
    return recognition_route(value).id
