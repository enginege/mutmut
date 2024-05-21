from .mutation_strategy import MutationStrategy
from parso.python.tree import Keyword

class AndOrTestMutation(MutationStrategy):
    def mutate(self, children, node, **_):
        children = children[:]
        children[1] = Keyword(
            value={'and': ' or', 'or': ' and'}[children[1].value],
            start_pos=node.start_pos,
        )
        return children
