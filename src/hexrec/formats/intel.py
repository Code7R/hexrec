# -*- coding: utf-8 -*-
import enum
import re
import struct
from typing import ByteString
from typing import Iterator
from typing import Optional
from typing import Sequence
from typing import Union

from ..records import Record as _Record
from ..records import RecordSeq
from ..records import Tag as _Tag
from ..utils import chop
from ..utils import hexlify
from ..utils import sum_bytes
from ..utils import unhexlify


@enum.unique
class Tag(_Tag):
    """Intel HEX tag."""

    DATA = 0
    """Binary data."""

    END_OF_FILE = 1
    """End of file."""

    EXTENDED_SEGMENT_ADDRESS = 2
    """Extended segment address."""

    START_SEGMENT_ADDRESS = 3
    """Start segment address."""

    EXTENDED_LINEAR_ADDRESS = 4
    """Extended linear address."""

    START_LINEAR_ADDRESS = 5
    """Start linear address."""

    @classmethod
    def is_data(cls, value: Union[int, 'IntegTag']) -> bool:
        r""":obj:`bool`: `value` is a data record tag."""
        return value == cls.DATA


class Record(_Record):
    r"""Intel HEX record.

    See:
        `<https://en.wikipedia.org/wiki/Intel_HEX>`_
    """

    TAG_TYPE = Tag
    """Associated Python class for tags."""

    REGEX = re.compile(r'^:(?P<count>[0-9A-Fa-f]{2})'
                       r'(?P<offset>[0-9A-Fa-f]{4})'
                       r'(?P<tag>[0-9A-Fa-f]{2})'
                       r'(?P<data>([0-9A-Fa-f]{2}){,255})'
                       r'(?P<checksum>[0-9A-Fa-f]{2})$')
    """Regular expression for parsing a record text line."""

    EXTENSIONS = ('.hex', '.ihex', '.mcs')
    """Automatically supported file extensions."""

    def __init__(self, address: int,
                 tag: 'Tag',
                 data: ByteString,
                 checksum: Union[int, type(Ellipsis)] = Ellipsis) -> None:

        if not 0 <= address < (1 << 32):
            raise ValueError('address overflow')
        if not 0 <= address + len(data) <= (1 << 32):
            raise ValueError('size overflow')
        super().__init__(address, self.TAG_TYPE(tag), data, checksum)

    def __str__(self) -> str:
        self.check()
        data = self.data or b''
        text = (f':{len(data):02X}'
                f'{((self.address or 0) & 0xFFFF):04X}'
                f'{self.tag:02X}'
                f'{hexlify(data)}'
                f'{self._get_checksum():02X}')
        return text

    def compute_count(self) -> int:
        return len(self.data)

    def compute_checksum(self) -> int:
        offset = (self.address or 0) & 0xFFFF

        checksum = (self.count +
                    sum_bytes(struct.pack('H', offset)) +
                    self.tag +
                    sum_bytes(self.data))

        checksum = (0x100 - int(checksum & 0xFF)) & 0xFF
        return checksum

    def check(self) -> None:
        super().check()

        if self.count != self.compute_count():
            raise ValueError('count error')

        self.TAG_TYPE(self.tag)
        # TODO: check values

    @classmethod
    def build_data(cls, address: int, data: ByteString) -> 'Record':
        r"""Builds a data record.

        Arguments:
            address (:obj:`int`): Record start address.
            data (:obj:`bytes`): Some program data.

        Returns:
            :obj:`Record`: Data record.

        Example:
            >>> str(Record.build_data(0x1234, b'Hello, World!'))
            ':0D12340048656C6C6F2C20576F726C642144'
        """
        record = cls(address, cls.TAG_TYPE.DATA, data)
        return record

    @classmethod
    def build_extended_segment_address(cls, address: int) -> 'Record':
        r"""Builds an extended segment address record.

        Arguments:
            address (:obj:`int`): Extended segment address.
                The 20 least significant bits are ignored.

        Returns:
            :obj:`Record`: Extended segment address record.

        Example:
            >>> str(Record.build_extended_segment_address(0x12345678))
            ':020000020123D8'
        """
        if not 0 <= address < (1 << 32):
            raise ValueError('address overflow')
        segment = address >> (16 + 4)
        tag = cls.TAG_TYPE.EXTENDED_SEGMENT_ADDRESS
        record = cls(0, tag, struct.pack('>H', segment))
        return record

    @classmethod
    def build_start_segment_address(cls, address: int) -> 'Record':
        r"""Builds an start segment address record.

        Arguments:
            address (:obj:`int`): Start segment address.

        Returns:
            :obj:`Record`: Start segment address record.

        Raises:
            :obj:`ValueError` Address overflow.

        Example:
            >>> str(Record.build_start_segment_address(0x12345678))
            ':0400000312345678E5'
        """
        if not 0 <= address < (1 << 32):
            raise ValueError('address overflow')

        tag = cls.TAG_TYPE.START_SEGMENT_ADDRESS
        record = cls(0, tag, struct.pack('>L', address))
        return record

    @classmethod
    def build_end_of_file(cls) -> 'Record':
        r"""Builds an end-of-file record.

        Returns:
            :obj:`Record`: End-of-file record.

        Example:
            >>> str(Record.build_end_of_file())
            ':00000001FF'
        """
        tag = cls.TAG_TYPE.END_OF_FILE
        return cls(0, tag, b'')

    @classmethod
    def build_extended_linear_address(cls, address: int) -> 'Record':
        r"""Builds an extended linear address record.

        Arguments:
            address (:obj:`int`): Extended linear address.
            The 16 least significant bits are ignored.

        Returns:
            :obj:`Record`: Extended linear address record.

        Raises:
            :obj:`ValueError` Address overflow.

        Example:
            >>> str(Record.build_extended_linear_address(0x12345678))
            ':020000041234B4'
        """
        if not 0 <= address < (1 << 32):
            raise ValueError('address overflow')

        segment = address >> 16
        tag = cls.TAG_TYPE.EXTENDED_LINEAR_ADDRESS
        record = cls(0, tag, struct.pack('>H', segment))
        return record

    @classmethod
    def build_start_linear_address(cls, address: int) -> 'Record':
        r"""Builds an start linear address record.

        Arguments:
            address (:obj:`int`): Start linear address.

        Returns:
            :obj:`Record`: Start linear address record.

        Raises:
            :obj:`ValueError` Address overflow.

        Example:
            >>> str(Record.build_start_linear_address(0x12345678))
            ':0400000512345678E3'
        """
        if not 0 <= address < (1 << 32):
            raise ValueError('address overflow')

        tag = cls.TAG_TYPE.START_LINEAR_ADDRESS
        record = cls(0, tag, struct.pack('>L', address))
        return record

    @classmethod
    def parse_record(cls, line: str) -> 'Record':
        line = str(line).strip()
        match = cls.REGEX.match(line)
        if not match:
            raise ValueError('regex error')
        groups = match.groupdict()

        offset = int(groups['offset'], 16)
        tag = cls.TAG_TYPE(int(groups['tag'], 16))
        count = int(groups['count'], 16)
        data = unhexlify(groups['data'] or '')
        checksum = int(groups['checksum'], 16)

        if count != len(data):
            raise ValueError('count error')
        record = cls(offset, tag, data, checksum)
        return record

    @classmethod
    def split(cls, data: ByteString,
              address: int = 0,
              columns: int = 16,
              align: bool = True,
              standalone: bool = True,
              start: Optional[int] = None) -> Iterator['Record']:
        r"""Splits a chunk of data into records.

        Arguments:
            data (:obj:`bytes`): Byte data to split.
            address (:obj:`int`): Start address of the first data record being
                split.
            columns (:obj:`int`): Maximum number of columns per data record.
                If ``None``, the whole `data` is put into a single record.
                Maximum of 255 columns.
            align (:obj:`bool`): Aligns record addresses to the column length.
            standalone (:obj:`bool`): Generates a sequence of records that can
                be saved as a standlone record file.
            start (:obj:`int`): Program start address.
                If ``None``, it is assigned the minimum data record address.

        Yields:
            :obj:`Record`: Data split into records.

        Raises:
            :obj:`ValueError` Address, size, or column overflow.
        """
        if not 0 <= address < (1 << 32):
            raise ValueError('address overflow')
        if not 0 <= address + len(data) <= (1 << 32):
            raise ValueError('size overflow')
        if not 0 < columns < 255:
            raise ValueError('column overflow')

        if start is None:
            start = address
        align_base = (address % columns) if align else 0
        address_old = 0

        for chunk in chop(data, columns, align_base):
            length = len(chunk)
            endex = address + length
            overflow = endex & 0xFFFF

            if overflow and (address ^ endex) & 0xFFFF0000:
                pivot = length - overflow

                yield cls.build_data(address, chunk[:pivot])
                address += pivot

                yield cls.build_extended_linear_address(address)

                yield cls.build_data(address, chunk[pivot:])
                address_old = address
                address += overflow

            else:
                if (address ^ address_old) & 0xFFFF0000:
                    yield cls.build_extended_linear_address(address)

                yield cls.build_data(address, chunk)
                address_old = address
                address += length

        if standalone:
            for record in cls.terminate(start):
                yield record

    @classmethod
    def build_standalone(cls, data_records: RecordSeq,
                         start: Optional[int] = None) \
            -> Iterator['Record']:
        r"""Makes a sequence of data records standalone.

        Arguments:
            data_records (:obj:`list` of :class:`Record`): A sequence of data
                records.
            start (:obj:`int`): Program start address.
                If ``None``, it is assigned the minimum data record address.

        Yields:
            :obj:`Record`: Records for a standalone record file.
        """
        for record in data_records:
            yield record

        if start is None:
            if not data_records:
                data_records = [cls.build_data(0, b'')]
            start = min(record.address for record in data_records)

        for record in cls.terminate(start):
            yield record

    @classmethod
    def terminate(cls, start: int) -> Sequence['Record']:
        r"""Builds a record termination sequence.

        The termination sequence is made of:

        # An extended linear address record at ``0``.
        # A start linear address record at `start`.
        # An end-of-file record.

        Arguments:
            start (:obj:`int`): Program start address.

        Returns:
            :obj:`list` of :obj:`Record`: Termination sequence.

        Example:
            >>> list(map(str, Record.terminate(0x12345678)))
            [':020000040000FA', ':0400000512345678E3', ':00000001FF']
        """
        return [cls.build_extended_linear_address(0),
                cls.build_start_linear_address(start),
                cls.build_end_of_file()]

    @classmethod
    def readdress(cls, records: RecordSeq) -> Sequence['Record']:
        r"""Converts to flat addressing.

        *Intel HEX*, stores records by *segment/offset* addressing.
        As this library adopts *flat* addressing instead, all the record
        addresses should be converted to *flat* addressing after loading.
        This procedure readdresses a sequence of records in-place.

        Warning:
            Only the `address` field is modified. All the other fields hold
            their previous value.

        Arguments:
            records (list): Sequence of records to be converted to *flat*
                addressing, in-place. Sequence generators supported.

        Example:
            >>> records = [
            ...     Record.build_extended_linear_address(0x76540000),
            ...     Record.build_data(0x00003210, b'Hello, World!'),
            ... ]
            >>> records  #doctest: +NORMALIZE_WHITESPACE
            [Record(address=0x00000000,
                         tag=<Tag.EXTENDED_LINEAR_ADDRESS: 4>, count=2,
                         data=b'vT', checksum=0x30),
             Record(address=0x00003210, tag=<Tag.DATA: 0>, count=13,
                         data=b'Hello, World!', checksum=0x48)]
            >>> Record.readdress(records)
            >>> records  #doctest: +NORMALIZE_WHITESPACE
            [Record(address=0x76540000,
                         tag=<Tag.EXTENDED_LINEAR_ADDRESS: 4>, count=2,
                         data=b'vT', checksum=0x30),
             Record(address=0x76543210, tag=<Tag.DATA: 0>, count=13,
                         data=b'Hello, World!', checksum=0x48)]
        """
        ESA = cls.TAG_TYPE.EXTENDED_SEGMENT_ADDRESS
        ELA = cls.TAG_TYPE.EXTENDED_LINEAR_ADDRESS
        base = 0

        for record in records:
            tag = record.tag
            if tag == ESA:
                base = struct.unpack('>H', record.data)[0] << 4
                address = base
            elif tag == ELA:
                base = struct.unpack('>H', record.data)[0] << 16
                address = base
            else:
                address = base + record.address

            record.address = address
