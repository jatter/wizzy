"""Student-scoped timetable versions and append-only single-instance overrides."""
import os

from alembic import op
from sqlalchemy import text

revision = '0014_timetable_core'
down_revision = '0013_review_workbench'


def upgrade():
    bind = op.get_bind()
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Timetable migration requires an offline RLS-bypassing owner')
    safe = bind.execute(text('''SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
        rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication) FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none()
    if not safe:
        raise RuntimeError('A restricted API role is required')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('''
    CREATE TABLE public.terms (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        deletion_epoch bigint NOT NULL CHECK(deletion_epoch>=0),
        name text NOT NULL CHECK(length(name) BETWEEN 1 AND 80 AND name ~ '[^[:space:]]'),
        start_date date NOT NULL, end_date date NOT NULL,
        week_starts_on smallint NOT NULL CHECK(week_starts_on BETWEEN 1 AND 7),
        timezone text NOT NULL DEFAULT 'Asia/Shanghai' CHECK(timezone='Asia/Shanghai'),
        revision integer NOT NULL DEFAULT 0 CHECK(revision BETWEEN 0 AND 2147483646),
        template_revision integer NOT NULL DEFAULT 0 CHECK(template_revision BETWEEN 0 AND revision),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK(end_date>=start_date AND end_date-start_date<370),
        UNIQUE(tenant_id,student_id,deletion_epoch,id),
        FOREIGN KEY(tenant_id,student_id) REFERENCES public.students(tenant_id,id)
    );
    CREATE INDEX terms_student_history ON public.terms(student_id,created_at,id);
    CREATE TABLE public.timetable_versions (
        tenant_id uuid NOT NULL, student_id uuid NOT NULL, deletion_epoch bigint NOT NULL,
        term_id uuid NOT NULL, revision integer NOT NULL CHECK(revision>0),
        kind text NOT NULL CHECK(kind IN ('template','override')),
        actor_id uuid NOT NULL REFERENCES public.users(id), confirm_conflicts boolean NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(term_id,revision), UNIQUE(tenant_id,student_id,deletion_epoch,term_id,revision),
        FOREIGN KEY(tenant_id,student_id,deletion_epoch,term_id)
            REFERENCES public.terms(tenant_id,student_id,deletion_epoch,id)
    );
    CREATE TABLE public.timetable_slots (
        tenant_id uuid NOT NULL, student_id uuid NOT NULL, deletion_epoch bigint NOT NULL,
        term_id uuid NOT NULL, template_revision integer NOT NULL, id uuid NOT NULL,
        position smallint NOT NULL CHECK(position BETWEEN 0 AND 99),
        weekday smallint NOT NULL CHECK(weekday BETWEEN 1 AND 7),
        period smallint NOT NULL CHECK(period BETWEEN 1 AND 20),
        subject text NOT NULL CHECK(length(subject) BETWEEN 1 AND 80 AND subject ~ '[^[:space:]]'),
        weeks text NOT NULL CHECK(weeks IN ('all','odd','even')),
        start_time time, end_time time, room text CHECK(length(room)<=100),
        CHECK((start_time IS NULL AND end_time IS NULL) OR
            (start_time IS NOT NULL AND end_time IS NOT NULL AND start_time<end_time
            AND extract(second FROM start_time)=0 AND extract(second FROM end_time)=0)),
        PRIMARY KEY(term_id,template_revision,id),
        UNIQUE(tenant_id,student_id,deletion_epoch,term_id,template_revision,id),
        FOREIGN KEY(tenant_id,student_id,deletion_epoch,term_id,template_revision)
            REFERENCES public.timetable_versions(tenant_id,student_id,deletion_epoch,term_id,revision)
    );
    CREATE TABLE public.lesson_overrides (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL, deletion_epoch bigint NOT NULL,
        term_id uuid NOT NULL, revision integer NOT NULL, template_revision integer NOT NULL,
        slot_id uuid NOT NULL, source_date date NOT NULL,
        action text NOT NULL CHECK(action IN ('cancel','move','restore')),
        target_date date, period smallint CHECK(period BETWEEN 1 AND 20),
        subject text CHECK(length(subject) BETWEEN 1 AND 80 AND subject ~ '[^[:space:]]'),
        start_time time, end_time time, room text CHECK(length(room)<=100),
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 1000 AND reason ~ '[^[:space:]]'),
        CHECK((action='move' AND target_date IS NOT NULL AND period IS NOT NULL AND subject IS NOT NULL)
            OR (action IN ('cancel','restore') AND target_date IS NULL AND period IS NULL AND subject IS NULL
                AND start_time IS NULL AND end_time IS NULL AND room IS NULL)),
        CHECK((start_time IS NULL AND end_time IS NULL) OR
            (start_time IS NOT NULL AND end_time IS NOT NULL AND start_time<end_time
            AND extract(second FROM start_time)=0 AND extract(second FROM end_time)=0)),
        UNIQUE(term_id,revision),
        FOREIGN KEY(tenant_id,student_id,deletion_epoch,term_id,revision)
            REFERENCES public.timetable_versions(tenant_id,student_id,deletion_epoch,term_id,revision),
        FOREIGN KEY(tenant_id,student_id,deletion_epoch,term_id,template_revision,slot_id)
            REFERENCES public.timetable_slots(tenant_id,student_id,deletion_epoch,term_id,template_revision,id)
    );
    CREATE INDEX lesson_override_instances ON public.lesson_overrides(term_id,slot_id,source_date,revision DESC);

    CREATE FUNCTION public.timetable_access(tenant uuid,student uuid,epoch bigint,writing boolean) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT COALESCE(tenant=NULLIF(current_setting('app.tenant_id',true),'')::uuid AND EXISTS(
            SELECT 1 FROM public.students s JOIN public.tenants t ON t.id=s.tenant_id
            WHERE s.id=student AND s.tenant_id=tenant AND s.deletion_epoch=epoch
                AND s.status='active' AND s.consent_active AND t.status='active' AND (
                    (NOT writing AND NULLIF(current_setting('app.student_id',true),'')::uuid=student
                        AND NULLIF(current_setting('app.user_id',true),'') IS NULL)
                    OR (NULLIF(current_setting('app.student_id',true),'') IS NULL AND EXISTS(
                        SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
                        WHERE m.tenant_id=tenant AND m.user_id=NULLIF(current_setting('app.user_id',true),'')::uuid
                            AND m.status='active' AND u.status='active' AND (m.role='owner' OR EXISTS(
                                SELECT 1 FROM public.student_access a WHERE a.tenant_id=tenant
                                    AND a.student_id=student AND a.user_id=m.user_id)))))),false)
    $$;
    REVOKE ALL ON FUNCTION public.timetable_access(uuid,uuid,bigint,boolean) FROM PUBLIC;
    ''')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.timetable_access(uuid,uuid,bigint,boolean) TO {role}')
    for table in ('terms', 'timetable_versions', 'timetable_slots', 'lesson_overrides'):
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC')
        op.execute(f'''CREATE POLICY timetable_read ON public.{table} FOR SELECT TO {role}
            USING(public.timetable_access(tenant_id,student_id,deletion_epoch,false))''')
        actor_check = " AND actor_id=NULLIF(current_setting('app.user_id',true),'')::uuid" if table == 'timetable_versions' else ''
        op.execute(f'''CREATE POLICY timetable_insert ON public.{table} FOR INSERT TO {role}
            WITH CHECK(public.timetable_access(tenant_id,student_id,deletion_epoch,true){actor_check})''')
        op.execute(f'GRANT SELECT,INSERT ON public.{table} TO {role}')
    op.execute(f'''CREATE POLICY timetable_update ON public.terms FOR UPDATE TO {role}
        USING(public.timetable_access(tenant_id,student_id,deletion_epoch,true))
        WITH CHECK(public.timetable_access(tenant_id,student_id,deletion_epoch,true));
        GRANT UPDATE(revision,template_revision) ON public.terms TO {role};''')


def downgrade():
    op.execute('''DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.terms) OR EXISTS(SELECT 1 FROM public.timetable_versions)
            OR EXISTS(SELECT 1 FROM public.timetable_slots) OR EXISTS(SELECT 1 FROM public.lesson_overrides) THEN
            RAISE EXCEPTION 'Cannot discard timetable or lesson override history';
        END IF;
    END $$;
    DROP TABLE public.lesson_overrides;
    DROP TABLE public.timetable_slots;
    DROP TABLE public.timetable_versions;
    DROP TABLE public.terms;
    DROP FUNCTION public.timetable_access(uuid,uuid,bigint,boolean);
    ''')
