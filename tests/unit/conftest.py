"""
This conftest.py contains handy components that prepare SparkSession and other relevant objects.
"""

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import mlflow
import pytest
from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession
from discoverx.dx import DX, Scanner, ScanResult
from discoverx.scanner import DeltaTable


@dataclass
class FileInfoFixture:
    """
    This class mocks the DBUtils FileInfo object
    """

    path: str
    name: str
    size: int
    modificationTime: int


class DBUtilsFixture:
    """
    This class is used for mocking the behaviour of DBUtils inside tests.
    """

    def __init__(self):
        self.fs = self

    def cp(self, src: str, dest: str, recurse: bool = False):
        copy_func = shutil.copytree if recurse else shutil.copy
        copy_func(src, dest)

    def ls(self, path: str):
        _paths = Path(path).glob("*")
        _objects = [
            FileInfoFixture(str(p.absolute()), p.name, p.stat().st_size, int(p.stat().st_mtime)) for p in _paths
        ]
        return _objects

    def mkdirs(self, path: str):
        Path(path).mkdir(parents=True, exist_ok=True)

    def mv(self, src: str, dest: str, recurse: bool = False):
        copy_func = shutil.copytree if recurse else shutil.copy
        shutil.move(src, dest, copy_function=copy_func)

    def put(self, path: str, content: str, overwrite: bool = False):
        _f = Path(path)

        if _f.exists() and not overwrite:
            raise FileExistsError("File already exists")

        _f.write_text(content, encoding="utf-8")

    def rm(self, path: str, recurse: bool = False):
        deletion_func = shutil.rmtree if recurse else os.remove
        deletion_func(path)


@pytest.fixture(scope="session")
def spark() -> SparkSession:
    """
    This fixture provides preconfigured SparkSession with Hive and Delta support.
    After the test session, temporary warehouse directory is deleted.
    :return: SparkSession
    """
    logging.info("Configuring Spark session for testing environment")
    warehouse_dir = tempfile.TemporaryDirectory().name
    if Path(warehouse_dir).exists():
        shutil.rmtree(warehouse_dir)

    _builder = (
        SparkSession.builder.master("local[1]")
        .config("spark.sql.warehouse.dir", Path(warehouse_dir).as_uri())
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.shuffle.partitions", "1")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .enableHiveSupport()
    )
    spark: SparkSession = configure_spark_with_delta_pip(_builder).getOrCreate()
    logging.info("Spark session configured")
    yield spark
    logging.info("Shutting down Spark session")
    spark.stop()
    if Path(warehouse_dir).exists():
        shutil.rmtree(warehouse_dir)


