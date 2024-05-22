from collections.abc import Iterator

class MutationCollection:
    def __init__(self, mutations):
        self.mutations = mutations

    def get_iterator(self):
        return MutationIterator(self.mutations)


class MutationIterator(Iterator):
    def __init__(self, mutations):
        self.mutations = mutations
        self.index = 0

    def __next__(self):
        if self.index >= len(self.mutations):
            raise StopIteration
        mutation = self.mutations[self.index]
        self.index += 1
        return mutation

    def prev(self):
        if self.index <= 0:
            raise Exception("Already at the first element")
        self.index -= 1
        return self.mutations[self.index]

    def current(self):
        if self.index >= len(self.mutations):
            raise Exception("Iterator out of bounds")
        return self.mutations[self.index]

    def has_next(self):
        return self.index < len(self.mutations)