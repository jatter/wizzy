"""Durable verdict derivation; never calls a model or settles grading quota."""
import argparse

from sqlalchemy import create_engine, text

from wizzy.config import Settings
from wizzy.execution import check_worker_role


class LearningConsumer:
    def __init__(self, engine):
        check_worker_role(engine)
        with engine.connect() as db:
            allowed = db.execute(text("SELECT has_function_privilege(current_user,"
                                      "'public.consume_question_derivation()','EXECUTE')")).scalar_one()
            if not allowed:
                raise RuntimeError('Use the restricted worker role with derivation capability')
        self.engine = engine

    def tick(self):
        with self.engine.begin() as db:
            return db.execute(text('SELECT public.consume_question_derivation()')).scalar_one()


def main():
    parser = argparse.ArgumentParser(description='Consume durable verdict events until idle')
    parser.add_argument('--once', action='store_true', help='Consume at most one event')
    args = parser.parse_args()
    engine = create_engine(Settings().worker_db_url, hide_parameters=True)
    try:
        consumer = LearningConsumer(engine)
        while True:
            status = consumer.tick()
            print(status)  # Operational state only; no student content or identifiers.
            if args.once or status == 'idle':
                break
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
