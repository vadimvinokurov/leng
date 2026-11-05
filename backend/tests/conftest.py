import pytest

from app import db


@pytest.fixture
def conn(tmp_path):
    """A WAL-enabled connection to a fresh empty SQLite database."""
    connection = db.connect(str(tmp_path / "test.db"))
    yield connection
    connection.close()
