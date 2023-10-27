import os
import struct
import warnings
from copy import deepcopy
from functools import cached_property
from io import SEEK_END
from typing import Iterable, List, Optional

# Reserve for whatever changes in the future
RESERVED_SPACE = 1024
RESERVED_BYTES = struct.pack("<" + "x" * RESERVED_SPACE)


class IndexFile:
    def __init__(self, path: str):
        self.path = path

    def write(self, offsets: List[int]):
        with open(self.path, "wb") as io:
            n = len(offsets)
            io.write(struct.pack("<Q", n))
            for offset in offsets:
                io.write(struct.pack("<Q", offset))

    def __len__(self):
        if not os.path.isfile(self.path):
            return 0

        with open(self.path, "rb") as io:
            io.seek(0)
            (n,) = struct.unpack("<Q", io.read(8))
        return n

    def __getitem__(self, idx):
        assert idx < len(self)
        with open(self.path, "rb") as io:
            io.seek((idx + 1) * 8)
            (offset,) = struct.unpack("<Q", io.read(8))
        return offset

    def __repr__(self):
        n = len(self)
        return f"Index file with {n} items"

    def append(self, idx):
        n = len(self)
        mode = "wb" if n == 0 else "rb+"
        with open(self.path, mode) as io:
            # Increase length
            io.seek(0)
            io.write(struct.pack("<Q", n + 1))

            # Add index
            io.seek(0, SEEK_END)
            io.write(struct.pack("<Q", idx))

    def quick_remove_at(self, i):
        """Quickly remove an index by writing the index at the end to that index position.

        WARNING: This does not preserve the position of the index.

        Args:
            i (int): The index to be removed
        """
        n = len(self)
        with open(self.path, "rb+") as f:
            # Take the offset at the end of the file
            f.seek(-8, SEEK_END)
            buffer = f.read(8)
            f.seek(-8, SEEK_END)
            f.truncate()

            # Overwrite current offset
            # If i is not the last one
            # no need for swapping
            if i < n - 1:
                f.seek(8 * (i + 1))
                f.write(buffer)

            # Reduce length
            f.seek(0)
            f.write(struct.pack("<Q", n - 1))

    def __setitem__(self, i, v):
        with open(self.path, "rb+") as f:
            # Overwrite current offset
            f.seek(8 * (i + 1))
            f.write(struct.pack("<Q", v))

    def __iter__(self):
        return (self[i] for i in range(len(self)))


def make_dataset(
    record_iters: Iterable,
    output: str,
    serializers: List,
    index_path: Optional[str] = None,
):
    indices = []

    # Write record file
    with open(output, "wb") as io:
        io.write(RESERVED_BYTES)

        for items in record_iters:
            # serialize
            items_bin = [serialize(items[i]) for i, serialize in enumerate(serializers)]
            headers = [len(b) for b in items_bin]
            headers_bin = [struct.pack("<Q", h) for h in headers]

            # Track global offset, local offset (size)
            indices.append(io.tell())

            # Write
            for h in headers_bin:
                io.write(h)
            for d in items_bin:
                io.write(d)

    # Write indice files
    if index_path is None:
        index_path = os.path.splitext(output)[0] + ".idx"
    IndexFile(index_path).write(indices)
    return output, index_path


class IndexedRecordDataset:
    def __init__(
        self,
        path: str,
        deserializers: Optional[List] = None,
        serializers: Optional[List] = None,
        index_path: Optional[str] = None,
    ):
        if index_path is None:
            index_path = os.path.splitext(path)[0] + ".idx"
        self.path = path
        self.deserializers = deserializers
        self.serializers = serializers
        self.index = IndexFile(index_path)

    @cached_property
    def num_items(self):
        return len(self.deserializers)

    def quick_remove_at(self, i):
        self.index.quick_remove_at(i)

    def defrag(self, output_file):
        ref_data = deepcopy(self)
        ref_data.deserializers = [lambda x: x for _ in self.deserializers]
        serializers = [lambda x: x for _ in self.deserializers]

        def data_iter():
            for item in ref_data:
                yield item

        return make_dataset(data_iter(), output_file, serializers=serializers)

    def __iter__(self):
        return iter(self[i] for i in range(len(self)))

    def __len__(self):
        return len(self.index)

    def __getitem__(self, idx: int):
        msg = "You need de-serializers for reading the data"
        deserializers = self.deserializers
        assert deserializers is not None, msg

        # Inputs
        offset = self.index[idx]
        N = self.num_items

        # Deserialize
        with open(self.path, "rb") as io:
            io.seek(offset)
            lens = [struct.unpack("<Q", io.read(8))[0] for _ in range(N)]
            items = [deserializers[i](io.read(n)) for i, n in enumerate(lens)]

        return items

    def append(self, items):
        if not os.path.isfile(self.path) or len(self) == 0:
            with open(self.path, "wb") as io:
                io.write(RESERVED_BYTES)

        msg = "You need serializers for reading the data"
        assert self.serializers is not None, msg
        items_bin = [
            serialize(items[i]) for i, serialize in enumerate(self.serializers)
        ]
        headers = [len(b) for b in items_bin]
        headers_bin = [struct.pack("<Q", h) for h in headers]
        with open(self.path, "a+b") as io:
            io.seek(0, SEEK_END)
            idx = io.tell()
            self.index.append(idx)
            for b in headers_bin:
                io.write(b)
            for b in items_bin:
                io.write(b)


class EzRecordDataset(IndexedRecordDataset):
    def __post_init__(self):
        warnings.warning(
            "EzRecordDataset is deprecated due to name changes, use IndexedRecordDataset instead",
            DeprecationWarning,
        )
