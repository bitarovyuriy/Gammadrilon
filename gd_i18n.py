"""Russian formatting for the retained 3.6.6 backends; no translation tables."""


def t(message: str, **values) -> str:
    return message.format(**values) if values else message
