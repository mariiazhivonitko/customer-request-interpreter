"""Quick terminal test, no UI. Use this first to check that LM Studio answers.

    python try_one.py 1          # request id from data/test_requests.json
    python try_one.py 1 --no-rules
"""
import json
import sys

import interpreter


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    rid = int(sys.argv[1])
    use_rules = "--no-rules" not in sys.argv
    req = next(r for r in interpreter.load_requests() if r["id"] == rid)
    print(f"Request: {req['sender']}: {req['text']}")
    print(f"Gold:    {json.dumps(req['gold'])}\n")
    result = interpreter.interpret(req, use_rules=use_rules)
    result.pop("prompt", None)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
