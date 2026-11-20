"""DAG Airflow (service transverse T3) : orchestration quotidienne du pipeline UNIFY.

P1/P2 (Debezium -> Kafka) tournent en continu. Le DAG enchaîne chaque nuit les étapes
P3 à P8 en appelant les fonctions du package ``unify`` (une tâche = un processus du DFD),
ce qui permet de relancer une étape isolément en cas d'échec.
"""

from datetime import date, datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

DEFAULTS = {"owner": "data-platform", "retries": 2, "retry_delay": timedelta(minutes=10)}


def _ctx():
    from unify.config import get_settings
    from unify.lake import Lake
    from unify.spark_jobs.session import get_spark

    settings = get_settings()
    return settings, Lake(settings.lake_root), get_spark("unify-airflow")


def p3_p4_standardisation(ds: str, **_):
    from unify.spark_jobs import bronze_to_silver

    settings, lake, spark = _ctx()
    return bronze_to_silver.run(spark, lake, settings, date.fromisoformat(ds))


def p5_mdm(**_):
    from unify.spark_jobs import mdm_job

    _, lake, spark = _ctx()
    return mdm_job.run(spark, lake)


def p6_features(**_):
    from unify.spark_jobs import features_job

    settings, lake, spark = _ctx()
    return features_job.run(spark, lake, settings.snapshot_date, settings.horizon_days)


def p7_p8_entrainement_scoring(**_):
    from pathlib import Path

    from unify.ml.predict import Predictor
    from unify.ml.train import save, train
    from unify.pipeline import score_portfolio

    settings, lake, spark = _ctx()
    gold = spark.read.parquet(lake.gold("ft_client_resiliation")).toPandas()
    out = Path(settings.artifacts_dir)
    save(train(gold), out)
    score_portfolio(gold, Predictor.load(out)).to_parquet(out / "scores.parquet", index=False)


with DAG(
    dag_id="unify_pipeline_quotidien",
    start_date=datetime(2027, 3, 1),
    schedule="0 2 * * *",
    catchup=False,
    default_args=DEFAULTS,
    tags=["unify", "mvp", "resiliation"],
) as dag:
    t_silver = PythonOperator(task_id="p3_p4_standardisation_qualite",
                              python_callable=p3_p4_standardisation)
    t_mdm = PythonOperator(task_id="p5_mdm", python_callable=p5_mdm)
    t_gold = PythonOperator(task_id="p6_features", python_callable=p6_features)
    t_ml = PythonOperator(task_id="p7_p8_entrainement_scoring",
                          python_callable=p7_p8_entrainement_scoring)
    t_silver >> t_mdm >> t_gold >> t_ml
