"""In-sandbox code-mode interpreter.

Trust model: this file IS the sandbox boundary for model-written code. The
parent process re-validates nothing this child emits beyond the line protocol
itself; all real enforcement lives here:

  * a strict allowlist AST validator (no imports, no dunders, no pow, ...),
  * a tiny hand-built builtins dict (no type/getattr/__import__/open, ...),
  * no import machinery available to user code at all,
  * subprocess isolation (the parent can kill us; we can only talk JSON lines
    over stdin/stdout, which are captured before user code runs).

User code sees exactly: SAFE_BUILTINS + a `tools` proxy. Nothing else.
"""

import json
import sys
import ast
import re

MAX_CODE_CHARS = 20000
_MAX_NODES = 6000
_MAX_DEPTH = 60
_MAX_INT_CONST = 10 ** 9
_MAX_REPEAT = 100000
_VALUE_REPR_CAP = 4000
_ERROR_CAP = 500
_MAX_PATTERN = 500
_MAX_TEXT = 1_000_000


class CodeRejected(ValueError):
    """User code was refused by the static validator."""


class ToolError(Exception):
    """A tool call failed (transport, limit, or tool-side error)."""


_ALLOWED_NODES = frozenset({
    "Module", "Expr", "Assign", "AugAssign", "AnnAssign", "If", "For",
    "While", "Break", "Continue", "Pass", "Return", "FunctionDef", "Lambda",
    "Try", "ExceptHandler", "Raise", "Assert",
    "ListComp", "SetComp", "DictComp", "GeneratorExp", "comprehension",
    "BoolOp", "BinOp", "UnaryOp", "Compare", "IfExp", "Call", "keyword",
    "Attribute", "Subscript", "Slice", "Starred",
    "Name", "Constant", "List", "Tuple", "Set", "Dict",
    "JoinedStr", "FormattedValue",
    "arguments", "arg", "Load", "Store",
    "And", "Or", "Not",
    "Add", "Sub", "Mult", "Div", "FloorDiv", "Mod",
    "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "In", "NotIn", "Is", "IsNot",
})

_BANNED_NAMES = frozenset({
    "eval", "exec", "compile", "open", "input", "getattr", "setattr",
    "delattr", "globals", "locals", "vars", "breakpoint", "memoryview",
    "super",
})

_CATCHABLE = frozenset({
    "Exception", "ValueError", "KeyError", "TypeError", "IndexError",
    "ZeroDivisionError", "AttributeError", "RuntimeError",
})

ALLOWED_ATTRS = frozenset(
    # str
    "strip lstrip rstrip split rsplit splitlines join lower upper title "
    "capitalize startswith endswith replace find rfind index count isdigit "
    "isalpha isalnum isspace isupper islower zfill ljust rjust center "
    "partition rpartition removeprefix removesuffix "
    # list
    "append extend insert pop remove index count sort reverse copy clear "
    # dict
    "get keys values items update setdefault pop popitem "
    # set
    "add discard union intersection difference issubset issuperset"
    .split()
)


def _reject(msg: str) -> "CodeRejected":
    return CodeRejected(msg)


def _check_name_str(name: str, what: str) -> None:
    if name == "tools" and what != "name":
        raise _reject("'tools' cannot be used as a " + what)
    if name.startswith("_"):
        raise _reject(f"{what} {name!r} must not start with '_'")
    if name in _BANNED_NAMES:
        raise _reject(f"name {name!r} is not allowed")


def _check_args(args: ast.arguments) -> None:
    if args.vararg is not None or args.kwarg is not None:
        raise _reject("*args/**kwargs are not allowed")
    for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        _check_name_str(a.arg, "parameter")


