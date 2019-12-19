# -*- coding: utf-8 -*-
import enum
import re
import struct
from typing import ByteString
from typing import Optional
from typing import Sequence
from typing import Union

from ..records import Record as _Record
from ..records import RecordSeq
from ..records import Tag as _Tag
from ..records import get_data_records
from ..utils import chop
from ..utils import expmsg
from ..utils import hexlify
from ..utils import sum_bytes
from ..utils import unhexlify


@enum.unique
class Tag(_Tag):
    """Motorola S-record tag."""

    HEADER = 0
    """Header string. Optional."""

    DATA_16 = 1
    """16-bit address data record."""

    DATA_24 = 2
    """24-bit address data record."""

    DATA_32 = 3
    """32-bit address data record."""

    _RESERVED = 4
    """Reserved tag."""

    COUNT_16 = 5
    """16-bit record count. Optional."""

    COUNT_24 = 6
    """24-bit record count. Optional."""

    START_32 = 7
    """32-bit start address. Terminates :attr:`DATA_32`."""

    START_24 = 8
    """24-bit start address. Terminates :attr:`DATA_24`."""

    START_16 = 9
    """16-bit start address. Terminates :attr:`DATA_16`."""

    @classmethod
    def is_data(cls, value: Union[int, 'Tag']) -> bool:
        r""":obj:`bool`: `value` is a data record tag."""
        return value in (cls.DATA_16, cls.DATA_24, cls.DATA_32)


