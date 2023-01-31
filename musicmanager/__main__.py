"""Program for migration music meta data between different services"""
import inspect
from typing import List, Tuple, Any, Annotated, Dict, Callable, Sequence, Iterable, Union
from pytunes.client import Client
from loguru import logger
import IReadiTunes as irit
import pickle
from abc import ABC, abstractmethod, ABCMeta
import xml.etree.ElementTree as ET
from collections import namedtuple, defaultdict
from dataclasses import dataclass, fields, field, asdict
import datetime
from tinydb import TinyDB, Query, JSONStorage
from tinydb_serialization import SerializationMiddleware
from tinydb_serialization.serializers import DateTimeSerializer
from typing import Optional
from argparse import ArgumentParser
from enum import Enum, auto


class AdapterType(Enum):
    """Enum for type of adapter"""
    READER = auto()
    WRITER = auto()

    def __str__(self):
        """Used for crafting command parameters"""
        return self.name.lower()


class AdapterParameterError(Exception):
    """Error while parsing parameter for adapter"""


@dataclass
class Adapter:
    """Dataclass for registering available ReadAdapters and WriteAdapters.
    Used while dynamically creating CLI parameters.
    """

    sub_class: ABCMeta
    name: str
    args: dict
    doc: str


@dataclass
class BaseSong(ABC):
    """Abstract dataclass for normalizing song objects.
    Inherit and implement to be used by concrete class inherited by BaseReadAdapter

    normalize_field(foreign_field_name: str) -> one_of_below_dataclass_field: str
    Required if field names differs from dataclass
    fields: name, location, artist, genre, played_count, rating, year, _date_Added

    normalize_datetime(foreign_datetime_format: str) -> datetime
    Required for casting datetime
    """

    name: str
    location: str
    artist: str = None
    genre: str = None
    bpm: int = 0
    played_count: int = 0
    rating: int = 0
    year: int = datetime.date.today().year
    _date_added: "normalize_datetime" = datetime.date.today()

    def __str__(self):
        return f"{self.artist} - {self.name:<40} {self.year:<6} {self.rating_in_stars if self.rating else ''}"

    def __matmul__(self, other):
        """Override special operator song1 @ song2 for comparing name and album"""
        return (self.name, self.artist) == (other.name, other.artist)

    def __eq__(self, condition):
        """Allow comparison of year, name or stars"""
        if count_stars(condition) and count_stars(self.rating_in_stars) == count_stars(condition):
            return True
        if isinstance(condition, int) and self.year == condition:
            return True
        if isinstance(condition, str) and self.name == condition:
            return True
        return False

    def __ge__(self, condition):
        """Allow greater or equal than year or stars"""
        if isinstance(condition, int):
            return self.year >= condition
        if count_stars(condition) and count_stars(self.rating_in_stars) >= count_stars(condition):
            return True
        return False

    def __lt__(self, condition):
        """Allow less than year or stars"""
        if isinstance(condition, int):
            return self.year < condition
        if count_stars(condition) and count_stars(self.rating_in_stars) < count_stars(condition):
            return True
        return False

    def __init__(
        self, **kwargs: Dict[Annotated[str, "Song field"], Annotated[Any, "Value"]]
    ):
        for k, v in kwargs.items():
            if not (normalized_field := self.normalize_field(k)):
                normalized_field = k
            if normalized_field not in (
                _.name if not _.name.startswith("_") else _.name[1:]
                for _ in fields(self)
            ):
                continue
            setattr(self, normalized_field, self._cast(normalized_field, v))

    def _cast(self, field_, value):
        if func := next((_.type for _ in fields(self) if _.name in [field_, f'_{field_}']), None):  # check also private field
            if not callable(func) and isinstance(func, str):
                func = getattr(self, func)
            return func(value)
        else:
            return value

    @property
    def rating_in_stars(self):
        return '⭐️' * int(float(self.rating) / 100 * 5)

    @property
    def date_added(self):
        return self._date_added

    @date_added.setter
    def date_added(self, value):
        if not isinstance(value, datetime.datetime):
            raise TypeError("The value has to be of type datetime")
        self._date_added = value

    @staticmethod
    @abstractmethod
    def normalize_field(
        foreign_field_name: Annotated[str, "Field name to be converted"]
    ) -> Annotated[str, "Dataclass field name"]:
        """
        Take foreign field name and map to field in dataclass.
        A typical pattern use a dict-lookup for the field.
        """

    @staticmethod
    @abstractmethod
    def normalize_datetime(
        foreign_datetime: Annotated[str, "Datetime text to be converted"]
    ) -> datetime.datetime:
        """
        Take string and turn this into datetime.
        Typical service returns in string and needs to be casted properly.
        """