def _check(node: ast.AST, tool_names: frozenset, depth: int,
           counter: list, tools_ok: bool = False) -> None:
    counter[0] += 1
    if counter[0] > _MAX_NODES:
        raise _reject("code too complex (too many AST nodes)")
    if depth > _MAX_DEPTH:
        raise _reject("code too deeply nested")
    t = type(node).__name__
    if t not in _ALLOWED_NODES:
        raise _reject(f"{t} is not allowed")

    if isinstance(node, ast.Name):
        _check_name_str(node.id, "name")
        if node.id == "tools" and not tools_ok:
            if isinstance(node.ctx, ast.Store):
                raise _reject("'tools' cannot be assigned to")
            raise _reject(
                "'tools' may only be used as tools.<tool_name>(...)")
    elif isinstance(node, ast.Attribute):
        attr = node.attr
        if attr.startswith("_"):
            raise _reject("attribute names must not start with '_'")
        v = node.value
        if isinstance(v, ast.Name) and v.id == "tools":
            if attr not in tool_names:
                raise _reject(f"unknown tool: {attr}")
            _check(v, tool_names, depth + 1, counter, tools_ok=True)
            return
        if attr not in ALLOWED_ATTRS:
            raise _reject(f"attribute .{attr} is not allowed")
    elif isinstance(node, ast.arg):
        _check_name_str(node.arg, "parameter")
    elif isinstance(node, ast.FunctionDef):
        if node.decorator_list:
            raise _reject("decorators are not allowed")
        _check_name_str(node.name, "function name")
        if node.name == "tools":
            raise _reject("'tools' cannot be assigned to")
        _check_args(node.args)
    elif isinstance(node, ast.Lambda):
        _check_args(node.args)
    elif isinstance(node, ast.ExceptHandler):
        et = node.type
        if et is None:
            raise _reject("bare 'except:' is not allowed")
        if isinstance(et, ast.Name):
            if et.id not in _CATCHABLE:
                raise _reject(f"cannot catch {et.id}")
        elif isinstance(et, ast.Tuple):
            for elt in et.elts:
                if not (isinstance(elt, ast.Name) and elt.id in _CATCHABLE):
                    raise _reject("except tuple must name builtin exceptions")
        else:
            raise _reject("except clause must name builtin exceptions")
    elif isinstance(node, ast.Constant):
        v = node.value
        if v is None or isinstance(v, bool):
            pass
        elif isinstance(v, int):
            if abs(v) > _MAX_INT_CONST:
                raise _reject("integer constant too large")
        elif not isinstance(v, (float, str)):
            raise _reject(
                f"constant of type {type(v).__name__} is not allowed")
    elif isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Mult):
            for operand in (node.left, node.right):
                if (isinstance(operand, ast.Constant)
                        and isinstance(operand.value, (int, float))
                        and abs(operand.value) > _MAX_REPEAT):
                    raise _reject("constant too large in '*' expression")

    for child in ast.iter_child_nodes(node):
        _check(child, tool_names, depth + 1, counter, tools_ok=False)


def validate_code(code: str, tool_names: "set[str] | frozenset[str]") -> None:
    """Statically validate model-written code or raise CodeRejected."""
    if not isinstance(code, str):
        raise _reject("code must be a string")
    if len(code) > MAX_CODE_CHARS:
        raise _reject(f"code too long (>{MAX_CODE_CHARS} chars)")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        raise _reject(f"syntax error: {e.msg}") from None
    except (ValueError, RecursionError, MemoryError):
        raise _reject("code cannot be parsed") from None
    names = frozenset(tool_names)
    _check(tree, names, 0, [0])
    try:
        compile(tree, "<code>", "exec")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as e:
        raise _reject(f"code cannot be compiled: {type(e).__name__}") from None


# --------------------------------------------------------------------------
# child program
# --------------------------------------------------------------------------

def _make_safe_builtins():
    def json_loads(s):
        return json.loads(s)

    def json_dumps(o):
        return json.dumps(o, ensure_ascii=False, default=str)

    def re_findall(pattern, text):
        if not isinstance(pattern, str) or len(pattern) > _MAX_PATTERN:
            raise ValueError("invalid pattern")
        if not isinstance(text, str) or len(text) > _MAX_TEXT:
            raise ValueError("invalid text")
        return re.findall(pattern, text)

    return {
        "len": len, "range": range, "enumerate": enumerate, "zip": zip,
        "map": map, "filter": filter, "sorted": sorted, "reversed": reversed,
        "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
        "any": any, "all": all,
        "list": list, "dict": dict, "set": set, "tuple": tuple,
        "frozenset": frozenset, "str": str, "int": int, "float": float,
        "bool": bool, "repr": repr, "isinstance": isinstance,
        "Exception": Exception, "ValueError": ValueError,
        "KeyError": KeyError, "TypeError": TypeError,
        "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError,
        "AttributeError": AttributeError, "RuntimeError": RuntimeError,
        "ToolError": ToolError,
        "json_loads": json_loads, "json_dumps": json_dumps,
        "re_findall": re_findall,
    }


