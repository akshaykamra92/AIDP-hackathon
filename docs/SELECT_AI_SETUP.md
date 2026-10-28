# Enable Oracle Select AI exploration

Ask the Warehouse can use Oracle Select AI to **draft SQL for inspection** while its certified answers continue to use registered metrics and fixed, bound SQL. The integration calls only `DBMS_CLOUD_AI.GENERATE(..., action => 'showsql')`; it does not execute generated SQL or turn it into a certified answer. It is off unless `SELECT_AI_ENABLED=true` and a scoped profile exists.

Oracle's [AI profile guide](https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/manage-ai-profiles.html) lists the required package privilege, AI-provider credential, and provider access. The [package reference](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/dbms-cloud-ai-package.html) documents `CREATE_PROFILE` and the stateless `GENERATE` function. Select AI availability and provider prerequisites depend on the Oracle deployment. The app will fail closed when the package or profile is unavailable.

## 1. Prepare a safe source view

Use an **owned `WH_` adapter view** created for exploration. Expose only fields whose names and comments may be sent to your selected model provider. Oracle notes that object names, column names, and comments from an AI profile can be sent to the provider; review these before profile creation. Prompts also go to the provider, so use synthetic questions in this repository. The profile script accepts explicit view names only. It does not expose an entire schema, and it sets `enforce_object_list=true` as described in [Oracle's restricted profile example](https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/generate-and-improve-sql-queries.html).

For the disposable synthetic walkthrough, `WH_EXAMPLE_FILLED_REQUISITIONS` is a possible view after the [demo adapter](../examples/filled_requisitions.view.sql) has been installed. For your own tables, follow the [adoption guide](ADOPTION.md) to create a reviewed `WH_` view and register a metric. Add more views by repeating `--view` when creating the profile.

The profile's object list controls the metadata and objects used for generation. **It is not row-level authorization.** Location access for certified answers must be enforced by the application's user policy and database query path. This Select AI path only displays generated text, so a generated filter is never treated as a location security check.

## 2. Enable database privileges and provider access

Ask your Oracle administrator to grant the app schema `EXECUTE` on `DBMS_CLOUD_AI` and, if this schema will create a provider credential, `EXECUTE` on `DBMS_CLOUD`. Configure a provider endpoint ACL when the provider requires it. Oracle's [prerequisites](https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/manage-ai-profiles.html) say OCI Generative AI does not require the external-provider network ACL; other providers may. Use a supported model and region from the [provider examples](https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/use-select-ai-ai-providers.html).

Keep the Oracle connection in the local, git-ignored `.env` as described in [ADOPTION.md](ADOPTION.md). Leave `SELECT_AI_ENABLED=false` while preparing the profile. The setup program never prints a provider key, accepts no secret on the command line, and uses Oracle bind variables for the provider key and profile attributes.

### OpenAI provider example

Place the provider API key in the local `.env` as `SELECT_AI_API_KEY=...` or inject it from your secret manager. Do not put it in SQL files, shell arguments, repository files, or app logs. The `--create-openai-credential` flag creates the Oracle credential **only if absent** and reads this environment value during `--apply`. The `--model` value below is a placeholder; choose a currently supported model from Oracle's [OpenAI provider documentation](https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/use-select-ai-ai-providers.html).

```bash
python scripts/select_ai_setup.py \
  --provider openai \
  --model YOUR_SUPPORTED_MODEL \
  --credential-name WH_SELECT_AI_CRED \
  --profile WH_SELECT_AI_EXPLORE \
  --view WH_EXAMPLE_FILLED_REQUISITIONS \
  --create-openai-credential
```

That command is a **dry run** and opens no Oracle connection. After reviewing the named view and provider settings, repeat it with `--apply` to create the credential and profile. The Oracle credential stores the provider key in the database; keep `SELECT_AI_API_KEY` out of the app runtime environment after setup.

### OCI Generative AI example

Create an Oracle `WH_` credential for OCI Generative AI using your organization's approved signing-key or secret-management process, then run:

```bash
python scripts/select_ai_setup.py \
  --provider oci \
  --model YOUR_SUPPORTED_OCI_MODEL \
  --oci-compartment-id YOUR_COMPARTMENT_OCID \
  --credential-name WH_SELECT_AI_CRED \
  --profile WH_SELECT_AI_EXPLORE \
  --view WH_EXAMPLE_FILLED_REQUISITIONS
```

Repeat with `--apply` after the dry run. This path uses the **existing Oracle credential** and does not read an OCI private key or create a new credential. Oracle's [OCI provider example](https://docs.oracle.com/en/database/oracle/oracle-database/26/selai/use-select-ai-ai-providers.html) shows the supported credential and profile attributes.

The setup command refuses an existing profile; it never drops or overwrites one. If the profile already exists, check it first and make changes through your DBA's reviewed profile process. `--apply` does not install tables, load data, or reset the demo schema.

## 3. Verify, then opt in

Set `SELECT_AI_PROFILE=WH_SELECT_AI_EXPLORE` in the local `.env`, then run:

```bash
python scripts/select_aictl.py check
```

The check requires an **enabled profile owned by the connected schema**, an explicit list of existing owned `WH_` views, `enforce_object_list=true`, `object_list_mode=all`, and conversation history off. It does not call an AI provider. The profile metadata can also be inspected in Oracle's `USER_CLOUD_AI_PROFILES` and `USER_CLOUD_AI_PROFILE_ATTRIBUTES` views; see the [view reference](https://docs.oracle.com/en/cloud/paas/autonomous-database/serverless/adbsb/dbms-cloud-ai-views.html).

Once the check passes, set `SELECT_AI_ENABLED=true` in the local `.env` or in the app's deployment environment, then restart the app. Test from the command line with a **synthetic** question:

```bash
python scripts/select_aictl.py ask
```

Enter `How many filled requisitions were in Florida in Q3 2026?` at the prompt. The output is labeled `EXPLORATORY`, `executed: false`, and `certified: false`. The exact generated SQL varies by provider, profile, and schema. The public recorded demo does not depend on this provider setup.

In an Oracle worksheet, an administrator can also test the profile in the same session with `DBMS_CLOUD_AI.SET_PROFILE('WH_SELECT_AI_EXPLORE')` followed by `SELECT AI SHOWSQL ...`. The app uses `GENERATE` with the profile name on every call because its database connections are pooled. Oracle documents that a `SELECT AI` command prefix in the prompt can override the function's action; the app rejects such input before calling the package.

## From an AI draft to a certified metric

An analyst may use the draft SQL to discover a useful calculation. A steward then writes or revises a `WH_` adapter view, saves a typed manifest, previews it, and sends it through `WH_METRIC_GOVERNANCE` review and certification. **Do not copy model-written SQL into a certified answer route.** The registered compiler uses only supported `COUNT`, `SUM`, `AVG`, and `RATIO_SUM` shapes with bind variables. A multi-table ratio can be defined in an approved adapter view without letting the prompt supply its joins or formula. See [ADOPTION.md](ADOPTION.md) for the complete versioned workflow and [SELECT_AI_COMPARISON.md](SELECT_AI_COMPARISON.md) for the side-by-side behavior.

For a real multi-user deployment, gate exploratory access by role, review the metadata sent to the provider, and keep all generated SQL display-only. Neither a prompt instruction nor `enforce_object_list` can replace the location policy on database reads.
