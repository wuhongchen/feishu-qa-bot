#!/usr/bin/env python3
"""Legacy compatibility module.

This file is kept for backward compatibility and re-exports v5 APIs.
"""

from intent_classifier_v5 import (  # noqa: F401
    IntentResult,
    add_intent,
    classify_intent,
    get_all_intents,
    get_answer,
    load_intents,
    reload_intents,
    update_intent,
)


if __name__ == "__main__":
    tests = ["作业什么时候截止？", "你好", "谢谢"]
    for question in tests:
        result = classify_intent(question)
        if result:
            print(f"Q: {question} -> {result.intent_id} ({result.confidence:.2f})")
        else:
            print(f"Q: {question} -> no match")