class Record(_Record):
    r"""Motorola S-record.

    See:
        `<https://en.wikipedia.org/wiki/SREC_(file_format)>`_
    """

    TAG_TYPE = Tag
    """Associated Python class for tags."""

    TAG_TO_ADDRESS_LENGTH = (2, 2, 3, 4, None, None, None, 4, 3, 2)
    """Maps a tag to its address byte length, if available."""

    MATCHING_TAG = (None, None, None, None, None, None, None, 3, 2, 1)
    """Maps the terminator tag to its mathing data tag."""

    REGEX = re.compile(r'^S[0-9]([0-9A-Fa-f]{2}){4,140}$')
    """Regular expression for parsing a record text line."""

    EXTENSIONS = ('.mot', '.s19', '.s28', '.s37', '.srec', '.exo')
    """Automatically supported file extensions."""

    def __init__(self, address: int,
                 tag: Tag,
                 data: ByteString,
                 checksum: Union[int, type(Ellipsis)] = Ellipsis) -> None:

        super().__init__(address, self.TAG_TYPE(tag), data, checksum)

    def __str__(self) -> str:
        self.check()
        tag_text = f'S{self.tag:d}'

        address_length = self.TAG_TO_ADDRESS_LENGTH[self.tag]
        if address_length is None:
            address_text = ''
            count_text = f'{(len(self.data) + 1):02X}'
        else:
            count_text = f'{(address_length + len(self.data) + 1):02X}'
            offset = 2 * (4 - address_length)
            address_text = f'{self.address:08X}'[offset:]

        data_text = hexlify(self.data)

        checksum_text = f'{self._get_checksum():02X}'

        text = ''.join((tag_text,
                        count_text,
                        address_text,
                        data_text,
                        checksum_text))
        return text

    def compute_count(self) -> int:
        tag = int(self.tag)
        address_length = self.TAG_TO_ADDRESS_LENGTH[tag] or 0
        return address_length + len(self.data) + 1

    def compute_checksum(self) -> int:
        checksum = sum_bytes(struct.pack('BL', self.count, self.address))
        checksum += sum_bytes(self.data)
        checksum = (checksum & 0xFF) ^ 0xFF
        return checksum

    def check(self) -> None:
        super().check()

        tag = int(self.TAG_TYPE(self.tag))

        if tag in (0, 4, 5, 6) and self.address:
            raise ValueError('address error')

        if self.count != self.compute_count():
            raise ValueError('count error')

    @classmethod
    def fit_data_tag(cls, endex: int) -> 'Record':
        r"""Fits a data tag by address.

        Depending on the value of `endex`, get the data tag with the smallest
        supported address.

        Arguments:
            endex (:obj:`int`): Exclusive end address of the data.

        Returns:
            :obj:`Tag`: Fitting data tag.

        Raises:
            :obj:`ValueError` Address overflow.

        Examples:
            >>> Record.fit_data_tag(0x00000000)
            <Tag.DATA_16: 1>

            >>> Record.fit_data_tag(0x0000FFFF)
            <Tag.DATA_16: 1>

            >>> Record.fit_data_tag(0x00010000)
            <Tag.DATA_16: 1>

            >>> Record.fit_data_tag(0x00FFFFFF)
            <Tag.DATA_24: 2>

            >>> Record.fit_data_tag(0x01000000)
            <Tag.DATA_24: 2>

            >>> Record.fit_data_tag(0xFFFFFFFF)
            <Tag.DATA_32: 3>

            >>> Record.fit_data_tag(0x100000000)
            <Tag.DATA_32: 3>
        """

        if not 0 <= endex <= (1 << 32):
            raise ValueError('address overflow')

        elif endex <= (1 << 16):
            return cls.TAG_TYPE.DATA_16

        elif endex <= (1 << 24):
            return cls.TAG_TYPE.DATA_24

        else:
            return cls.TAG_TYPE.DATA_32

    @classmethod
    def fit_count_tag(cls, record_count: int) -> 'Record':
        r"""Fits the record count tag.

        Arguments:
            record_count (:obj:`int`): Record count.

        Returns:
            :obj:`Tag`: Fitting record count tag.

        Raises:
            :obj:`ValueError` Count overflow.

        Examples:
            >>> Record.fit_count_tag(0x0000000)
            <Tag.COUNT_16: 5>

            >>> Record.fit_count_tag(0x00FFFF)
            <Tag.COUNT_16: 5>

            >>> Record.fit_count_tag(0x010000)
            <Tag.COUNT_24: 6>

            >>> Record.fit_count_tag(0xFFFFFF)
            <Tag.COUNT_24: 6>
        """

        if not 0 <= record_count < (1 << 24):
            raise ValueError('count overflow')

        elif record_count < (1 << 16):
            return cls.TAG_TYPE.COUNT_16

        else:  # record_count < (1 << 24)
            return cls.TAG_TYPE.COUNT_24

    @classmethod
    def build_header(cls, data: ByteString) -> 'Record':
        r"""Builds a header record.

        Arguments:
            data (:obj:`bytes`): Header string data.

        Returns:
            :obj:`Record`: Header record.

        Example:
            >>> str(Record.build_header(b'Hello, World!'))
            'S010000048656C6C6F2C20576F726C642186'
        """
        return cls(0, 0, data)

    @classmethod
    def build_data(cls, address: int,
                   data: ByteString,
                   tag: Optional[Tag] = None) -> 'Record':
        r"""Builds a data record.

        Arguments:
            address (:obj:`int`): Record start address.
            data (:obj:`bytes`): Some program data.
            tag (:obj:`Tag`): Data tag record.
                If ``None``, automatically selects the fitting one.

        Returns:
            :obj:`Record`: Data record.

        Raises:
            :obj:`ValueError` Tag error.

        Examples:
            >>> str(Record.build_data(0x1234, b'Hello, World!'))
            'S110123448656C6C6F2C20576F726C642140'

            >>> str(Record.build_data(0x1234, b'Hello, World!',
            ...                               tag=Tag.DATA_16))
            'S110123448656C6C6F2C20576F726C642140'

            >>> str(Record.build_data(0x123456, b'Hello, World!',
            ...                               tag=Tag.DATA_24))
            'S21112345648656C6C6F2C20576F726C6421E9'

            >>> str(Record.build_data(0x12345678, b'Hello, World!',
            ...                               tag=Tag.DATA_32))
            'S3121234567848656C6C6F2C20576F726C642170'
        """
        if tag is None:
            tag = cls.fit_data_tag(address + len(data))

        if tag not in (1, 2, 3):
            raise ValueError('tag error')

        record = cls(address, tag, data)
        return record

    @classmethod
    def build_terminator(cls, start: int,
                         last_data_tag: Tag = Tag.DATA_16) \
            -> 'Record':
        r"""Builds a terminator record.

        Arguments:
            start (:obj:`int`): Program start address.
            last_data_tag (:obj:`Tag`): Last data record tag to match.

        Returns:
            :obj:`Record`: Terminator record.

        Examples:
            >>> str(Record.build_terminator(0x1234))
            'S9031234B6'

            >>> str(Record.build_terminator(0x1234,
            ...                                     Tag.DATA_16))
            'S9031234B6'

            >>> str(Record.build_terminator(0x123456,
            ...                                     Tag.DATA_24))
            'S8041234565F'

            >>> str(Record.build_terminator(0x12345678,
            ...                                     Tag.DATA_32))
            'S70512345678E6'
        """
        tag_index = cls.MATCHING_TAG.index(int(last_data_tag))
        terminator_record = cls(start, tag_index, b'')
        return terminator_record

    @classmethod
    def build_count(cls, record_count: int) -> 'Record':
        r"""Builds a count record.

        Arguments:
            count (:obj:`int`): Record count.

        Returns:
            :obj:`Record`: Count record.

        Raises:
            :obj:`ValueError` Count error.

        Examples:
             >>> str(Record.build_count(0x1234))
             'S5031234B6'

             >>> str(Record.build_count(0x123456))
             'S6041234565F'
        """
        tag = cls.fit_count_tag(record_count)
        count_data = struct.pack('>L', record_count)
        count_record = cls(0, tag, count_data[(7 - tag):])
        return count_record

    @classmethod
    def parse_record(cls, line: str) -> 'Record':
        line = str(line).strip()
        match = cls.REGEX.match(line)
        if not match:
            raise ValueError('regex error')

        tag = int(line[1:2])
        count = int(line[2:4], 16)
        assert 2 * count == len(line) - (2 + 2)
        address_length = cls.TAG_TO_ADDRESS_LENGTH[tag] or 0
        address = int('0' + line[4:(4 + 2 * address_length)], 16)
        data = unhexlify(line[(4 + 2 * address_length):-2])
        checksum = int(line[-2:], 16)

        record = cls(address, tag, data, checksum)
        return record

    @classmethod
    def build_standalone(cls, data_records: RecordSeq,
                         start: Optional[int] = None,
                         tag: Optional[Tag] = None,
                         header: ByteString = b''):
        r"""Makes a sequence of data records standalone.

        Arguments:
            data_records (:obj:`list` of :class:`Record`): A sequence of data
                records.
            start (:obj:`int`): Program start address.
                If ``None``, it is assigned the minimum data record address.
            tag (:obj:`Tag`): Data tag record.
                If ``None``, automatically selects the fitting one.
            header (:obj:`bytes`): Header byte data.

        Yields:
            :obj:`Record`: Records for a standalone record file.
        """
        address = 0
        count = 0
        if tag is None:
            if not data_records:
                data_records = [cls.build_data(0, b'')]
            tag = max(record.tag for record in data_records)

        yield cls.build_header(header)

        for record in data_records:
            yield record
            count += 1
            address = max(address, record.address + len(record.data))
            tag = max(tag, record.tag)

        yield cls.build_count(count)

        if start is None:
            if not data_records:
                data_records = [cls.build_data(0, b'')]
            start = min(record.address for record in data_records)

        yield cls.build_terminator(start, tag)

    @classmethod
    def check_sequence(cls, records: RecordSeq, overlap: bool = True) -> None:
        super().check_sequence(records)

        unpack = struct.unpack
