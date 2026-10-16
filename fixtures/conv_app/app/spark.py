from pyspark.sql import SparkSession


def get_spark():
    """The shared Spark session."""
    return SparkSession.builder.appName("x").getOrCreate()