class TunesSong(BaseSong):
    @staticmethod
    def normalize_field(
        foreign_field_name: Annotated[str, "Field name to be converted"]
    ) -> Annotated[str, "Dataclass field name"]:
        fk = foreign_field_name.lower().replace(" ", "_")
        t = {"play_count": "played_count"}
        return t.get(fk, fk)

    @staticmethod
    def normalize_datetime(
        foreign_datetime: Annotated[str, "Datetime text to be converted"]
    ) -> datetime.datetime:
        return datetime.datetime.strptime(foreign_datetime, "%Y-%m-%dT%H:%M:%SZ")


class MacOSMusicSong(BaseSong):
    @staticmethod
    def normalize_field(
        foreign_field_name: Annotated[str, "Field name to be converted"]
    ) -> Annotated[str, "Dataclass field name"]:
        pass

    @staticmethod
    def normalize_datetime(
        foreign_datetime: Annotated[str, "Datetime text to be converted"]
    ) -> datetime.datetime:
        pass


class BaseReadAdapter(ABC):
    """Abstract base class for adapter reading songs from service
    Subclass and implement

    __init__()
    Depending on the service to read from

    yield_song() -> Iterable[BaseSong]
    This is the only concrete method required to allow context manager to work
    """
    adapter_type = AdapterType.READER

    def __enter__(self):
        """Context mananager"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Override any requirements for closing service"""

    def __iter__(self):
        for song in self.yield_song():
            yield song

    def __contains__(self, target_song: BaseSong) -> bool:
        for source_song in iter(self):
            if source_song @ target_song:
                return True
        else:
            return False

    @abstractmethod
    def yield_song(self) -> Iterable[BaseSong]:
        """Return iterable for all songs"""


class TunesReadAdapter(BaseReadAdapter):
    """Read from iTunes"""

    def __init__(
        self,
        xml: Annotated[
            str, "xml file from iTunes"
        ] = "/Users/edo/Music/backup/iTunes Library.xml",
        limit: Annotated[
            int, "limit to number of songs",
        ] = 0
    ):
        self.limit = limit
        self.local_fields = [
            "Year",
            "BPM",
            "Date Added",
            "Play Count",
            "Rating",
            "Name",
            "Artist",
            "Genre",
            "Location",
        ]
        self.tree = ET.parse(xml).getroot()

    def yield_song(self) -> Iterable[BaseSong]:
        s = self.tree[0].findall("dict")[0]
        if self.limit:
            s = s[:self.limit + 1]
        for item in s:
            try:
                if item[0].text == "Track ID":
                    keys = item[::2]
                    values = item[1::2]
            except (ValueError, IndexError):
                pass
            else:
                keys_values_el: list[tuple[Any, Any]] = list(zip(keys, values))
                key_values = {i[0].text: i[1].text for i in keys_values_el}
                yield TunesSong(**key_values)


class MacOSMusicReadAdapter(BaseReadAdapter):
    """Read from MacOS Music application"""

    def __init__(self):
        self.c = Client()

    def yield_song(self) -> Iterable[BaseSong]:
        self.jump_song(-1)
        last_song_index = self.get_current_index()
        for i in range(1, last_song_index):
            self.next_song()
            self.c.volume = 0
            self.c.current_track.refresh()
            kv = {}
            for k in self.c.current_track.keys():
                try:
                    kv[k] = self.c.current_track[k]
                except (TypeError, KeyError):
                    continue
            yield MacOSMusicSong(**kv)

    def get_current_attribute(self, attribute):
        return self.c.current_track[attribute]

    def set_song_field(self, field: str, value: Any):
        self.c.current_track.__setattr__(field, value)

    def next_song(self):
        self.c.next()

    def get_song_name(self):
        return str(self.c.current_track)

    def get_path_name(self):
        return self.c.current_track.path

    def jump_song(self, index):
        self.c.jump(index)

    def get_current_index(self):
        return self.c.current_track["index"]

    @staticmethod
    def _match_song(song: BaseSong, field_values: Annotated[dict, 'field and values']):
        return all([getattr(song, k, None) == v for k, v in field_values.items()])

    def get_song_index_by_fields(self, field_values: Annotated[dict, 'field and values']):
        for song_index, s in enumerate(self.yield_song()):
            if self._match_song(s, field_values):
                return song_index


class JsonReadAdapter(BaseReadAdapter):
    """Read from JSON"""

    def __init__(self, json_read: Annotated[str, "json file"] = "/tmp/music.json") -> None:
        serialization = SerializationMiddleware(JSONStorage)
        serialization.register_serializer(DateTimeSerializer(), 'date')
        self.db = TinyDB(json, storage=serialization)

    def yield_song(self) -> Iterable[BaseSong]:
        # TODO: fix
        pass