#        last_data = None
        first_tag = None
        data_count = 0
        it = iter(records)
        header_found = False
        count_found = False

        while True:
            try:
                record = next(it)
            except StopIteration:
                record = None
                break

            record_tag = int(record.tag)

            if record_tag == 0:
                if header_found:
                    raise ValueError('header error')

                header_found = True

            elif record_tag in (1, 2, 3):
                if first_tag is None:
                    first_tag = record_tag

                elif record_tag != first_tag:
                    raise ValueError(expmsg(record_tag, 'in (1, 2, 3)',
                                            'tag error'))

#                if overlap and record.overlaps(last_data):
#                    raise ValueError('overlapping records')

#                last_data = record
                data_count += 1

            elif record_tag == 5:
                if count_found:
                    raise ValueError('misplaced count')
                count_found = True
                expected_count = unpack('>H', record.data)[0]
                if expected_count != data_count:
                    raise ValueError(expmsg(data_count, expected_count,
                                            'record count error'))

            elif record_tag == 6:
                if count_found:
                    raise ValueError('misplaced count')
                count_found = True
                u, hl = unpack('>BH', record.data)
                expected_count = (u << 16) | hl
                if expected_count != data_count:
                    raise ValueError(expmsg(data_count, expected_count,
                                            'record count error'))

            else:
                break

        if not count_found:
            raise ValueError('missing count')

        if not header_found:
            raise ValueError('missing header')

        if record is None:
            raise ValueError('missing start')
        elif record.tag not in (7, 8, 9):
            raise ValueError('tag error')
        else:
            matching_tag = cls.MATCHING_TAG[record.tag]
            if first_tag != matching_tag:
                raise ValueError(expmsg(matching_tag, first_tag,
                                        'matching tag error'))

        try:
            next(it)
        except StopIteration:
            pass
        else:
            raise ValueError('sequence length error')

    @classmethod
    def split(cls, data: ByteString,
              address: int = 0,
              columns: int = 16,
              align: bool = True,
              standalone: bool = True,
              start: Optional[int] = None,
              tag: Optional[Tag] = None,
              header: ByteString = b'') \
            -> Sequence['Record']:
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
            start (:obj:`int`): Program start address.
                If ``None``, it is assigned the minimum data record address.
            tag (:obj:`Tag`): Data tag record.
                If ``None``, automatically selects the fitting one.
            header (:obj:`bytes`): Header byte data.

        Yields:
            :obj:`Record`: Data split into records.

        Raises:
            :obj:`ValueError` Address, size, or column overflow.
        """
        if not 0 <= address < (1 << 32):
            raise ValueError('address overflow')
        if not 0 <= address + len(data) <= (1 << 32):
            raise ValueError('size overflow')
        if not 0 < columns < 128:
            raise ValueError('column overflow')

        if start is None:
            start = address
        if tag is None:
            tag = cls.fit_data_tag(address + len(data))
        count = 0

        if standalone:
            yield cls.build_header(header)

        skip = (address % columns) if align else 0
        for chunk in chop(data, columns, skip):
            yield cls.build_data(address, chunk, tag)
            count += 1
            address += len(chunk)

        if standalone:
            yield cls.build_count(count)
            yield cls.build_terminator(start, tag)

    @classmethod
    def fix_tags(cls, records: RecordSeq) -> Sequence['Record']:
        r"""Fix record tags.

        Updates record tags to reflect modified size and count.
        All the checksums are updated too.
        Operates in-place.

        Arguments:
            records (:obj:`list` of :obj:`Record`): A sequence of
                records. Must be in-line mutable.
        """
        if records:
            max_address = max(record.address + len(record.data)
                              for record in records)
        else:
            max_address = 0
        tag = cls.TAG_TYPE(cls.fit_data_tag(max_address))
        COUNT_16 = cls.TAG_TYPE.COUNT_16
        start_tags = (cls.TAG_TYPE.START_16,
                      cls.TAG_TYPE.START_24,
                      cls.TAG_TYPE.START_32)
        start_ids = []

        for index, record in enumerate(records):
            if record.tag == COUNT_16:
                count = struct.unpack('>L', record.data.rjust(4, b'\0'))[0]
                if count >= (1 << 16):
                    record.tag = cls.TAG_TYPE.COUNT_24
                    record.data = struct.pack('>L', count)[1:]
                    record.update_count()
                    record.update_checksum()

            elif record.is_data():
                record.tag = tag
                record.update_checksum()

            elif record.tag in start_tags:
                start_ids.append(index)

        data_records = get_data_records(records)
        if not data_records:
            data_records = [cls.build_data(0, b'')]
        max_tag = int(max(record.tag for record in data_records))
        start_tag = cls.TAG_TYPE(cls.MATCHING_TAG.index(max_tag))
        for index in start_ids:
            records[index].tag = start_tag
