import json
import os
from abc import ABCMeta, ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import time
from enum import Enum, auto
from pathlib import Path
from typing import Iterable, Annotated, Any
from xml.etree import ElementTree as ET

import appdirs
import appscript.reference
import pytunes
from loguru import logger
from pytunes.client import Client
from tinydb import JSONStorage, TinyDB
from tinydb_serialization import SerializationMiddleware
from tinydb_serialization.serializers import DateTimeSerializer
from pynput.keyboard import Key, Controller
from time import sleep

from musicmanager.song import TunesSong, MacOSMusicSong, JsonSong, BaseSong


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
        try:
            for song in self.yield_song():
                yield song
        except TypeError as e:
            logger.error(f'Error reading using {self.__class__.__name__} with {e!r}')
            raise e from None

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
        try:
            _ = self.c.status
        except appscript.reference.CommandError as e:
            logger.error(f'Error connecting to client with {e.args}')
            raise e from None
        else:
            logger.debug(f'Connected to client with status {_}')

    def yield_song(self, start_song=0) -> Iterable[BaseSong]:
        self.jump_song(-1)
        self.c.volume = 0
        last_song_index = self.get_current_index()
        self.jump_song(1)
        for i in range(start_song, last_song_index + 1):
            self.jump_song(i)
            self.c.play()
            kv = {}
            for k in self.c.current_track.keys():
                try:
                    kv[k] = self.c.current_track[k]
                except (TypeError, KeyError):
                    continue
            yield MacOSMusicSong(**kv)
        self.c.status

    def get_current_attribute(self, attribute):
        return self.c.current_track[attribute]

    def set_song_field(self, field: str, value: Any):
        if value is None:
            logger.warning(f'Skipping - {field} was None for {self.c.current_track}')
            return
        try:
            self.c.current_track.__setattr__(field, value)
        except AttributeError:
            logger.warning(f'Skipping - unable to set field {field} with the value {value} on {self.c.current_track}')

    def next_song(self):
        self.c.next()

    def get_song_name(self):
        return str(self.c.current_track)

    def get_path_name(self):
        return self.c.current_track.path

    def jump_song(self, index):
        try:
            self.c.jump(index)  # TODO: fix if alert comes up
            logger.warning(f'Unable to locate song on index {index}, skipping to next')
        except pytunes.MusicPlayerError as e:
            self.escape_error_window()
            self.save_index_reference_to_file(index)

    def escape_error_window(self, delay_secs=2):
        os.system("osascript -e 'tell application \"System Events\" to key code 53'")  # send escape
        sleep(delay_secs)

    def save_index_reference_to_file(self, index):
        package_name = next(iter(self.__module__.split('.')))
        app_dir = appdirs.user_data_dir(package_name)
        file = Path(f'{app_dir}/{self.__class__.__name__}_skipped.txt')
        file.parent.mkdir(parents=True, exist_ok=True)
        logger.debug(f'Adding {index} to {file.as_posix()}')
        skipped_files = json.loads(file.read_text()) if file.exists() else []
        skipped_files.append(index)
        file.write_text(json.dumps(list(set(skipped_files))))

    def get_current_index(self):
        return self.c.current_track["index"]

    @staticmethod
    def _match_song(song: BaseSong, field_values: Annotated[dict, 'field and values']):
        return all([getattr(song, k, None) == v for k, v in field_values.items()])

    def get_song_index_by_fields(self, field_values: Annotated[dict, 'field and values']):
        all_songs = [_ for _ in self.yield_song()]
        for song_index, s in enumerate(all_songs, start=1):
            logger.debug(f'searching song, current index {song_index}')
            if self._match_song(s, field_values):
                logger.debug(f'Found {s} on index {song_index}')
                return song_index


class JsonReadAdapter(BaseReadAdapter):
    """Read from JSON"""

    def __init__(self, json_read: Annotated[str, "json file"] = "/tmp/music.json") -> None:
        serialization = SerializationMiddleware(JSONStorage)
        serialization.register_serializer(DateTimeSerializer(), 'date')
        self.db = TinyDB(json_read, storage=serialization)

    def yield_song(self) -> Iterable[BaseSong]:
        for s in self.db.all():
            yield JsonSong(**s)
        # TODO: fix
        ...


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
        self.db = TinyDB(json_write, storage=serialization, indent=2)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.db.close()

    def write(self, song: BaseSong):
        self.db.insert(vars(song))


class MacOSMusicWriteAdapter(BaseWriteAdapter, MacOSMusicReadAdapter):
    """Write song to MacOS Music application"""

    def __init__(self,
                 match_fields: Annotated[str, "match fields before updates, comma separated"] = "name,artist",
                 exclude_fields: Annotated[str, "which fields to exclude, comma separated"] = "none"):
        super().__init__()
        self.exclude_fields = exclude_fields
        self.match_fields = [_.strip() for _ in match_fields.split(',')]
        self.c = Client()

    def write(self, song: BaseSong):
        match_fields = {_: getattr(song, _) for _ in self.match_fields}
        song_index = self.get_song_index_by_fields(match_fields)
        if song_index is not None:
            self.jump_song(song_index)
            for field_, value in asdict(song).items():
                if field_ not in self.exclude_fields.split(','):
                    self.set_song_field(field_, value)
            logger.info(f'Complete updating {song}')
        else:
            logger.warning(f'Song not found {song}')
