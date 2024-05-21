from .mutation_strategy import MutationStrategy

class DecoratorMutation(MutationStrategy):
    def mutate(self, children, **_):
        assert children[-1].type == 'newline'
        return children[-1:]
