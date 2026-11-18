"""Offline first-administrator enrollment; never migrates, resets or writes local secrets."""
import argparse
import getpass
import os
import sys
from uuid import uuid4

from pydantic import Field, ValidationError, field_validator
from sqlalchemy import create_engine, text

from wizzy.schemas import Input
from wizzy.security import passwords


class BootstrapError(RuntimeError):
    pass


class Enrollment(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128, repr=False)

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        if '@' not in value or any(char.isspace() for char in value):
            raise ValueError('Invalid email')
        return value


def initialize(url, *, email, password):
    try:
        enrollment = Enrollment(email=email, password=password)
    except ValidationError as exc:
        raise BootstrapError('Invalid platform enrollment configuration') from exc
    engine = create_engine(url, hide_parameters=True)
    identifier = uuid4()
    try:
        with engine.begin() as db:
            if not db.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
                raise BootstrapError('An offline RLS-bypassing owner is required')
            # Serialize first enrollment, including attempts using different email addresses.
            db.execute(text('LOCK TABLE public.platform_accounts IN EXCLUSIVE MODE'))
            if db.execute(text('SELECT EXISTS(SELECT 1 FROM public.platform_accounts)')).scalar_one():
                raise BootstrapError('Platform already initialized; existing accounts are never reset')
            db.execute(text('''INSERT INTO public.platform_accounts
                (id,email,password_hash,encrypted_totp,credential_version,role,last_totp_step)
                VALUES(:id,:email,:password,'unused',:version,'security_admin',-1)'''),
                {'id': identifier, 'email': enrollment.email,
                 'password': passwords.hash(enrollment.password), 'version': uuid4()})
            db.execute(text('''INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(:id,:actor,'bootstrap',:request)'''),
                {'id': uuid4(), 'actor': identifier, 'request': f'bootstrap_{uuid4().hex}'})
    finally:
        engine.dispose()
    return identifier


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline first platform security administrator enrollment')
    parser.add_argument('--email', required=True)
    args = parser.parse_args(argv)
    try:
        url = os.getenv('WIZZY_MIGRATION_URL')
        if not url:
            raise BootstrapError('Explicit migration URL is required')
        password = getpass.getpass('Administrator password (12–128 characters): ')
        confirmation = getpass.getpass('Confirm password: ')
        if password != confirmation:
            raise BootstrapError('Passwords do not match')
        identifier = initialize(url, email=args.email, password=password)
    except Exception:
        # Never echo driver exceptions, enrollment material, passwords or connection strings.
        print('Platform initialization failed. Check offline permissions, configuration, enrollment and existing accounts.', file=sys.stderr)
        return 1
    print(f'Platform security administrator created: {identifier}. Sign in with this email and password.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
