"""Official BFCL AST checker, vendored. See NOTICE.

ast_checker.py is upstream byte-for-byte, so it still does `from bfcl_eval...import`.
Rather than patch it, register the four modules it wants under those names first.
MODEL_CONFIG_MAPPING is the only heavy one (it pulls every vendor SDK); it is consulted
only for `underscore_to_dot`, which is False for a prompt-mode model that sees the
function names verbatim.
"""
import sys
import types

from . import enums, type_mappings


class _NoDotConversion:
    underscore_to_dot = False


class _ModelConfigMapping(dict):
    def __missing__(self, key):
        return _NoDotConversion()


def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


if "bfcl_eval" not in sys.modules:
    _stub("bfcl_eval")
    _stub("bfcl_eval.constants")
    _stub("bfcl_eval.eval_checker")
    _stub("bfcl_eval.eval_checker.ast_eval")
    _stub("bfcl_eval.eval_checker.ast_eval.type_convertor")
    _stub("bfcl_eval.constants.enums", **vars(enums))
    _stub("bfcl_eval.constants.type_mappings", **vars(type_mappings))
    _stub("bfcl_eval.constants.model_config", MODEL_CONFIG_MAPPING=_ModelConfigMapping())
    # Java/JS converters are never reached on the Python categories this eval runs.
    for _n in ("java_type_converter", "js_type_converter"):
        _stub(f"bfcl_eval.eval_checker.ast_eval.type_convertor.{_n}",
              **{_n: lambda *a, **k: (_ for _ in ()).throw(NotImplementedError(_n))})

from .ast_checker import ast_checker                                    # noqa: E402
from .enums import Language, ReturnFormat                               # noqa: E402
from .prompting import (                                                # noqa: E402
    DEFAULT_SYSTEM_PROMPT_FORMAT,
    _func_doc_language_specific_pre_processing,
    default_decode_ast_prompting,
    formulate_system_prompt,
)

__all__ = ["ast_checker", "Language", "ReturnFormat", "DEFAULT_SYSTEM_PROMPT_FORMAT",
           "default_decode_ast_prompting", "formulate_system_prompt",
           "_func_doc_language_specific_pre_processing"]
