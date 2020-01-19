# -*- coding: utf-8 -*-
import re
from typing import Any
from typing import Iterator
from typing import Optional
from typing import Type
from typing import Union

from ..records import Record as _Record
from ..records import RecordSeq
from ..records import Tag
from ..utils import AnyBytes
from ..utils import chop
from ..utils import hexlify
from ..utils import unhexlify


class Record(_Record):
    r"""MOS Technology record file.

    See:
        `<https://srecord.sourceforge.net/man/man5/srec_mos_tech.html>`_

    """

    TAG_TYPE = None

    REGEX = re.compile(r'^;(?P<count>[0-9A-Fa-f]{2})'
                       r'(?P<address>[0-9A-Fa-f]{4})'
                       r'(?P<data>([0-9A-Fa-f]{2}){,255})'
                       r'(?P<checksum>[0-9A-Fa-f]{4})$')
    """Regular expression for parsing a record text line."""

    EXTENSIONS = ('.mos',)

    def __init__(
        self: 'Record',
        address: int,
        tag: Optional[Tag],
        data: AnyBytes,
        checksum: Union[int, type(Ellipsis)] = Ellipsis,
    ) -> None:
        super().__init__(address, tag, data, checksum)

    def __repr__(
        self: 'Record',
    ) -> str:
        text = (f'{type(self).__name__}('
                f'address=0x{self.address:04X}, '
                f'tag={self.tag!r}, '
                f'count={self.count:d}, '
                f'data={self.data!r}, '
                f'checksum=0x{(self._get_checksum() or 0):04X}'
                f')')
        return text

    def __str__(
        self: 'Record',
    ) -> str:
        text = (f';'
                f'{self.count:02X}'
                f'{self.address:04X}'
                f'{hexlify(self.data)}'
                f'{self._get_checksum():04X}')
        return text

    def is_data(
        self: 'Record',
    ) -> bool:
        return self.count > 0

    def compute_checksum(
        self: 'Record',
    ) -> int:
        if self.count:
            checksum = (self.count +
                        (self.address >> 16) +
                        (self.address & 0xFF) +
                        sum(self.data)) & 0xFFFF
        else:
            checksum = self.address
        return checksum

    def check(
        self: 'Record',
    ) -> None:
        if not 0 <= self.address < (1 << 16):
            raise ValueError('address overflow')

        if self.tag is not None:
            raise ValueError('wrong tag')

        if not 0 <= self.count < (1 << 8):
            raise ValueError('count overflow')

        if self.data is None:
            raise ValueError('no data')

        if self.count != len(self.data):
            raise ValueError('count error')

        if self.checksum is not None:
            if not 0 <= self.checksum < (1 << 16):
                raise ValueError('checksum overflow')

            if self.checksum != self.compute_checksum():
                raise ValueError('checksum error')

    @classmethod
    def build_data(
        cls: Type['Record'],
        address: int,
        data: AnyBytes,
    ) -> 'Record':
        r"""Builds a data record.

        Returns:
            :obj:`Record`: A data record.

        Example:
            >>> Record.build_data(0x1234, b'Hello, World!')
            ... #doctest: +NORMALIZE_WHITESPACE
            Record(address=0x1234, tag=None, count=13,
                   data=b'Hello, World!', checksum=0x04AA)
        """
        record = cls(address, None, data)
        return record

    @classmethod
    def build_terminator(
        cls: Type['Record'],
        record_count: int,
    ) -> 'Record':
        r"""Builds a terminator record.

        The terminator record holds the number of data records in the
        `address` fiels.
        Also the `checksum` field is actually set to the record count.

        Returns:
            :obj:`Record`: A terminator record

        Example:
            >>> Record.build_data(0x1234, b'Hello, World!')
            ... #doctest: +NORMALIZE_WHITESPACE
            Record(address=0x00001234, tag=0, count=13,
                   data=b'Hello, World!', checksum=0x69)
        """
        record = cls(record_count, None, b'', record_count)
        return record

    @classmethod
    def split(
        cls: Type['Record'],
        data: AnyBytes,
        address: int = 0,
        columns: int = 16,
        align: bool = True,
        standalone: bool = True,
    ) -> Iterator['Record']:
        r"""Splits a chunk of data into records.

        Arguments:
            data (:obj:`bytes`): Byte data to split.
            address (:obj:`int`): Start address of the first data record being
                split.
            columns (:obj:`int`): Maximum number of columns per data record.
                If ``None``, the whole `data` is put into a single record.
                Maximum of 128 columns.
            align (:obj:`bool`): Aligns record addresses to the column length.
            standalone (:obj:`bool`): Generates a sequence of records that can
                be saved as a standlone record file.

        Yields:
            :obj:`Record`: Data split into records.

        Raises:
            :obj:`ValueError` Address, size, or column overflow.
        """
        if not 0 <= address < (1 << 16):
            raise ValueError('address overflow')
        if not 0 <= address + len(data) <= (1 << 16):
            raise ValueError('size overflow')
        if not 0 < columns < (1 << 8):
            raise ValueError('column overflow')

        align_base = (address % columns) if align else 0
        offset = address
        record_count = 0

        for chunk in chop(data, columns, align_base):
            record_count += 1
            yield cls.build_data(offset, chunk)
            offset += len(chunk)

        if standalone:
            yield cls.build_terminator(record_count)

    @classmethod
    def parse_record(
        cls: Type['Record'],
        line: str,
        *args: Any,
        **kwargs: Any,
    ) -> 'Record':
        del args, kwargs
        line = str(line).strip()
        match = cls.REGEX.match(line)
        if not match:
            raise ValueError('regex error')
        groups = match.groupdict()

        address = int(groups['address'], 16)
        count = int(groups['count'], 16)
        data = unhexlify(groups['data'] or '')
        checksum = int(groups['checksum'], 16)

        if count != len(data):
            raise ValueError('count error')

        record = cls(address, None, data, checksum)
        return record

    @classmethod
    def build_standalone(
        cls: Type['Record'],
        data_records: RecordSeq,
        *args: Any,
        **kwargs: Any,
    ) -> Iterator['Record']:
        r"""Makes a sequence of data records standalone.

        Arguments:
            data_records (:obj:`list` of :class:`Record`): A sequence of data
                records.

        Yields:
            :obj:`Record`: Records for a standalone record file.
        """
        del args, kwargs

        record_count = 0
        for record in data_records:
            record_count += 1
            yield record

        yield cls.build_terminator(record_count)

    @classmethod
    def check_sequence(
        cls: Type['Record'],
        records: RecordSeq,
    ) -> None:
        super().check_sequence(records)

        if len(records) < 1:
            raise ValueError('missing terminator')

        record_count = len(records) - 1
        record = records[-1]

        if record.address != record_count or record.checksum != record_count:
            raise ValueError('wrong terminator')