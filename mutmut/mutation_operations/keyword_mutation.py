from .mutation_strategy import MutationStrategy

class KeywordMutation(MutationStrategy):
    def mutate(self, value, context, **_):
        if len(context.stack) > 2 and context.stack[-2].type in ('comp_op', 'sync_comp_for') and value in ('in', 'is'):
            return

        if len(context.stack) > 1 and context.stack[-2].type == 'for_stmt':
            return

        return {
            'not': '',
            'is': 'is not',  # this will cause "is not not" sometimes, so there's a hack to fix that later
            'in': 'not in',
            'break': 'continue',
            'continue': 'break',
            'True': 'False',
            'False': 'True',
        }.get(value)
