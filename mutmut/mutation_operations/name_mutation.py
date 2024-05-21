from .mutation_strategy import MutationStrategy
from mutmut import array_subscript_pattern, function_call_pattern

class NameMutation(MutationStrategy):
    def mutate(self, node, value, **_):
        simple_mutants = {
            'True': 'False',
            'False': 'True',
            'deepcopy': 'copy',
            'None': '""',
            'max': 'min',
            'min': 'max',
            'len': 'sum',
            'sum': 'len',
            'all': 'any',
            'any': 'all',
            'sorted': 'reversed',
            'reversed': 'sorted',
            'abs': 'len',
            'map': 'filter',
            'filter': 'map',
            'range': 'list',
            'list': 'tuple',
            'tuple': 'list',
            'dict': 'list',
            'set': 'list',
            'frozenset': 'set',
            'str': 'repr',
            'repr': 'str',
        }
        if value in simple_mutants:
            return simple_mutants[value]

        if array_subscript_pattern.matches(node=node):
            return 'None'

        if function_call_pattern.matches(node=node):
            return 'None'
