from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()


def export():
    return spark.sql("SELECT day, cust FROM mart.sales_report WHERE dbl > 1")
