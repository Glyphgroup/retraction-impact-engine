"""Inspect the Cochrane OpenAPI spec: print schema shapes and endpoint details."""
import json
import re
import sys

SPEC = json.load(open("swagger.json", encoding="utf-8"))
SCHEMAS = SPEC["components"]["schemas"]


def type_of(v):
    if "$ref" in v:
        return v["$ref"].split("/")[-1]
    t = v.get("type", "?")
    if t == "array":
        return "array<%s>" % type_of(v["items"])
    fmt = v.get("format")
    return "%s(%s)" % (t, fmt) if fmt else t


MAX_DEPTH = 0  # 0 = shallow, no nested expansion


def show_schema(name, seen=None, depth=0):
    seen = seen if seen is not None else set()
    if name in seen or name not in SCHEMAS:
        return
    seen.add(name)
    pad = "  " * depth
    print("%s=== %s ===" % (pad, name))
    nested = []
    schema = SCHEMAS[name]
    props = dict(schema.get("properties") or {})
    for part in schema.get("allOf") or []:
        if "$ref" in part:
            print("%s  (inherits %s)" % (pad, part["$ref"].split("/")[-1]))
        props.update(part.get("properties") or {})
    for k, v in props.items():
        t = type_of(v)
        enum = v.get("enum")
        print("%s  %-28s %-24s %s" % (pad, k, t, "enum=" + ",".join(map(str, enum)) if enum else ""))
        base = t.replace("array<", "").rstrip(">")
        if base in SCHEMAS:
            nested.append(base)
    if depth < MAX_DEPTH:
        for n in nested:
            show_schema(n, seen, depth + 1)


def show_path(pattern):
    for p, ops in sorted(SPEC["paths"].items()):
        if not re.search(pattern, p):
            continue
        for method, op in ops.items():
            if method not in ("get", "post", "put", "delete"):
                continue
            print("--- %s %s" % (method.upper(), p))
            if op.get("summary"):
                print("    summary:", op["summary"])
            for prm in op.get("parameters", []):
                print("    param %s in=%s req=%s type=%s" % (
                    prm.get("name"), prm.get("in"), prm.get("required"),
                    type_of(prm.get("schema", {}))))
            if op.get("security") is not None:
                print("    security:", op["security"])
            for code, resp in (op.get("responses") or {}).items():
                content = resp.get("content") or {}
                for ct, body in content.items():
                    print("    %s %s -> %s" % (code, ct, type_of(body.get("schema", {}))))
                if not content:
                    print("    %s (no body)" % code)


if __name__ == "__main__":
    mode = sys.argv[1]
    for arg in sys.argv[2:]:
        if mode == "schema":
            show_schema(arg)
        else:
            show_path(arg)
