"""Lossless JSON numbers for canonical billing artifacts; no binary money math."""

import json
from decimal import Decimal


def dumps_exact(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite canonical decimal")
        return format(value, "f")
    if value is None or isinstance(value, (str, bool, int, float)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, list):
        return "[" + ",".join(dumps_exact(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=False) + ":" + dumps_exact(value[key])
                for key in sorted(value)
            )
            + "}"
        )
    raise TypeError("Unsupported canonical JSON type")


def loads_exact(text):
    def pairs(values):
        output = {}
        for key, value in values:
            if key in output:
                raise ValueError("Duplicate JSON key")
            output[key] = value
        return output

    def constant(_):
        raise ValueError("Non-finite JSON number")

    return json.loads(
        text, parse_float=Decimal, parse_constant=constant, object_pairs_hook=pairs
    )