def main() -> None:
    stdin = sys.stdin
    stdout = sys.stdout

    def send(obj) -> None:
        stdout.write(json.dumps(obj) + "\n")
        stdout.flush()

    line = stdin.readline()
    if not line:
        sys.exit(3)
    try:
        req = json.loads(line)
    except ValueError:
        sys.exit(3)

    code = req["code"]
    tools_map = req["tools"]
    max_output_chars = int(req["max_output_chars"])
    max_calls = int(req["max_calls"])
    max_arg_chars = int(req["max_arg_chars"])

    try:
        validate_code(code, set(tools_map))
    except CodeRejected as e:
        send({"t": "done", "status": "rejected", "error": str(e),
              "output": "", "value": None})
        return

    safe_builtins = _make_safe_builtins()

    buffer: list = []
    truncated = False

    def user_print(*args, sep=" ", end="\n"):
        nonlocal truncated
        if truncated:
            return
        text = sep.join(str(a) for a in args) + end
        room = max_output_chars - sum(len(p) for p in buffer)
        if room <= 0:
            truncated = True
            return
        if len(text) > room:
            buffer.append(text[:room])
            truncated = True
        else:
            buffer.append(text)

    safe_builtins["print"] = user_print

    counter = 0

    class _ToolsProxy:
        __slots__ = ()

        def __getattr__(self, name):
            if name not in tools_map:
                raise AttributeError(f"unknown tool: {name}")
            api_name = tools_map[name]

            def call(*args, **kwargs):
                nonlocal counter
                if args:
                    raise TypeError("tools calls take keyword arguments only")
                counter += 1
                if counter > max_calls:
                    raise ToolError(f"too many tool calls (limit {max_calls})")
                try:
                    payload = json.dumps(kwargs)
                except (TypeError, ValueError):
                    raise ToolError(
                        "tool arguments must be JSON-serializable") from None
                if len(payload) > max_arg_chars:
                    raise ToolError("tool arguments too long")
                # `out` lets the parent keep what was printed so far if it has to
                # stop this process (owner approval needed, STOP, timeout).
                send({"t": "call", "id": counter, "tool": api_name,
                      "args": kwargs, "out": "".join(buffer),
                      "trunc": truncated})
                resp_line = stdin.readline()
                if not resp_line:
                    sys.exit(3)
                try:
                    resp = json.loads(resp_line)
                except ValueError:
                    sys.exit(3)
                if resp.get("t") == "ret":
                    return resp.get("result")
                if resp.get("t") == "err":
                    raise ToolError(str(resp.get("error")))
                sys.exit(3)

            return call

    proxy = _ToolsProxy()

    tree = ast.parse(code, mode="exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body[-1]
        tree.body[-1] = ast.Assign(
            targets=[ast.Name(id="_result", ctx=ast.Store())],
            value=last.value)
    ast.fix_missing_locations(tree)
    compiled = compile(tree, "<code>", "exec")

    g: dict = {"__builtins__": safe_builtins, "tools": proxy}
    sentinel = object()

    sys.setrecursionlimit(200)
    status = "ok"
    error_msg = None
    try:
        exec(compiled, g, g)  # noqa: S102 - deliberate, this IS the sandbox
    except Exception as e:  # includes RecursionError, MemoryError
        status = "error"
        error_msg = f"{type(e).__name__}: {e}"[:_ERROR_CAP]

    result = g.get("_result", sentinel)
    if result is sentinel:
        value = None
    else:
        try:
            dumped = json.dumps(result, allow_nan=False, default=str)
            if len(dumped) > 8000:
                value = dumped[:8000] + "...[value truncated]"
            else:
                value = json.loads(dumped)
        except (TypeError, ValueError):
            value = repr(result)[:_VALUE_REPR_CAP]

    done = {"t": "done", "status": status,
            "output": "".join(buffer), "truncated": truncated,
            "value": value}
    if status == "error":
        done["error"] = error_msg
    send(done)


if __name__ == "__main__":
    main()
