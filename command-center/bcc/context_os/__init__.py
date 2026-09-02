"""Context OS — единый internal API для сборки контекста.

Ни один агент сам не собирает себе контекст. Все идут через Context.request().

F-018 disposition: DEPRECATED_NON_PROTECTIVE. Каноничный context engine —
`bossman-core/bossman/context_engine`; у bcc TaskEngine нет хука before_call,
и `integration.attach_to_engine` никогда не регистрировал свой хук (dead by
bug). Пакет НЕ участвует ни в одном security-контроле: ничего не фильтрует,
не редактирует и не запрещает. Компилятор/стор/state machine остаются как
библиотечные примитивы под tests/test_context_os.py; `attach_to_engine`
теперь честно бросает NotImplementedError.
"""
from .compiler import CompiledContext, ContextCompiler
from .hierarchical import ContextLayer, HierarchicalContextManager, TokenBudgeter
from .state import STATE_ORDER, StateMachine
from .stores import DecisionStore, FailureStore

__all__ = [
    "CompiledContext",
    "ContextCompiler",
    "ContextLayer",
    "DecisionStore",
    "FailureStore",
    "HierarchicalContextManager",
    "StateMachine",
    "STATE_ORDER",
    "TokenBudgeter",
]
