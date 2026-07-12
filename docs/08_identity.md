# Identity Lite

## Purpose

Identity exists to:

- authenticate Core users;
- record who created or changed business data;
- provide normal administrative access;
- provide temporary emergency superuser access.

## User

User is an application account, not an employee profile.

Fields:

- id: UUIDv7
- email
- full_name
- password_hash
- is_active
- is_admin
- is_superuser
- created_at
- updated_at

Email is the login identifier and must be unique after normalization.

Passwords are never stored in plain text.

Password hashing algorithm: Argon2id.

## Access levels

### Regular user

Performs normal business operations.

### Administrator

Performs normal daily administration:

- manage users;
- activate and deactivate accounts;
- reset user passwords;
- manage reference data.

Administrator access may be used during normal work.

### Superuser

Reserved for emergency and maintenance operations:

- recover access when administrators are unavailable;
- perform explicitly protected maintenance commands;
- perform exceptional hard deletion;
- repair seriously inconsistent data.

Daily work under superuser privileges is prohibited.

Superuser access:

- is disabled by default;
- cannot be enabled through the public API;
- cannot be enabled through the web interface;
- can only be enabled or disabled through local CLI commands;
- requires a textual reason when enabled;
- must be audited.

## CLI commands

Create the first administrator:

`uv run python -m core identity create-admin`

Enable emergency superuser access:

`uv run python -m core identity enable-superuser <email> --reason "<reason>"`

Disable emergency superuser access:

`uv run python -m core identity disable-superuser <email>`

## Audit

Superuser activation and deactivation are stored as privilege audit events.

Each event records:

- target user;
- action;
- reason;
- timestamp;
- actor description.

For the initial CLI implementation, actor description may identify the local system user.

## Authentication

Authentication will use:

- email and password;
- Argon2id password verification;
- JWT access tokens;
- no refresh tokens in the initial implementation.

JWT and protected HTTP routes are implemented after the User and CLI foundation.

## Audit fields

Business entities will later reference User through:

- created_by_id
- updated_by_id
- deleted_by_id

## Access tokens

- Authentication uses email and password.
- JWT access tokens use HS256.
- Access token lifetime is 8 hours.
- JWT subject contains the User UUID.
- Inactive or soft-deleted users cannot log in.
- Tokens belonging to inactive or soft-deleted users are rejected.
- JWT secrets are stored only in environment configuration.
- Refresh tokens are not part of Identity Lite.