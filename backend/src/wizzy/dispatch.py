"""Outbox relay only. Publishing is not task execution or grading success."""
import argparse
import sys
import time

from sqlalchemy import create_engine, text

from wizzy.broker import create_broker, publish_event
from wizzy.config import Settings


class Dispatcher:
    def __init__(self, engine, publisher):
        self.engine, self.publisher = engine, publisher
        with engine.connect() as db:
            unsafe = db.execute(text("""SELECT rolsuper OR rolbypassrls OR rolcreatedb OR
                rolcreaterole OR rolinherit OR rolreplication OR
                EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) OR
                EXISTS(SELECT 1 FROM pg_class WHERE relowner=r.oid) OR
                EXISTS(SELECT 1 FROM pg_namespace WHERE nspowner=r.oid) OR
                EXISTS(SELECT 1 FROM pg_database WHERE datdba=r.oid)
                FROM pg_roles r WHERE rolname=current_user""")).scalar_one()
            grants = db.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
                AND c.relkind IN ('r','p','v','m','f') AND (
                    has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES') OR
                    has_table_privilege(current_user,c.oid,'DELETE,TRUNCATE,TRIGGER')))""")).scalar_one()
            if unsafe or grants:
                raise RuntimeError("Use a separate restricted dispatcher role without table privileges")

    def claim(self):
        # Commit before touching Redis. Claim one at a time so batches do not age
        # behind a slow broker; expired leases remain reclaimable after a crash.
        with self.engine.begin() as db:
            row = db.execute(text("SELECT * FROM public.claim_outbox(1)")).mappings().first()
            return dict(row) if row else None

    def finish(self, event, published):
        with self.engine.begin() as db:
            return db.execute(text("SELECT public.settle_outbox(:id,:token,:published)"),
                              {"id": event["event_id"], "token": event["lease_token"],
                               "published": published}).scalar_one()

    def tick(self):
        event = self.claim()
        if not event:
            return "idle"
        try:
            self.publisher(event)
        except Exception:
            # Never persist/log driver text: it may contain broker credentials.
            return "retry" if self.finish(event, False) else "lease_lost"
        return "published" if self.finish(event, True) else "lease_lost"


def main():
    parser = argparse.ArgumentParser(description="Relay queued job identifiers; not an AI worker")
    parser.add_argument("--once", action="store_true", help="Relay at most one event and exit")
    args = parser.parse_args()
    engine = broker = None
    try:
        settings = Settings()
        engine = create_engine(settings.dispatcher_db_url, pool_pre_ping=True, hide_parameters=True,
                               connect_args={"connect_timeout": 3})
        broker = create_broker(settings)
        dispatcher = Dispatcher(engine, lambda event: publish_event(broker, event))
        while True:
            status = dispatcher.tick()
            if args.once or status != "idle":
                print(f"dispatch={status}", flush=True)
            if args.once:
                return 1 if status in ("retry", "lease_lost") else 0
            if status != "published":
                time.sleep(2)
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("Dispatcher failed. Check local configuration and database migration; credentials were not logged.", file=sys.stderr)
        return 1
    finally:
        if broker:
            broker.close()
        if engine:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