@pytest.fixture(autouse=True, scope="module")
def sample_datasets(spark: SparkSession, request):
    """
    This fixture loads a sample dataset defined in a csv and
    creates a table registered in the metastore to be used for
    tests.

    Args:
        spark: Spark session
        request: the pytest request fixture contains information about
            the current test. Used here to get current path.

    Returns:

    """
    logging.info("Creating sample datasets")

    module_path = Path(request.module.__file__)

    warehouse_dir = tempfile.TemporaryDirectory().name
    if Path(warehouse_dir).exists():
        shutil.rmtree(warehouse_dir)

    # tb_1
    test_file_path = module_path.parent / "data/tb_1.csv"
    (
        spark.read.option("header", True)
        .schema("id integer,ip string,mac string,description string")
        .csv(str(test_file_path.resolve()))
    ).createOrReplaceTempView("view_tb_1")
    spark.sql(
        f"CREATE TABLE IF NOT EXISTS default.tb_1 USING delta LOCATION '{warehouse_dir}/tb_1' AS SELECT * FROM view_tb_1 "
    )

    # tb_2
    test_file_path = module_path.parent / "data/tb_2.csv"
    (
        spark.read.option("header", True).schema("id integer,`ip.v2` string").csv(str(test_file_path.resolve()))
    ).createOrReplaceTempView("view_tb_2")
    spark.sql(
        f"CREATE TABLE IF NOT EXISTS default.tb_2 USING delta LOCATION '{warehouse_dir}/tb_2' AS SELECT * FROM view_tb_2 "
    )

    # columns_mock
    test_file_path = module_path.parent / "data/columns_mock.csv"
    (
        spark.read.option("header", True)
        .schema(
            "table_catalog string,table_schema string,table_name string,column_name string,data_type string,partition_index int"
        )
        .csv(str(test_file_path.resolve()))
    ).createOrReplaceTempView("view_columns_mock")
    spark.sql(
        f"CREATE TABLE IF NOT EXISTS default.columns USING delta LOCATION '{warehouse_dir}/columns' AS SELECT * FROM view_columns_mock"
    )

    # column_tags
    test_file_path = module_path.parent / "data/column_tags.csv"
    (
        spark.read.option("header", True)
        .schema(
            "catalog_name string, schema_name string, table_name string, column_name string, tag_name string, tag_value string"
        )
        .csv(str(test_file_path.resolve()))
    ).createOrReplaceTempView("column_tags_temp_view")
    spark.sql(
        f"CREATE TABLE IF NOT EXISTS default.column_tags USING delta LOCATION '{warehouse_dir}/column_tags' AS SELECT * FROM column_tags_temp_view"
    )

    # table_tags
    test_file_path = module_path.parent / "data/table_tags.csv"
    (
        spark.read.option("header", True)
        .schema("catalog_name string,schema_name string,table_name string,tag_name string,tag_value string")
        .csv(str(test_file_path.resolve()))
    ).createOrReplaceTempView("table_tags_temp_view")
    spark.sql(
        f"CREATE TABLE IF NOT EXISTS default.table_tags USING delta LOCATION '{warehouse_dir}/table_tags' AS SELECT * FROM table_tags_temp_view"
    )

    # schema_tags
    test_file_path = module_path.parent / "data/schema_tags.csv"
    (
        spark.read.option("header", True)
        .schema("catalog_name string,schema_name string,tag_name string,tag_value string")
        .csv(str(test_file_path.resolve()))
    ).createOrReplaceTempView("schema_tags_temp_view")
    spark.sql(
        f"CREATE TABLE IF NOT EXISTS default.schema_tags USING delta LOCATION '{warehouse_dir}/schema_tags' AS SELECT * FROM schema_tags_temp_view"
    )

    # catalog_tags
    test_file_path = module_path.parent / "data/catalog_tags.csv"
    (
        spark.read.option("header", True)
        .schema("catalog_name string,tag_name string,tag_value string")
        .csv(str(test_file_path.resolve()))
    ).createOrReplaceTempView("catalog_tags_temp_view")
    spark.sql(
        f"CREATE TABLE IF NOT EXISTS default.catalog_tags USING delta LOCATION '{warehouse_dir}/catalog_tags' AS SELECT * FROM catalog_tags_temp_view"
    )

    logging.info("Sample datasets created")

    yield None

    logging.info("Test session finished, removing sample datasets")

    spark.sql("DROP TABLE IF EXISTS default.tb_1")
    spark.sql("DROP TABLE IF EXISTS default.tb_2")
    spark.sql("DROP TABLE IF EXISTS default.columns")
    spark.sql("DROP TABLE IF EXISTS default.column_tags")
    spark.sql("DROP TABLE IF EXISTS default.table_tags")
    spark.sql("DROP TABLE IF EXISTS default.schema_tags")
    spark.sql("DROP TABLE IF EXISTS default.catalog_tags")
    if Path(warehouse_dir).exists():
        shutil.rmtree(warehouse_dir)


@pytest.fixture(scope="session", autouse=True)
def mlflow_local():
    """
    This fixture provides local instance of mlflow with support for tracking and registry functions.
    After the test session:
    * temporary storage for tracking and registry is deleted.
    * Active run will be automatically stopped to avoid verbose errors.
    :return: None
    """
    logging.info("Configuring local MLflow instance")
    tracking_uri = tempfile.TemporaryDirectory().name
    registry_uri = f"sqlite:///{tempfile.TemporaryDirectory().name}"

    mlflow.set_tracking_uri(Path(tracking_uri).as_uri())
    mlflow.set_registry_uri(registry_uri)
    logging.info("MLflow instance configured")
    yield None

    mlflow.end_run()

    if Path(tracking_uri).exists():
        shutil.rmtree(tracking_uri)

    if Path(registry_uri).exists():
        Path(registry_uri).unlink()
    logging.info("Test session finished, unrolling the MLflow instance")


@pytest.fixture(scope="module")
def monkeymodule():
    """
    Required for monkeypatching with module scope.
    For more info see
    https://stackoverflow.com/questions/53963822/python-monkeypatch-setattr-with-pytest-fixture-at-module-scope
    """
    with pytest.MonkeyPatch.context() as mp:
        yield mp


@pytest.fixture(autouse=True, scope="module")
def mock_uc_functionality(monkeymodule):
    # apply the monkeypatch for the information_schema
    monkeymodule.setattr(DX, "INFORMATION_SCHEMA", "default")

    # mock classifier method _get_classification_table_from_delta as we don't
    # have catalogs in open source spark
    def get_or_create_classification_table_mock(self, scan_table_name: str):
        (schema, table) = scan_table_name.split(".")
        # Test fails without drop if table already exists
        self.spark.sql(f"DROP DATABASE IF EXISTS {schema} CASCADE")
        self.spark.sql(f"CREATE DATABASE IF NOT EXISTS {schema}")
        self.spark.sql(
            f"""
                CREATE TABLE IF NOT EXISTS {schema + '.' + table} (table_catalog string, table_schema string, table_name string, column_name string, class_name string, score double, effective_timestamp timestamp) USING DELTA
                """
        )
        return DeltaTable.forName(self.spark, scan_table_name)

    monkeymodule.setattr(
        ScanResult,
        "_get_or_create_result_table_from_delta",
        get_or_create_classification_table_mock,
    )
