import json
import re
import urllib.parse
from typing import Any
from typing import Dict
from typing import Iterator
from typing import Optional
from typing import Tuple
from unittest import mock

import apsw
import pytest
from sqlalchemy import inspect, select, Table, MetaData, func, create_engine
from sqlalchemy.engine.url import make_url

from shillelagh.adapters.base import Adapter
from shillelagh.backends.apsw.db import connect
from shillelagh.backends.apsw.db import Connection
from shillelagh.backends.apsw.db import Cursor
from shillelagh.backends.apsw.dialect import APSWDialect
from shillelagh.backends.apsw.dialect import APSWGSheetsDialect
from shillelagh.exceptions import NotSupportedError
from shillelagh.exceptions import ProgrammingError
from shillelagh.fields import Float
from shillelagh.fields import Integer
from shillelagh.fields import Order
from shillelagh.fields import String
from shillelagh.filters import Equal
from shillelagh.filters import Filter
from shillelagh.filters import Range
from shillelagh.types import NUMBER
from shillelagh.types import Row
from shillelagh.types import STRING

from ...fakes import FakeAdapter
from ...fakes import FakeEntryPoint


class FakeEntryPoint:
    def __init__(self, name: str, adapter: Adapter):
        self.name = name
        self.adapter = adapter

    def load(self) -> Adapter:
        return self.adapter


class FakeAdapter(Adapter):

    age = Float(filters=[Range], order=Order.NONE, exact=True)
    name = String(filters=[Equal], order=Order.ASCENDING, exact=True)
    pets = Integer()

    @staticmethod
    def supports(uri: str) -> bool:
        parsed = urllib.parse.urlparse(uri)
        return parsed.scheme == "dummy"

    @staticmethod
    def parse_uri(uri: str) -> Tuple[()]:
        return ()

    def __init__(self):
        self.data = [
            {"rowid": 0, "name": "Alice", "age": 20, "pets": 0},
            {"rowid": 1, "name": "Bob", "age": 23, "pets": 3},
        ]

    def get_data(self, bounds: Dict[str, Filter]) -> Iterator[Dict[str, Any]]:
        data = self.data[:]

        for column in ["name", "age"]:
            if column in bounds:
                data = [row for row in data if bounds[column].check(row[column])]

        yield from iter(data)

    def insert_row(self, row: Row) -> int:
        row_id: Optional[int] = row["rowid"]
        if row_id is None:
            row["rowid"] = row_id = max(row["rowid"] for row in self.data) + 1

        self.data.append(row)

        return row_id

    def delete_row(self, row_id: int) -> None:
        self.data = [row for row in self.data if row["rowid"] != row_id]


def test_create_engine(mocker):
    entry_points = [FakeEntryPoint("dummy", FakeAdapter)]
    mocker.patch(
        "shillelagh.backends.apsw.db.iter_entry_points",
        return_value=entry_points,
    )

    engine = create_engine("shillelagh://")
    inspector = inspect(engine)

    table = Table("dummy://", MetaData(bind=engine), autoload=True)
    query = select([func.sum(table.columns.pets)], from_obj=table)
    assert query.scalar() == 3


def test_create_engine_no_adapters(mocker):
    engine = create_engine("shillelagh://")
    inspector = inspect(engine)

    with pytest.raises(ProgrammingError) as excinfo:
        Table("dummy://", MetaData(bind=engine), autoload=True)
    assert str(excinfo.value) == "Unsupported table: dummy://"


def test_dialect_ping():
    mock_dbapi_connection = mock.MagicMock()
    dialect = APSWDialect()
    assert dialect.do_ping(mock_dbapi_connection) is True


def test_gsheets_dialect(fs):
    dialect = APSWGSheetsDialect()
    assert dialect.create_connect_args(make_url("gsheets://")) == (
        (
            ":memory:",
            ["gsheetsapi"],
            {},
        ),
        {},
    )

    dialect = APSWGSheetsDialect(
        service_account_info={"secret": "XXX"}, subject="user@example.com"
    )
    assert dialect.create_connect_args(make_url("gsheets://")) == (
        (
            ":memory:",
            ["gsheetsapi"],
            {"gsheetsapi": ({"secret": "XXX"}, "user@example.com")},
        ),
        {},
    )

    with open("credentials.json", "w") as fp:
        json.dump({"secret": "YYY"}, fp)

    dialect = APSWGSheetsDialect(
        service_account_file="credentials.json", subject="user@example.com"
    )
    assert dialect.create_connect_args(make_url("gsheets://")) == (
        (
            ":memory:",
            ["gsheetsapi"],
            {"gsheetsapi": ({"secret": "YYY"}, "user@example.com")},
        ),
        {},
    )