import functools
import inspect
from argparse import ArgumentParser, Namespace
from collections import defaultdict
from functools import partial
from typing import Dict, Union, Callable, Tuple, Annotated
from console_explorer import browse_for_folder
from pathlib import Path
from loguru import logger
from . adapter import AdapterParameterError, AdapterType, Adapter, BaseReadAdapter, BaseWriteAdapter
import music_tag

from musicmanager import BaseSong

SongLocationList = dict[Annotated[tuple, 'artist, name'], Annotated[str, 'location']]


def get_class_arguments(sub_class) -> Dict:
    """Get arguments required for subclass"""
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


def get_all_adapter_names(adapters) -> dict[AdapterType, list[str]]:
    """Return dict with name of adapters of each type"""
    r = defaultdict(list)
    for t in AdapterType:
        for a in adapters[t]:
            r[t].append(a.name)
    return r


def get_matching_kwargs(cls: Callable, incoming_args: Namespace) -> dict:
    """Match incoming_args with signature of Callable and return dict of kwargs"""
    result = {}
    args = inspect.signature(cls).parameters.items()
    for arg_name, extra in args:
        if arg_name in incoming_args and getattr(incoming_args, arg_name) is not None:
            result[arg_name] = getattr(incoming_args, arg_name)
        else:
            result[arg_name] = extra.default
    return result


def get_adapters_in_args(adapters, args: Namespace) -> dict[AdapterType, partial]:
    """Take arguments and extract Reader and Writer classes to be used in main"""
    all_adapter_names = get_all_adapter_names(adapters)
    result = {}
    for t in AdapterType:
        for adapter_name_from_arg, enabled in args._get_kwargs():
            if not enabled:
                continue
            for _ in adapters[t]:
                if getattr(_, 'name') == adapter_name_from_arg:
                    cls = getattr(_, 'sub_class')  # get reader and writer
                    output_args = get_matching_kwargs(cls, args)
                    result[t] = functools.partial(cls, **output_args)
                    continue
    return result


def get_read_write_adapters(args: Namespace, adapters: dict) -> tuple[partial | None, partial | None]:
    adapters_by_args = get_adapters_in_args(adapters, args)
    reader = adapters_by_args.get(AdapterType.READER, None)
    writer = adapters_by_args.get(AdapterType.WRITER, None)
    logger.debug(f'Reader: {reader}')
    logger.debug(f'Writer: {writer}')
    if not reader or not writer:
        raise SystemExit('You need to specify one reader and one writer')
    return reader, writer


def get_folder_song_list(folder: str, ext='mp3') -> SongLocationList:
    """Return a dict with artist, name and location"""
    result = {}
    for f in Path(folder).rglob(f'*.{ext}'):
        id3 = music_tag.load_file(f.as_posix())
        k = (id3['artist'].value, id3['tracktitle'].value)
        result[k] = f.as_posix()
    return result


def update_song_list(song_list: list[BaseSong], folder_song_list: SongLocationList) -> list[BaseSong]:
    for s in song_list:
        new_location = folder_song_list.get((s.artist, s.name), None)
        if not new_location:
            continue
        logger.info(f'Updating {s} {s.location} -> {new_location}')
        s.location = new_location
    return song_list


def setup_logger():
    logger.add('music_manager_{time}.log')


def cli_migrate():
    """Read from reader and output to writer"""
    setup_logger()
    adapters = get_adaptors()
    parser = adapters_to_argparser(adapters)
    args = parser.parse_args()
    reader, writer = get_read_write_adapters(args, adapters)
    logger.info(f'Migrating songs from {reader.func.__name__} to {writer.func.__name__}')
    with reader() as r, writer() as w:
        for song in r:
            logger.info(f"Updating song {song}")
            w.write(song)
    logger.info(f'Complete')


def cli_fix_location():
    """Iterate through reader, update with location of mp3 matching into writer"""
    setup_logger()
    adapters = get_adaptors()
    parser = adapters_to_argparser(adapters)
    args = parser.parse_args()
    logger.info('Which folder do you have your MP3 files?')
    folder = browse_for_folder()
    reader, writer = get_read_write_adapters(args, adapters)
    logger.info(f'Collecting current locations from {reader.func.__name__}')
    adapter_song_list = list(reader())
    folder_song_list = get_folder_song_list(folder)
    adapter_song_list = update_song_list(adapter_song_list, folder_song_list)
    with writer() as w:
        for song in adapter_song_list:
            logger.info(f'Writing {song}')
            w.write(song)
    logger.info(f'Complete')

