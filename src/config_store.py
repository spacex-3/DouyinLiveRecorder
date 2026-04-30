# -*- coding: utf-8 -*-
from __future__ import annotations

import configparser
import io
import os
import tempfile
import threading
from pathlib import Path
from typing import Iterable

TEXT_ENCODING = 'utf-8-sig'
CONFIG_WRITE_LOCK = threading.RLock()


def ensure_file(path: str | Path, default_content: str = '') -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if not file_path.exists():
        atomic_write_text(file_path, default_content)
    return file_path


def read_text(path: str | Path, encoding: str = TEXT_ENCODING) -> str:
    file_path = ensure_file(path)
    return file_path.read_text(encoding=encoding)


def atomic_write_text(path: str | Path, content: str, encoding: str = TEXT_ENCODING) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None

    with CONFIG_WRITE_LOCK:
        fd, temp_name = tempfile.mkstemp(prefix=f'.{file_path.name}.', suffix='.tmp', dir=file_path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, 'w', encoding=encoding, newline='') as handle:
                handle.write(content)
            os.replace(temp_path, file_path)
        finally:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass


def new_raw_config() -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser()
    parser.optionxform = str
    return parser


def load_ini(path: str | Path, encoding: str = TEXT_ENCODING) -> configparser.RawConfigParser:
    ensure_file(path)
    parser = new_raw_config()
    parser.read(path, encoding=encoding)
    return parser


def save_ini(parser: configparser.RawConfigParser, path: str | Path, encoding: str = TEXT_ENCODING) -> None:
    buffer = io.StringIO()
    parser.write(buffer)
    atomic_write_text(path, buffer.getvalue(), encoding=encoding)


def ensure_sections(parser: configparser.RawConfigParser, sections: Iterable[str]) -> None:
    for section in sections:
        if not parser.has_section(section):
            parser.add_section(section)