class BaseWriteAdapter(ABC):
    """Abstract base class for adapter writing songs to service
    Subclass and implement

    write(song: BaseSong)
    Single method for writing a song object subclassed from BaseSong
    """
    adapter_type = AdapterType.WRITER

    def __enter__(self):
        """Context manager"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Override any requirements for closing service"""

    @abstractmethod
    def write(self, song: BaseSong):
        """Method responsible for writing a song to service"""


class JsonWriteAdapter(BaseWriteAdapter):
    """Write to JSON"""

    def __init__(self, json_write: Annotated[str, "json file"] = "/tmp/music.json") -> None:
        serialization = SerializationMiddleware(JSONStorage)
        serialization.register_serializer(DateTimeSerializer(), 'date')
        self.db = TinyDB(json, storage=serialization)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.db.close()

    def write(self, song: BaseSong):
        self.db.insert(vars(song))


class MacOSMusicWriteAdapter(BaseWriteAdapter, MacOSMusicReadAdapter):
    """Write song to MacOS Music application"""

    def __init__(self, 
                 match_fields: Annotated[str, "match fields before updates, comma separated"] = "name,artist",
                 exclude_fields: Annotated[str, "which fields to exclude, comma separated"] = "location"):
        super().__init__()
        self.exclude_fields = exclude_fields
        self.match_fields = [_.strip() for _ in match_fields.split(',')]
        self.c = Client()

    def write(self, song: BaseSong):
        match_fields = {_: getattr(song, _) for _ in self.match_fields}
        if song_index := self.get_song_index_by_fields(match_fields):
            self.jump_song(song_index)
            self.set_song_field(asdict(song))
        pass


def count_stars(input_string: str, match_bytes: bytes = b'\xe2\xad\x90'):
    """Function for counting count of bytes sequence within input_bytes, used for counting stars"""
    return input_string.encode().count(match_bytes)


def get_class_arguments(sub_class) -> Dict:
    args_with_annotation = {}
    c = sub_class.__init__.__code__
    args = [_ for _ in c.co_varnames[:c.co_argcount] if _ != "self"]  # only get method parameters
    for arg in args:
        default_arg = inspect.signature(sub_class.__init__).parameters[arg].default
        if annotation := (sub_class.__init__.__annotations__.get(arg, {}) or ''):
            if hasattr(annotation, '__metadata__'):
                annotation = next(iter(annotation.__metadata__))
        try:
            type_ = next(
                iter(sub_class.__init__.__annotations__[arg].__dict__.get("__args__", [])),
                None,
            ) or sub_class.__init__.__annotations__.get(arg, None)  # In such not using typing.Annotated
        except KeyError:
            raise AdapterParameterError(
                f'{sub_class.__name__} lack type annotation for parameter "{arg}"'
            ) from None
        args_with_annotation[arg] = (type_, annotation, default_arg)
    return args_with_annotation


def get_adaptors(
    base_read_class=BaseReadAdapter,
    base_write_class=BaseWriteAdapter,
) -> Dict[AdapterType, list[Adapter]]:
    adapters = defaultdict(list)
    for base_class in (base_read_class, base_write_class):
        sub_class: Union[BaseReadAdapter, BaseWriteAdapter]
        for sub_class in base_class.__subclasses__():
            adapter = Adapter(sub_class, sub_class.__name__, get_class_arguments(sub_class), sub_class.__doc__)
            adapters[sub_class.adapter_type].append(adapter)
    return adapters


def adapters_to_argparser(adapters: dict[AdapterType, list[Adapter]]) -> ArgumentParser:
    # todo: enum for reader and writer
    parser = ArgumentParser(__package__, description=__doc__)
    for adapter_type, adapters in adapters.items():
        for adapter in adapters:
            # group = parser.add_mutually_exclusive_group()
            group = parser.add_argument_group(adapter.name)
            group.add_argument(f'--{adapter.name}', action='store_true', help=adapter.doc)
            for parameter_name, (parameter_type, parameter_help, default_arg) in adapter.args.items():
                group.add_argument(f'--{parameter_name}', type=parameter_type, help=f'[{adapter.name}] {parameter_help} (default: {default_arg})' if isinstance(parameter_help, str) else '')
    return parser


if __name__ == "__main__":
    adapters = get_adaptors()
    parser = adapters_to_argparser(adapters)
    args = parser.parse_args()
    # exit()
    r = TunesReadAdapter()
    w = JsonWriteAdapter()
    with TunesReadAdapter(limit=5) as r, JsonWriteAdapter() as w:
        for song in r:
            print(f"Writing: {song}")
            song == '⭐⭐⭐⭐'
            w.write(song)

    # m = MacOSMusicReader()
    # for song in m:
    #     print(song)
