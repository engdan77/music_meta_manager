from typing import List, Tuple, Any, Annotated, Dict, Callable, Sequence, Iterable

from pytunes.client import Client
from loguru import logger
import IReadiTunes as irit
import pickle
from abc import ABC, abstractmethod
import xml.etree.ElementTree as ET
from collections import namedtuple
from dataclasses import dataclass, fields, field
import datetime
from typing import Optional

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
    _date_added: '_normalize_datetime' = datetime.date.today()

    def __str__(self):
        return f"{self.artist} - {self.name:<40} {self.year:<6} {'⭐️' * int(float(self.rating) / 100 * 5) if self.rating else ''}"

    def __init__(self, **kwargs: Dict[Annotated[str, "Song field"], Annotated[Any, "Value"]]):
        for k, v in kwargs.items():
            if not (normalized_field := self._normalize_field(k)):
                normalized_field = k
            if normalized_field not in (_.name if not _.name.startswith('_') else _.name[1:] for _ in fields(self)):
                continue
            setattr(self, normalized_field, self._cast(normalized_field, v))

    def _cast(self, field_, value):
        if func := next((_.type for _ in fields(self) if _.name == field_), None):
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
            raise TypeError('The value has to be of type datetime')
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
    def _normalize_field(foreign_field_name: Annotated[str, "Field name to be converted"]) -> Annotated[str, "Dataclass field name"]:
        fk = foreign_field_name.lower().replace(' ', '_')
        t = {'play_count': 'played_count'}
        return t.get(fk, fk)

    @staticmethod
    def _normalize_datetime(foreign_datetime: Annotated[str, "Datetime text to be converted"]) -> datetime.datetime:
        return datetime.datetime.strptime(foreign_datetime, '%Y-%m-%dT%H:%M:%SZ')


class MacOSMusicSong(BaseSong):

    @staticmethod
    def _normalize_datetime(foreign_datetime: Annotated[str, "Datetime text to be converted"]) -> datetime.datetime:
        pass

    @staticmethod
    def _normalize_field(foreign_field_name: Annotated[str, "Field name to be converted"]) -> Annotated[
        str, "Dataclass field name"]:
        pass



class SongsReader(ABC):
    """Abstract base class for adapter reading songs from service"""

    def __iter__(self):
        for song in self.yield_song():
            yield song

    @abstractmethod
    def yield_song(self) -> Iterable[BaseSong]:
        """Return iterable for all songs"""


class TunesFileReader(SongsReader):
    """SongsReader for iTunes"""

    def __init__(self, xml="/Users/edo/Music/iTunes Library.xml"):
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


class MacOSMusicReader(SongsReader):
    """SongsReader for MacOS Music application"""

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


if __name__ == "__main__":
    # f = TunesFileReader()
    m = MacOSMusicReader()
    for song in m:
        print(song)
