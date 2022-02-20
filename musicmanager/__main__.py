from typing import List, Tuple, Any, Annotated, Dict, Callable, Sequence, Iterable

from pytunes.client import Client
from loguru import logger
import IReadiTunes as irit
import pickle
from abc import ABC, abstractmethod
import xml.etree.ElementTree as ET
from collections import namedtuple, defaultdict
from dataclasses import dataclass, fields, field
import datetime
from tinydb import TinyDB, Query, JSONStorage
from tinydb_serialization import SerializationMiddleware
from tinydb_serialization.serializers import DateTimeSerializer
from typing import Optional


class AdapterParameterError(Exception):
    """Error while parsing parameter for adapter"""


@dataclass
class BaseSong(ABC):
    """Abstract class for individual services."""

    name: str
    location: str
    artist: str = None
    genre: str = None
    bpm: int = 0
    played_count: int = 0
    rating: int = 0
    year: int = datetime.date.today().year
    _date_added: "_normalize_datetime" = datetime.date.today()

    def __str__(self):
        return f"{self.artist} - {self.name:<40} {self.year:<6} {'⭐️' * int(float(self.rating) / 100 * 5) if self.rating else ''}"

    def __matmul__(self, item):
        """Override special operator song1 @ song2 for comparing name and album"""
        return (self.name, self.artist) == (item.name, item.artist)

    def __init__(
        self, **kwargs: Dict[Annotated[str, "Song field"], Annotated[Any, "Value"]]
    ):
        for k, v in kwargs.items():
            if not (normalized_field := self._normalize_field(k)):
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
    def date_added(self):
        return self._date_added

    @date_added.setter
    def date_added(self, value):
        if not isinstance(value, datetime.datetime):
            raise TypeError("The value has to be of type datetime")
        self._date_added = value

    @staticmethod
    @abstractmethod
    def _normalize_field(
        foreign_field_name: Annotated[str, "Field name to be converted"]
    ) -> Annotated[str, "Dataclass field name"]:
        """
        Take foreign field name and map to field in dataclass.
        A typical pattern use a dict-lookup for the field.
        """

    @staticmethod
    @abstractmethod
    def _normalize_datetime(
        foreign_datetime: Annotated[str, "Datetime text to be converted"]
    ) -> datetime.datetime:
        """
        Take string and turn this into datetime.
        Typical service returns in string and needs to be casted properly.
        """


class TunesSong(BaseSong):
    @staticmethod
    def _normalize_field(
        foreign_field_name: Annotated[str, "Field name to be converted"]
    ) -> Annotated[str, "Dataclass field name"]:
        fk = foreign_field_name.lower().replace(" ", "_")
        t = {"play_count": "played_count"}
        return t.get(fk, fk)

    @staticmethod
    def _normalize_datetime(
        foreign_datetime: Annotated[str, "Datetime text to be converted"]
    ) -> datetime.datetime:
        return datetime.datetime.strptime(foreign_datetime, "%Y-%m-%dT%H:%M:%SZ")


class MacOSMusicSong(BaseSong):
    @staticmethod
    def _normalize_datetime(
        foreign_datetime: Annotated[str, "Datetime text to be converted"]
    ) -> datetime.datetime:
        pass

    @staticmethod
    def _normalize_field(
        foreign_field_name: Annotated[str, "Field name to be converted"]
    ) -> Annotated[str, "Dataclass field name"]:
        pass


class BaseReadAdapter(ABC):
    """Abstract base class for adapter reading songs from service"""

    def __enter__(self):
        """Context mananager"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Override any requirements for closing service"""

    def __iter__(self):
        for song in self.yield_song():
            yield song

    def __contains__(self, target_song: BaseSong):
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
        ] = "/Users/edo/Music/iTunes Library.xml",
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

    def set_current_rating(self, rating: int):
        self.c.current_track.__setattr__("rating", rating)

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


class BaseWriteAdapter(ABC):
    """Abstract base class for adapter writing songs to service"""

    def __enter__(self):
        """Context mnanager"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Override any requirements for closing service"""


class JsonWriteAdapter(BaseWriteAdapter):
    """Write to JSON"""

    def __init__(self, json: str = "music.json") -> None:
        serialization = SerializationMiddleware(JSONStorage)
        serialization.register_serializer(DateTimeSerializer(), 'date')
        self.db = TinyDB(json, storage=serialization)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.db.close()

    def write(self, song: BaseSong):
        self.db.insert(vars(song))


def get_class_arguments(sub_class) -> Dict:
    args_with_annotation = {}
    c = sub_class.__init__.__code__
    args = [_ for _ in c.co_varnames[:c.co_argcount] if _ != "self"]  # only get method parameters
    for arg in args:
        if annotation := (sub_class.__init__.__annotations__.get(arg, {}) or ''):
            if hasattr(annotation, '__metadata__'):
                annotation = annotation.__metadata__
        try:
            type_ = next(
                iter(sub_class.__init__.__annotations__[arg].__dict__.get("__args__", [])),
                None,
            ) or sub_class.__init__.__annotations__.get(arg, None)  # In such not using typing.Annotated
        except KeyError:
            raise AdapterParameterError(
                f'{sub_class.__name__} lack type annotation for parameter "{arg}"'
            ) from None
        args_with_annotation[arg] = (type_, annotation)
    return args_with_annotation


def get_adaptors(
    base_read_class: BaseReadAdapter = BaseReadAdapter,
    base_write_class=BaseWriteAdapter,
) -> Dict[str, str]:
    adaptors = defaultdict(list)
    for type_, base_class in {
        "readers": base_read_class,
        "writers": base_write_class,
    }.items():
        for sub_class in base_class.__subclasses__():
            name = sub_class.__name__
            doc = sub_class.__doc__
            args = get_class_arguments(sub_class)
            adaptors[type_].append(
                {"class": sub_class, "name": name, "args": args, "doc": doc}
            )
    return adaptors


if __name__ == "__main__":
    print(get_adaptors())
    # r = TunesReadAdapter()
    # w = JsonWriteAdapter()
    with TunesReadAdapter(limit=5) as r, JsonWriteAdapter() as w:
        for song in r:
            print(song)
            x = song in r
            w.write(song)

    # m = MacOSMusicReader()
    # for song in m:
    #     print(song)
