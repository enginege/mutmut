from .mutation_strategy import MutationStrategy
from parso.python.tree import FStringStart, FStringEnd

class FStringMutation(MutationStrategy):
    def mutate(self, children, **_):
        fstring_start: FStringStart = children[0]
        fstring_end: FStringEnd = children[-1]

        children = children[:]  # we need to copy the list here, to not get in place mutation on the next line!

        children[0] = FStringStart(fstring_start.value + 'XX',
                                   start_pos=fstring_start.start_pos,
                                   prefix=fstring_start.prefix)

        children[-1] = FStringEnd('XX' + fstring_end.value,
                                  start_pos=fstring_end.start_pos,
                                  prefix=fstring_end.prefix)

        return children