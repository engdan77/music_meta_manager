from typing import List, Tuple, Any, Annotated, Dict

from pytunes.client import Client
from loguru import logger
import IReadiTunes as irit
import pickle
from abc import ABC, abstractmethod
import xml.etree.ElementTree as ET
from collections import namedtuple
from dataclasses import dataclass, fields
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
    date_added: datetime.date = datetime.date.today()
    year: int = datetime.date.today().year

    def __str__(self):
        return f"{self.artist} - {self.name:<40} {self.year:<6} {'⭐️' * int(float(self.rating)/100 * 5) if self.rating else ''}"

    def __init__(self, **kwargs: Dict[Annotated[str, "Song field"], Annotated[Any, "Value"]]):
        for k, v in kwargs.items():
            if k not in (_.name for _ in fields(self)):
                continue
            setattr(self, self._normalize_field(k), v)

    @staticmethod
    @abstractmethod
    def _normalize_field(
        foreign_field_name: Annotated[str, "Field name to be converted"]
    ) -> Annotated[str, "Dataclass field name"]:
        """Take foreign field name and map to field in dataclass."""

    @staticmethod
    @abstractmethod
    def _normalize_datetime(
        foreign_datetime: Annotated[str, "Datetime text to be converted"]
    ) -> datetime.datetime:
        """Take string and turn this into datetime."""


class TunesSong(BaseSong):

    @staticmethod
    def _normalize_field(foreign_field_name: Annotated[str, "Field name to be converted"]) -> Annotated[
        str, "Dataclass field name"]:
        fk = foreign_field_name.lower().replace(' ', '_')
        t = {'play_count': 'played_count'}
        return t.get(fk, fk)

    @staticmethod
    def _normalize_datetime(foreign_datetime: Annotated[str, "Datetime text to be converted"]) -> datetime.datetime:
        return datetime.datetime.strptime(foreign_datetime, '%Y-%m-%dT%H:%M:%SZ')


class SongsReader(ABC):
    @abstractmethod
    def get_songs(self) -> List[BaseSong]:
        pass


class TunesFileReader(SongsReader):
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

    def get_songs(self):
        s = self.tree[0].findall("dict")[0]
        songs = []
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
                x = TunesSong(**key_values)
                print(x)
                songs.append(x)
        return songs


class MusicClientParser(SongsReader):
    def __init__(self):
        self.c = Client()
        r = self.get_all_ratings()

    def get_current_attribute(self, attribute):
        return self.c.current_track[attribute]

    def get_songs(self):
        songs = []
        rating_list = []
        self.jump_song(-1)
        last_song_index = self.get_current_index()
        for i in range(1, last_song_index):
            logger.info(f"processing song {i}/{last_song_index}")
            try:
                rating = self.get_current_attribute(
                    "rating"
                )  # TODO: get all attributes
            except TypeError:
                logger.debug(f"skipping {i}")
                continue
            path = self.get_path_name()
            song = self.get_song_name()
            songs.append([rating, song, path])
            self.next_song()
        return rating_list

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
    f = TunesFileReader()
    all_songs = f.get_songs()
    # m = MusicClientParser()
