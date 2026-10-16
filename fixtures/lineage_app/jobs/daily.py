from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()


def build_daily():
    """Aggregate raw orders into mart.sales_daily."""
    df = spark.read.table("raw.orders")
    out = df.groupBy("day").count()
    out.write.mode("overwrite").saveAsTable("mart.sales_daily")
