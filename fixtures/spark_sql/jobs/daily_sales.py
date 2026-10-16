from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()


def build_daily():
    """Aggregate raw orders into the sales_daily table."""
    df = spark.read.table("raw.orders")
    out = df.groupBy("day").count()
    out.write.mode("overwrite").saveAsTable("mart.sales_daily")


def enrich():
    return spark.sql("SELECT d.day, c.name FROM mart.sales_daily d JOIN dim.customers c ON d.cid = c.id")
