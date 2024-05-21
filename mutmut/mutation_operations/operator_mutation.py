from .mutation_strategy import MutationStrategy
from mutmut.ast_pattern import ASTPattern

import_from_star_pattern = ASTPattern("""
from _name import *
#                 ^
""")

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
