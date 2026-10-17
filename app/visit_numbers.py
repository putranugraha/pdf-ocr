"""Validation shared by OCR and new visit/prescription records."""

import re

VISIT_NUMBER_LENGTH = 19


def is_valid_visit_number(value: str) -> bool:
    return re.fullmatch(rf"[A-Z0-9]{{{VISIT_NUMBER_LENGTH}}}", value) is not None
