"""Interface en ligne de commande : ``unify run | generate | api``."""

from __future__ import annotations

import argparse
import json
import logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="unify", description="Pipeline UNIFY AbAssurance / AssurePlus"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="Exécute le pipeline complet (P0 -> P8)")
    run_p.add_argument("--no-clean", action="store_true", help="Conserve le data lake existant")
    run_p.add_argument(
        "--redeliver",
        nargs="*",
        default=[],
        help="Tables re-livrées deux fois (simulation at-least-once)",
    )
    run_p.add_argument(
        "--no-mllib", action="store_true", help="N'entraîne pas le modèle Spark MLlib"
    )
    sub.add_parser("generate", help="Génère uniquement les bases sources simulées")
    api_p = sub.add_parser("api", help="Démarre l'API de prédiction (FastAPI)")
    api_p.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "run":
        from unify.pipeline import run

        report = run(
            clean=not args.no_clean,
            redeliver=tuple(args.redeliver),
            with_spark_mllib=not args.no_mllib,
        )
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    elif args.command == "generate":
        from unify.config import get_settings
        from unify.pipeline import step_generate

        print(json.dumps(step_generate(get_settings()), indent=2))
    else:
        import uvicorn

        # écoute sur toutes les interfaces : l'API tourne dans un conteneur, derrière la passerelle
        uvicorn.run("unify.api.main:app", host="0.0.0.0", port=args.port)  # noqa: S104  # nosec B104


if __name__ == "__main__":
    main()
