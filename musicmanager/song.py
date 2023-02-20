import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from typing import Dict, Annotated, Any


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
            casted_value = self._cast(normalized_field, v)
            if casted_value is None:
                casted_value = v  # Why? to avoid issue where datetime comes as should not be casted
            setattr(self, normalized_field, casted_value)

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
        org_value = value
        if isinstance(value, datetime.date):
            value = datetime.datetime.fromordinal(value.toordinal())
        if not isinstance(value, datetime.datetime):
            raise TypeError(f"The value has to be of type datetime currently type {type(value)} = {value}")
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


class JsonSong(BaseSong):

    @staticmethod
    def normalize_field(foreign_field_name: Annotated[str, "Field name to be converted"]) -> Annotated[
        str, "Dataclass field name"]:
        pass

    @staticmethod
    def normalize_datetime(foreign_datetime: Annotated[str, "Datetime text to be converted"]) -> datetime.datetime:
        return datetime.datetime.fromordinal(foreign_datetime.toordinal())
        pass


def count_stars(input_string: str, match_bytes: bytes = b'\xe2\xad\x90'):
    """Function for counting count of bytes sequence within input_bytes, used for counting stars"""
    return input_string.encode().count(match_bytes)
