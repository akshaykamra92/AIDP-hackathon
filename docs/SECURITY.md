# Location-scoped access

The portable app uses application users and current location grants stored in Oracle. A certified metric must expose an approved text location column through its `WH_` adapter view. The query compiler applies that column to the authenticated user's active grants on **every answer**. A requested filter can narrow a user's scope, but it cannot expand it.

This security schema is independent of the synthetic `WH_LOCATION` demo table. An organization can map any source location structure to stable uppercase text keys in its adapter views.

## Install without changing source data

After [installing the portable catalog](ADOPTION.md), run:

```bash
python scripts/install_security.py --dry-run
python scripts/install_security.py --apply
python scripts/install_security.py --verify-only
```

The installer creates four `WH_` tables and the `WH_SECURITY_ADMIN` package. It adds no users, grants, source rows, or credentials. Reapplying it preserves existing users and access history; the package definition is refreshed. It never runs the synthetic reset script.

| Object | Purpose |
| --- | --- |
| `WH_APP_USER` | User ID, display name, `ADMIN`/`READER` role, active/revoked status, and a unique SHA-256 verifier of a random bearer token. Plaintext tokens are never stored. |
| `WH_SEC_LOCATION` | Canonical uppercase location keys and active/inactive status. These keys can map to any source schema. |
| `WH_USER_LOCATION` | Current user-to-location grants with active/revoked status and grant/revocation timestamps. |
| `WH_ACCESS_AUDIT` | Access administration actions, actor, target user, and location key. It has no token or hash column. |

## Set up users and locations

Use the administrator CLI from a trusted local environment. Token-producing commands write each newly generated token to a **local, git-ignored file with mode 0600**. Keep those files in a secret manager for a deployed service and never paste token contents into Git, chat, logs, or documentation. The command output reports only the file path and action status.

```bash
python scripts/userctl.py bootstrap \
  --user-id admin --display-name "Access Admin" \
  --token-out work/tokens/admin.token

python scripts/userctl.py register-location \
  --location-key ORLANDO --display-name "Orlando" \
  --admin-token-file work/tokens/admin.token
python scripts/userctl.py register-location \
  --location-key TAMPA --display-name "Tampa" \
  --admin-token-file work/tokens/admin.token

python scripts/userctl.py create-user \
  --user-id analyst1 --display-name "Analyst One" --role READER \
  --admin-token-file work/tokens/admin.token \
  --token-out work/tokens/analyst1.token
python scripts/userctl.py grant-location \
  --user-id analyst1 --location-key ORLANDO \
  --admin-token-file work/tokens/admin.token
```

The first `bootstrap` call is allowed only while `WH_APP_USER` is empty. For later CLI mutations, the CLI validates the administrator token file and passes that active `ADMIN` user's ID as the package actor. Package calls also require a privileged database connection. The actor ID is an audit identity, not a replacement for the database privilege check. There are no public web routes for adding users, grants, or metrics.

To remove access or rotate a credential:

```bash
python scripts/userctl.py revoke-location \
  --user-id analyst1 --location-key ORLANDO \
  --admin-token-file work/tokens/admin.token
python scripts/userctl.py rotate-token \
  --user-id analyst1 --admin-token-file work/tokens/admin.token \
  --token-out work/tokens/analyst1-new.token
python scripts/userctl.py revoke-user --user-id analyst1 \
  --admin-token-file work/tokens/admin.token
```

To disable a location and its grants:

```bash
python scripts/userctl.py set-location-status \
  --location-key ORLANDO --status INACTIVE \
  --admin-token-file work/tokens/admin.token
```

Enabling the location later does **not** restore those grants; an administrator must grant them again. Revoking a user also revokes that user's location grants. The package prevents revoking the last active administrator. A token rotation invalidates the previous token as soon as the transaction commits.

## Map a different source schema to location keys

Each new metric manifest declares `location_column`, for example `LOCATION_KEY`. Its `WH_` adapter view must emit that column as text, with values matching registered `WH_SEC_LOCATION.LOCATION_KEY` values. Every source row must belong to **one** location before the metric aggregate runs; a pre-aggregated row that combines several locations cannot be scoped safely. For historical events, emit the location at the time of the event rather than a current assignment. If the source stores numeric IDs, cast them in the view:

```sql
CREATE OR REPLACE VIEW WH_ORDER_METRIC_SOURCE AS
SELECT o.FILLED_AT AS METRIC_DATE,
       TO_CHAR(o.LOCATION_ID) AS LOCATION_KEY,
       o.AMOUNT AS AMOUNT,
       o.CHANNEL AS CHANNEL
FROM YOUR_APPROVED_ORDER_VIEW o
WHERE o.FILLED_AT IS NOT NULL;
```

The `YOUR_APPROVED_ORDER_VIEW` name above is illustrative. Replace it with a view in your own schema and keep its data permissions and sensitive-column exclusions under your normal governance process. For text source keys, use an uppercase, trimmed expression in the adapter. Do not insert a source table name or SQL fragment into the manifest. Then include `"location_column": "LOCATION_KEY"` in the metric manifest and follow the [draft → review → certify lifecycle](ADOPTION.md). A certified metric with no valid location column cannot answer a scoped request.

## How one answer is limited

Send the bearer token to the API over HTTPS in a deployment. The app hashes it and looks up an active `WH_APP_USER`; only the SHA-256 verifier is compared with the stored value. The answer SQL contains a bound user ID and an `EXISTS` predicate over `WH_USER_LOCATION`, `WH_SEC_LOCATION`, and `WH_APP_USER`. All three rows must be active, and the grant's location key must equal the metric source row's location key. The app rejects requests with no active grant and does not turn an unsupported question into unscoped SQL.

The grant check is evaluated when each SQL statement runs, so a committed revocation affects subsequent queries. Oracle's normal statement-level read consistency means an already-running statement may finish using the snapshot it started with. `WH_REGISTERED_ANSWER_LOG.ASKED_BY` and its canonical query JSON identify the user behind each certified answer; `WH_ACCESS_AUDIT` records changes to access.

## Deployment boundary

This version still connects the web app as the catalog owner in one Oracle schema. The package checks an active `ADMIN` actor and the web app exposes no admin mutation routes, but the schema owner itself has direct table privileges. For production with regulated data, use separate Oracle runtime and administration accounts, grant runtime only the reads and answer-log insert it needs, keep `WH_SECURITY_ADMIN` executable only by the administrator account, and adapt metadata lookups and object qualification for those accounts. Put the API behind HTTPS and enterprise identity management, and consider Oracle row-level security on source objects for defense in depth. Test those account grants and policies before treating the application-layer scope as a database-enforced policy.
