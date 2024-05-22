from collections.abc import Iterator

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

    def __len__(self):
        return len(self.mutations)