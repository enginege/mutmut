from .mutation_strategy import MutationStrategy
from mutmut import import_from_star_pattern

class OperatorMutation(MutationStrategy):
    def mutate(self, value, node, **_):
        if import_from_star_pattern.matches(node=node):
            return

        if value in ('*', '**') and node.parent.type == 'param':
            return

        if value == '*' and node.parent.type == 'parameters':
            return

        if value in ('*', '**') and node.parent.type in ('argument', 'arglist'):
            return

        return {
            '+': '-',
            '-': '+',
            '*': '/',
            '/': '*',
            '//': '/',
            '%': '/',
            '<<': '>>',
            '>>': '<<',
            '&': '|',
            '|': '&',
            '^': '&',
            '**': '*',
            '~': '',

            '+=': ['-=', '='],
            '-=': ['+=', '='],
            '*=': ['/=', '='],
            '/=': ['*=', '='],
            '//=': ['/=', '='],
            '%=': ['/=', '='],
            '<<=': ['>>=', '='],
            '>>=': ['<<=', '='],
            '&=': ['|=', '='],
            '|=': ['&=', '='],
            '^=': ['&=', '='],
            '**=': ['*=', '='],
            '~=': '=',

            '<': '<=',
            '<=': '<',
            '>': '>=',
            '>=': '>',
            '==': '!=',
            '!=': '==',
            '<>': '==',
        }.get(value)
